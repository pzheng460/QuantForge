"""Pine Live Trading Engine — runs the Pine interpreter on real-time klines.

The same interpreter that produces 270=270 trade parity with TradingView in
backtest mode is used bar-by-bar on live market data.  When the interpreter
calls ``strategy.entry`` / ``strategy.close`` / ``strategy.exit``, the
:class:`OrderBridge` routes those signals to real exchange orders.

Usage
-----
::

    engine = PineLiveEngine(
        pine_source=open("my_strategy.pine").read(),
        exchange="bitget",
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        demo=True,
    )
    await engine.start()
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path

from quantforge.pine.interpreter.context import BarData, ExecutionContext
from quantforge.pine.interpreter.runtime import PineRuntime
from quantforge.pine.live.connector import (
    CcxtConnector,
    fetch_warmup_bars,
    timeframe_to_seconds,
)
from quantforge.pine.live.order_bridge import OrderBridge
from quantforge.pine.parser.parser import parse

logger = logging.getLogger(__name__)


class PineLiveEngine:
    """Runs a Pine Script strategy on live market data.

    The existing Pine interpreter is used **bar-by-bar** on real-time klines.
    Indicator state is built up during warmup with historical bars, then each
    confirmed kline is fed through the same execution path.

    Parameters
    ----------
    pine_source : str
        Raw Pine Script source code.
    exchange : str
        ccxt exchange id (e.g. ``"bitget"``).
    symbol : str
        Trading pair in ccxt format (e.g. ``"BTC/USDT:USDT"``).
    timeframe : str
        Kline interval (e.g. ``"15m"``).
    demo : bool
        If ``True`` (default), signals are logged but not submitted as real
        orders.
    warmup_bars : int
        Number of historical bars to fetch for indicator warmup.
    position_size_usdt : float
        Notional position size in USDT.
    dry_run : bool
        If ``True``, signals are logged but no orders are submitted.
        Different from ``demo`` which uses the exchange sandbox API.
    """

    def __init__(
        self,
        pine_source: str,
        exchange: str = "bitget",
        symbol: str = "BTC/USDT:USDT",
        timeframe: str = "15m",
        demo: bool = True,
        warmup_bars: int = 500,
        position_size_usdt: float = 100.0,
        dry_run: bool = False,
        strategy_name: str = "pine_strategy",
        leverage: int = 1,
    ) -> None:
        self.pine_source = pine_source
        self.exchange = exchange
        self.symbol = symbol
        self.timeframe = timeframe
        self.demo = demo
        self.dry_run = dry_run
        self.warmup_bars = warmup_bars
        self.position_size_usdt = position_size_usdt
        self.strategy_name = strategy_name
        self.leverage = leverage

        # Parse once
        self.ast = parse(pine_source)

        # Runtime (created during start)
        self._runtime: PineRuntime | None = None
        self._bridge: OrderBridge | None = None

        # State
        self._running = False
        self._bars_processed = 0
        self._warmup_complete = False
        self._last_bar_time: int = 0

    async def start(self) -> None:
        """Start the live trading loop.

        1. Honour Evolving Mode's ``trading_control.json`` (pause/reduce).
        2. Fetch warmup klines and build indicator state.
        3. Poll for new confirmed klines and feed them to the interpreter.
        """
        # ── Evolving Mode gate ──────────────────────────────────────────────
        # If this strategy is under Evolving Mode control, the autonomous bot
        # subsystem may have flipped its trading_control action. We respect:
        #   - "pause"  → refuse to start (the risk gate caught something)
        #   - "reduce" → cut position_size_usdt in half before starting
        # When Evolving Mode is OFF (default), this is a no-op.
        from quantforge import evolving

        # Sanity: warn if strategy_name doesn't match any known .pine file.
        # This catches the "ema_crossover" vs "EMA Cross" silent mismatch
        # where evolving + trading_control are keyed by a name that nobody
        # else recognises, so the gate silently never fires.
        known = evolving.known_strategy_names()
        if known and self.strategy_name not in known:
            logger.warning(
                "strategy_name '%s' does not match any .pine file in %s. "
                "Evolving Mode gates keyed by this name will not fire. "
                "Known: %s",
                self.strategy_name,
                evolving.PINE_STRATEGIES_DIR,
                ", ".join(known[:5]) + ("…" if len(known) > 5 else ""),
            )

        if evolving.is_enabled(self.strategy_name):
            from quantforge.trading_control import TradingControl
            ctrl = TradingControl().get_action(self.strategy_name)
            action = ctrl.get("action", "resume")
            reasons = ctrl.get("reasons", [])
            if action == "pause":
                logger.error(
                    "Refusing to start: trading_control says PAUSE for %s. Reasons: %s",
                    self.strategy_name, reasons,
                )
                raise RuntimeError(
                    f"Pine engine paused by Evolving Mode for '{self.strategy_name}': "
                    + ("; ".join(reasons) if reasons else "no reason given")
                )
            if action == "reduce":
                old = self.position_size_usdt
                self.position_size_usdt = old / 2
                logger.warning(
                    "Evolving Mode REDUCE — position size %.2f → %.2f for %s. Reasons: %s",
                    old, self.position_size_usdt, self.strategy_name, reasons,
                )

        logger.info(
            "Starting Pine live engine | symbol=%s exchange=%s tf=%s demo=%s",
            self.symbol,
            self.exchange,
            self.timeframe,
            self.demo,
        )

        # --- Setup ---
        # --demo uses exchange sandbox/testnet API (e.g. Bitget UTA Demo)
        # --dry-run logs signals without submitting orders
        connector = None
        if not self.dry_run:
            try:
                connector = CcxtConnector(
                    exchange_id=self.exchange,
                    symbol=self.symbol,
                    demo=self.demo,  # True = sandbox mode
                )
                mode = "DEMO (sandbox)" if self.demo else "LIVE"
                logger.info("CcxtConnector initialised — %s order submission", mode)

                # Set leverage before trading starts.
                #
                # Bitget UTA quirks:
                #   - Spot UTA has no leverage concept → skip cleanly.
                #   - Futures UTA needs the v3 set-leverage endpoint;
                #     ccxt's generic set_leverage routes through the Classic
                #     mix endpoint which UTA rejects with 40085.
                is_bitget_uta = getattr(connector, "is_bitget_uta", False)
                is_spot_uta = is_bitget_uta and ":USDT" not in self.symbol
                if self.leverage > 0 and not is_spot_uta:
                    if is_bitget_uta:
                        try:
                            bg_symbol = self.symbol.split(":")[0].replace("/", "")
                            category = (
                                "USDC-FUTURES" if ":USDC" in self.symbol
                                else "USDT-FUTURES"
                            )
                            margin_coin = "USDC" if ":USDC" in self.symbol else "USDT"
                            resp = connector._exchange.privateUtaPostV3AccountSetLeverage({
                                "symbol": bg_symbol,
                                "category": category,
                                "leverage": str(self.leverage),
                                "marginCoin": margin_coin,
                            })
                            if resp.get("code") == "00000":
                                logger.info(
                                    "Leverage set to %dx for %s (UTA %s)",
                                    self.leverage, self.symbol, category,
                                )
                            else:
                                logger.warning("UTA set leverage non-OK: %s", resp)
                        except Exception:
                            logger.exception("Failed to set leverage (UTA path)")
                    else:
                        try:
                            connector._exchange.set_leverage(
                                self.leverage, self.symbol
                            )
                            logger.info(
                                "Leverage set to %dx for %s", self.leverage, self.symbol
                            )
                        except Exception:
                            logger.exception("Failed to set leverage")

                    # Verify actual leverage from exchange. Skip on Bitget UTA
                    # — ccxt's fetch_positions routes through Classic v2 mix
                    # which UTA rejects with 40085. We just set it via the UTA
                    # endpoint above, so trust the API success response there.
                    try:
                        positions = (
                            [] if is_bitget_uta
                            else connector._exchange.fetch_positions([self.symbol])
                        )
                        actual_leverage = None
                        for p in positions:
                            lev = p.get("leverage")
                            if lev is not None:
                                actual_leverage = int(float(lev))
                                break
                        if actual_leverage is not None:
                            if actual_leverage != self.leverage:
                                logger.warning(
                                    "LEVERAGE MISMATCH: configured=%dx "
                                    "actual=%dx for %s",
                                    self.leverage,
                                    actual_leverage,
                                    self.symbol,
                                )
                            else:
                                logger.info(
                                    "Leverage verified: %dx for %s",
                                    actual_leverage,
                                    self.symbol,
                                )
                        else:
                            logger.warning(
                                "Could not verify leverage — no position "
                                "data returned for %s",
                                self.symbol,
                            )
                    except Exception:
                        logger.exception("Failed to verify leverage")
            except Exception:
                logger.exception(
                    "Failed to initialise CcxtConnector — falling back to dry-run"
                )

        # Fetch real account balance for DemoTracker initial_capital
        initial_capital = None
        if connector is not None:
            try:
                params = {}
                if self.exchange == "bitget":
                    params["uta"] = True
                balance = connector._exchange.fetch_balance(params)
                usdt_total = float(balance.get("USDT", {}).get("total", 0))
                if usdt_total > 0:
                    initial_capital = usdt_total
                    logger.info("Fetched account balance: %.2f USDT", usdt_total)
                else:
                    logger.warning(
                        "USDT balance is 0 — using fallback initial capital"
                    )
            except Exception:
                logger.exception(
                    "Failed to fetch account balance — using fallback initial capital"
                )

        if initial_capital is None:
            initial_capital = self.position_size_usdt * 100
            logger.info("Using fallback initial capital: %.2f USDT", initial_capital)

        self._bridge = OrderBridge(
            demo=self.dry_run,  # Only skip orders in dry-run mode
            position_size_usdt=self.position_size_usdt,
            leverage=self.leverage,
            connector=connector,
            symbol=self.symbol,
            initial_capital=initial_capital,
        )

        # --- Restore trade history from disk ---
        self._restore_trade_history()

        ctx = ExecutionContext()
        self._runtime = PineRuntime(ctx)
        self._runtime.init_incremental(self.ast)

        # NOTE: Do NOT wire signal callbacks until warmup is complete.
        # During warmup we replay historical bars to build indicator state.
        # Signals from historical bars must be silently discarded.

        # --- Warmup ---
        await self._run_warmup()

        # --- Sync position state ---
        # After warmup, Pine StrategyContext may have a position from
        # replaying historical bars.  Sync OrderBridge to match so that
        # the first real signal is handled correctly (e.g. reversal close).
        await self._sync_position_state(connector)

        # Wire signal callbacks AFTER warmup + position sync
        if self._runtime.strategy_ctx:
            self._runtime.strategy_ctx.set_signal_callbacks(
                on_entry=self._bridge.on_entry,
                on_close=self._bridge.on_close,
                on_exit=self._bridge.on_exit,
            )

        # --- Live loop ---
        self._running = True
        # Write initial performance JSON so the web dashboard detects the strategy
        self._flush_performance(0.0)
        logger.info("Live trading active — waiting for confirmed klines")
        await self._poll_loop()

    async def stop(self) -> None:
        """Stop the live trading loop."""
        self._running = False
        self._flush_performance(self._bridge._last_price if self._bridge else 0.0)
        logger.info("Pine live engine stopped after %d bars", self._bars_processed)

    def _config_fingerprint(self) -> str:
        """Generate a fingerprint of the current strategy configuration.

        Used to detect whether a saved performance file belongs to the
        same configuration or a different one (changed params, new test).
        """
        config_str = (
            f"{self.pine_source}|{self.exchange}|{self.symbol}|"
            f"{self.timeframe}|{self.position_size_usdt}|{self.leverage}"
        )
        return hashlib.sha256(config_str.encode()).hexdigest()[:16]

    def _restore_trade_history(self) -> None:
        """Restore DemoTracker trade history from live_performance.json.

        Only restores if the saved config fingerprint matches the current
        configuration.  If it doesn't match (strategy changed), the old
        file is archived and a fresh run begins.
        """
        perf_path = (
            Path.home() / ".quantforge" / "live"
            / self.strategy_name / "live_performance.json"
        )
        if not perf_path.exists():
            logger.info("No previous performance file — starting fresh")
            return

        try:
            with open(perf_path, "r") as f:
                data = json.load(f)
        except Exception:
            logger.exception("Failed to read %s — starting fresh", perf_path)
            return

        saved_fp = data.get("config_fingerprint", "")
        current_fp = self._config_fingerprint()

        if saved_fp != current_fp:
            # Config changed — archive old file and start fresh
            from datetime import datetime
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_path = perf_path.with_name(f"live_performance_{ts}.json")
            try:
                perf_path.rename(archive_path)
                logger.warning(
                    "Config fingerprint mismatch (saved=%s, current=%s). "
                    "Archived old data to %s — starting fresh run",
                    saved_fp or "<none>", current_fp, archive_path.name,
                )
            except Exception:
                logger.exception("Failed to archive old performance file")
            return

        # Fingerprint matches — safe to restore
        trades = data.get("trades", [])
        if trades and self._bridge and self._bridge.demo_tracker:
            self._bridge.demo_tracker.restore_trades(trades)
            logger.info(
                "Restored %d trades from %s (fingerprint=%s)",
                len(trades), perf_path, current_fp,
            )

        # Restore open position as fallback (exchange sync will override later)
        open_pos = data.get("open_position")
        if open_pos and self._bridge:
            side = open_pos.get("side")
            entry_price = float(open_pos.get("entry_price", 0))
            qty = float(open_pos.get("qty", 0))
            if side and qty > 0:
                self._bridge.sync_position(side, qty, entry_price)
                logger.info(
                    "Restored open position from disk: %s %.6f @ %.2f "
                    "(will be overridden by exchange sync if available)",
                    side.upper(), qty, entry_price,
                )

    async def _sync_position_state(self, connector) -> None:
        """Sync OrderBridge position with exchange + Pine state after warmup.

        Priority:
        1. Exchange position (ground truth for live/demo mode)
        2. Pine StrategyContext position (fallback for dry-run mode)
        """
        exchange_pos = None

        # Try to read exchange position (works for live and demo/sandbox).
        # For Bitget UTA SPOT, fetch_positions is unsupported — connector
        # returns None via get_position() and we fall through to Pine state.
        if connector is not None:
            try:
                pos = connector.get_position()
                if pos:
                    # Re-shape get_position() output to the dict shape
                    # the rest of this method expects.
                    exchange_pos = {
                        "side": pos["side"],
                        "contracts": pos["contracts"],
                        "entryPrice": pos["entryPrice"],
                    }
            except Exception:
                logger.exception("Failed to fetch exchange positions")

        if exchange_pos:
            side = exchange_pos.get("side")  # "long" or "short"
            entry_price = float(exchange_pos.get("entryPrice", 0))
            contracts = float(exchange_pos.get("contracts", 0))
            self._bridge.sync_position(side, contracts, entry_price)
            logger.info(
                "Synced position from exchange: %s %.6f @ %.2f",
                side, contracts, entry_price,
            )
            # Also sync Pine StrategyContext to match exchange
            if self._runtime and self._runtime.strategy_ctx:
                from quantforge.pine.interpreter.builtins.strategy import Direction
                pine_dir = Direction.LONG if side == "long" else Direction.SHORT
                ctx = self._runtime.strategy_ctx
                ctx.position.direction = pine_dir
                ctx.position.qty = contracts
                ctx.position.entry_price = entry_price
                logger.info("Pine StrategyContext synced to exchange position")
            return

        # Exchange returned no open position.
        #
        # LIVE / DEMO with a live connector: exchange is the ground truth.
        # If Pine still thinks it's long/short (e.g. warmup simulation ended
        # in a position, or a phantom carried over from a previous run),
        # we MUST reset Pine + OrderBridge to FLAT. Trusting Pine here is
        # how the engine ends up sending a "close" order that actually
        # opens a fresh reverse position.
        #
        # DRY-RUN only: no exchange, so Pine state is all we have.
        is_live_with_exchange = connector is not None and not self.dry_run
        if is_live_with_exchange:
            self._bridge.sync_position(None, 0.0, 0.0)
            # Also reset Pine StrategyContext to match.
            if self._runtime and self._runtime.strategy_ctx:
                ctx = self._runtime.strategy_ctx
                if not ctx.position.is_flat:
                    # Expected on first start: Pine simulates strategy.entry/exit
                    # over the warmup bars and naturally ends in some position
                    # if the last historical signal was an entry. We reset to
                    # match exchange (FLAT) since live trades only fire on
                    # future signals. INFO, not WARNING — this is by-design.
                    logger.info(
                        "Pine ended warmup in %s qty=%.6f (simulated). "
                        "Exchange is FLAT — resetting Pine to match. "
                        "Real orders will only fire on signals after this point.",
                        ctx.position.direction.value if ctx.position.direction else "?",
                        ctx.position.qty,
                    )
                    ctx.position.direction = None  # None ⇒ is_flat == True
                    ctx.position.qty = 0.0
                    ctx.position.entry_price = 0.0
            logger.info("Exchange position is FLAT — synced.")
            return

        # Dry-run: trust Pine StrategyContext.
        if self._runtime and self._runtime.strategy_ctx:
            pine_pos = self._runtime.strategy_ctx.position
            if not pine_pos.is_flat:
                side = pine_pos.direction.value
                entry = pine_pos.entry_price
                qty = pine_pos.qty
                if entry > 0 and self.position_size_usdt > 0:
                    actual_qty = (self.position_size_usdt * self.leverage) / entry
                else:
                    actual_qty = qty
                self._bridge.sync_position(side, actual_qty, entry)
                logger.info(
                    "(dry-run) Synced position from Pine state: %s qty=%.6f entry=%.2f",
                    side, actual_qty, entry,
                )
            else:
                self._bridge.sync_position(None, 0.0, 0.0)
                logger.info("(dry-run) Pine state is FLAT — no position to sync")

    async def _run_warmup(self) -> None:
        """Fetch historical bars and feed them to the interpreter."""
        logger.info("Fetching %d warmup bars...", self.warmup_bars)

        bars = fetch_warmup_bars(
            symbol=self.symbol,
            exchange_id=self.exchange,
            timeframe=self.timeframe,
            num_bars=self.warmup_bars,
        )

        if not bars:
            logger.warning("No warmup bars fetched — indicators will start cold")
            self._warmup_complete = True
            return

        for bar in bars:
            self._runtime.process_bar(bar)
            self._bars_processed += 1

        self._last_bar_time = bars[-1].time
        self._warmup_complete = True
        logger.info(
            "Warmup complete — processed %d bars, indicators ready",
            len(bars),
        )

    async def _poll_loop(self) -> None:
        """Poll for new confirmed klines via ccxt.

        Calculates the exact time until the next bar closes, sleeps until
        then + a small buffer, and fetches only the latest confirmed bar.

        In a production deployment this would be replaced with a WebSocket
        kline subscription from the QuantForge connector layer.
        """
        import time

        import ccxt

        exchange_cls = getattr(ccxt, self.exchange)
        exchange = exchange_cls({"enableRateLimit": True})
        exchange.load_markets()

        tf_sec = timeframe_to_seconds(self.timeframe)
        buffer_sec = 5  # seconds after bar close before fetching

        while self._running:
            # Calculate exact sleep until next bar close + buffer
            now = time.time()
            next_bar_close = ((now // tf_sec) + 1) * tf_sec
            wait_time = max(1, next_bar_close - now + buffer_sec)

            logger.debug(
                "Sleeping %.1fs until next bar close (tf=%ss, buffer=%ss)",
                wait_time,
                tf_sec,
                buffer_sec,
            )
            await asyncio.sleep(wait_time)

            try:
                # Fetch last 2 bars — second-to-last is the confirmed bar
                ohlcv = exchange.fetch_ohlcv(self.symbol, self.timeframe, limit=2)
                if ohlcv and len(ohlcv) >= 2:
                    confirmed = ohlcv[-2]
                    bar_ts = confirmed[0] // 1000

                    if bar_ts > self._last_bar_time:
                        bar = BarData(
                            open=confirmed[1],
                            high=confirmed[2],
                            low=confirmed[3],
                            close=confirmed[4],
                            volume=confirmed[5],
                            time=bar_ts,
                        )
                        # Update price before processing (for P&L tracking)
                        self._bridge.update_price(bar.close)

                        new_orders = self._runtime.process_bar(bar)
                        self._bars_processed += 1
                        self._last_bar_time = bar_ts

                        logger.info(
                            "Bar %d | close=%.2f | orders=%d | pos=%s",
                            self._bars_processed,
                            bar.close,
                            len(new_orders),
                            self._bridge._position_side or "flat",
                        )

                        # Flush performance JSON for web dashboard
                        self._flush_performance(bar.close)

                        # Print demo P&L summary every 6 bars (6h for 1h tf)
                        tracker = self._bridge.demo_tracker
                        if tracker and self._bars_processed % 6 == 0:
                            logger.info("\n%s", tracker.summary(bar.close))

            except Exception:
                logger.exception("Error in poll loop")

    def feed_bar(self, bar: BarData) -> list:
        """Manually feed a bar (for testing or WebSocket integration).

        Returns list of new orders placed during this bar.
        """
        if self._runtime is None:
            raise RuntimeError(
                "Engine not started — call start() first or setup manually"
            )
        new_orders = self._runtime.process_bar(bar)
        self._bars_processed += 1
        self._last_bar_time = bar.time
        return new_orders

    @property
    def bridge(self) -> OrderBridge | None:
        return self._bridge

    @property
    def runtime(self) -> PineRuntime | None:
        return self._runtime

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def bars_processed(self) -> int:
        return self._bars_processed

    def _flush_performance(self, current_price: float = 0.0) -> None:
        """Write live performance data to JSON for the web dashboard."""
        tracker = self._bridge.demo_tracker if self._bridge else None
        if tracker is None:
            return
        try:
            perf_dir = Path.home() / ".quantforge" / "live" / self.strategy_name
            perf_dir.mkdir(parents=True, exist_ok=True)
            perf_path = perf_dir / "live_performance.json"

            data = tracker.to_dict(current_price)
            data["config_name"] = self.strategy_name
            data["config_fingerprint"] = self._config_fingerprint()
            # Write atomically via temp file
            tmp = perf_path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            tmp.replace(perf_path)
        except Exception:
            logger.exception("Failed to flush performance JSON")
