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

        # DemoTracker's reporting baseline = the user's allocation, NOT the
        # exchange wallet balance. The wallet can hold deposits / withdrawals
        # / margin from other strategies entirely outside this engine's
        # concern; folding it into the baseline would corrupt the strategy's
        # P&L percentages with unrelated cash flows. The strategy is given
        # ``position_size_usdt`` to play with — that is the denominator.
        #
        # We still query the wallet once just to log it for the operator
        # ("here's what's actually sitting on the exchange"), but it does
        # NOT feed the engine's statistics.
        if connector is not None:
            try:
                params = {"uta": True} if self.exchange == "bitget" else {}
                balance = connector._exchange.fetch_balance(params)
                wallet_usdt = float(balance.get("USDT", {}).get("total", 0))
                logger.info(
                    "Wallet balance: %.2f USDT (informational; not used as "
                    "P&L baseline — that's bound to position_size_usdt=%.2f)",
                    wallet_usdt, self.position_size_usdt,
                )
            except Exception:
                logger.exception(
                    "Failed to fetch wallet balance for logging (non-fatal)"
                )

        self._bridge = OrderBridge(
            demo=self.dry_run,  # Only skip orders in dry-run mode
            position_size_usdt=self.position_size_usdt,
            leverage=self.leverage,
            connector=connector,
            symbol=self.symbol,
            initial_capital=float(self.position_size_usdt),
        )

        # --- Restore trade history from disk ---
        self._restore_trade_history()

        ctx = ExecutionContext()
        self._runtime = PineRuntime(ctx)
        self._runtime.init_incremental(self.ast)

        # Backtest ↔ live alignment override (single helper used by every
        # entry point — backtest, optimize, CLI, live). The live engine
        # always has a position_size_usdt so this always runs.
        self._runtime.apply_sizing_override(
            self.position_size_usdt, self.leverage,
        )
        sc = self._runtime.strategy_ctx
        if sc is not None and self.position_size_usdt > 0:
            # Pine equity baseline + DemoTracker baseline both anchor to
            # position_size_usdt — the strategy is measured strictly against
            # the allocation it was given. Wallet movements (deposits,
            # other strategies, margin reservations) don't pollute these
            # numbers.
            logger.info(
                "Pine sizing aligned to live: qty_type=%s default_qty=%.4f "
                "initial_capital=$%.2f commission=0",
                sc.default_qty_type, sc.default_qty, sc.initial_capital,
            )

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

        # Warmup ran the script over historical bars and Pine internally
        # filled imaginary entry/close orders, accumulating a `trades` ledger
        # and walking `equity` away from initial_capital. Reset that ledger
        # so the live phase starts from a clean slate — indicators / series
        # state (the actual point of warmup) stay intact because they live
        # on the ExecutionContext, not on the StrategyContext.
        if sc is not None:
            sc.trades.clear()
            sc.pending_orders.clear()
            sc._equity_curve.clear()
            sc.equity = sc.initial_capital
            sc._entry_count = 0
        # Keep DemoTracker's view aligned: its trade ledger was repopulated
        # from disk by ``_restore_trade_history`` (for cross-session continuity).
        # We DO NOT clear that, since the user wants to see cumulative live P&L
        # across restarts. The asymmetry (Pine sees 0, tracker sees N) is fine
        # because Pine doesn't reference past trades and tracker only feeds
        # the dashboard, not the strategy.

        # Wire signal callbacks AFTER warmup + position sync.
        # Queue-time → SignalRecord history; fill-time → exchange submission.
        if self._runtime.strategy_ctx:
            sc = self._runtime.strategy_ctx
            sc.set_signal_callbacks(
                on_entry=self._bridge.on_entry,
                on_close=self._bridge.on_close,
                on_exit=self._bridge.on_exit,
                on_entry_fill=self._bridge.on_entry_fill,
                on_close_fill=self._bridge.on_close_fill,
            )
            # If a queue-time exchange submission fails, the bridge calls
            # this back to remove the still-queued Pine order. Without
            # the rollback Pine would fill it internally on the next bar
            # and the script would carry on as if the entry/close
            # succeeded — wrong position, wrong equity, spurious follow-up
            # orders. Reconcile catches drift one bar later, but only
            # AFTER the bad state has potentially fired more signals.
            self._bridge.set_failure_callback(sc.cancel_pending)

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

        # Fingerprint matches — restore only the open position as a fallback
        # (the upcoming exchange-sync step will override if connected). We
        # deliberately do NOT restore the trades history: after warmup,
        # Pine clears its own ctx.trades for a clean live phase, so we keep
        # DemoTracker symmetrical to avoid a strategy that reads
        # `strategy.closedtrades` seeing values the live session never
        # produced. Trades that count toward dashboard P&L will accumulate
        # from this session forward; the previous session's totals live in
        # the archived ``live_performance_*.json`` files for audit.
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
            self._apply_synced_pine_position(side, contracts, entry_price)
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
            if self._runtime and self._runtime.strategy_ctx:
                ctx = self._runtime.strategy_ctx
                if not ctx.position.is_flat:
                    logger.info(
                        "Pine ended warmup in %s qty=%.6f (simulated). "
                        "Exchange is FLAT — resetting Pine to match. "
                        "Real orders will only fire on signals after this point.",
                        ctx.position.direction.value if ctx.position.direction else "?",
                        ctx.position.qty,
                    )
                self._apply_synced_pine_position(None, 0.0, 0.0)
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
        """Poll for confirmed klines, backfilling any bars we fell behind on.

        Each iteration:
          1. Sleep until the next bar's close + a small buffer.
          2. Fetch every confirmed bar with ``ts > _last_bar_time``
             (limit sized to the gap, so a brief network outage doesn't
             desync indicators from backtest).
          3. Process them in order. If more than one bar arrived, treat
             as a backfill: suppress exchange submission while replaying
             so Pine can rebuild indicator state without firing stale
             market orders. The reconcile step afterwards corrects any
             resulting position drift.
          4. Reconcile Pine's expected position against the exchange.

        In production this loop would be replaced with a websocket kline
        subscription from the connector layer.
        """
        import time
        import math

        import ccxt

        exchange_cls = getattr(ccxt, self.exchange)
        exchange = exchange_cls({"enableRateLimit": True})
        exchange.load_markets()

        tf_sec = timeframe_to_seconds(self.timeframe)
        tf_ms = tf_sec * 1000
        buffer_sec = 5  # seconds after bar close before fetching

        while self._running:
            # Sleep until next bar close + buffer
            now = time.time()
            next_bar_close = ((now // tf_sec) + 1) * tf_sec
            wait_time = max(1, next_bar_close - now + buffer_sec)
            logger.debug(
                "Sleeping %.1fs until next bar close (tf=%ss, buffer=%ss)",
                wait_time, tf_sec, buffer_sec,
            )
            await asyncio.sleep(wait_time)

            try:
                # ── Pull every bar we haven't seen ─────────────────────
                # since_ms = first millisecond AFTER the last bar we processed
                since_ms = (self._last_bar_time + 1) * 1000 if self._last_bar_time else None
                now_ms = int(time.time() * 1000)
                current_bar_open_ms = (now_ms // tf_ms) * tf_ms
                missing = (
                    max(2, math.ceil((now_ms - since_ms) / tf_ms) + 2)
                    if since_ms else 2
                )
                limit = min(missing, 500)
                ohlcv = (
                    exchange.fetch_ohlcv(self.symbol, self.timeframe, since=since_ms, limit=limit)
                    if since_ms is not None
                    else exchange.fetch_ohlcv(self.symbol, self.timeframe, limit=limit)
                )
                if not ohlcv:
                    continue

                # Confirmed bars only — exclude the bar currently in progress.
                new_bars = [
                    b for b in ohlcv
                    if b[0] < current_bar_open_ms and (b[0] // 1000) > self._last_bar_time
                ]
                if not new_bars:
                    continue

                # ── Backfill suppression ──────────────────────────────
                is_backfill = len(new_bars) > 1
                if is_backfill:
                    logger.warning(
                        "Falling behind: backfilling %d bars (last_bar_time=%d, "
                        "now=%d). Exchange submission suppressed during replay; "
                        "reconcile runs after.",
                        len(new_bars), self._last_bar_time, now_ms // 1000,
                    )
                    self._bridge._submission_enabled = False

                for confirmed in new_bars:
                    bar_ts = confirmed[0] // 1000
                    bar = BarData(
                        open=confirmed[1], high=confirmed[2], low=confirmed[3],
                        close=confirmed[4], volume=confirmed[5], time=bar_ts,
                    )
                    self._bridge.update_price(bar.open)
                    new_orders = self._runtime.process_bar(bar)
                    self._bridge.update_price(bar.close)
                    self._bars_processed += 1
                    self._last_bar_time = bar_ts

                    logger.info(
                        "Bar %d | open=%.2f close=%.2f | new_queue=%d | pos=%s qty=%.6f%s",
                        self._bars_processed, bar.open, bar.close,
                        len(new_orders),
                        self._bridge._position_side or "flat",
                        self._bridge._position_qty,
                        " [backfill]" if is_backfill else "",
                    )

                if is_backfill:
                    self._bridge._submission_enabled = True

                # ── Reconcile every poll iteration ─────────────────────
                await self._reconcile_position()

                # Flush performance JSON for the dashboard
                self._flush_performance(new_bars[-1][4])

                # Demo P&L summary periodically
                tracker = self._bridge.demo_tracker
                if tracker and self._bars_processed % 6 == 0:
                    logger.info("\n%s", tracker.summary(new_bars[-1][4]))

            except Exception:
                logger.exception("Error in poll loop")

    async def _reconcile_position(self) -> None:
        """Verify the bridge's position view matches the exchange.

        Drift can creep in from:
          - Exchange rejecting a submitted order (insufficient margin, etc.)
          - Sub-bar slippage between queue-time estimate qty and Pine's
            resolved qty
          - Backfilled bars that fired Pine signals without exchange sends
          - Partial fills

        When the relative qty diff exceeds 0.5% (or side mismatches) we
        log + treat the exchange as ground truth: re-sync the bridge and
        Pine's StrategyContext to whatever the exchange reports. This
        prevents long-term divergence even when individual order
        submissions fail.
        """
        if self.dry_run:
            return
        connector = getattr(self._bridge, "_connector", None)
        if connector is None:
            return
        try:
            pos = connector.get_position()
        except Exception:
            logger.exception("Reconcile: failed to fetch exchange position")
            return

        if pos:
            exch_side = pos.get("side")
            exch_qty = float(pos.get("contracts", 0))
            exch_entry = float(pos.get("entryPrice", 0))
        else:
            exch_side = None
            exch_qty = 0.0
            exch_entry = 0.0

        bridge_side = self._bridge._position_side
        bridge_qty = self._bridge._position_qty

        side_drift = bridge_side != exch_side
        qty_drift = abs(bridge_qty - exch_qty)
        rel_drift = (qty_drift / bridge_qty) if bridge_qty > 0 else qty_drift

        if not (side_drift or rel_drift > 0.005):
            return

        logger.warning(
            "POSITION DRIFT detected — bridge=%s qty=%.6f, exchange=%s qty=%.6f. "
            "Treating exchange as ground truth and re-syncing Pine.",
            bridge_side or "flat", bridge_qty, exch_side or "flat", exch_qty,
        )
        self._bridge.sync_position(exch_side, exch_qty, exch_entry)
        self._apply_synced_pine_position(exch_side, exch_qty, exch_entry)

    def _apply_synced_pine_position(
        self, side: str | None, qty: float, entry_price: float,
    ) -> None:
        """Push an externally-resolved position into Pine's StrategyContext.

        Used by both the post-warmup ``_sync_position_state`` (exchange is
        ground truth) and the per-bar ``_reconcile_position`` (drift
        correction). Beyond direction/qty/entry_price, this resets the
        bookkeeping fields that strategies query so they don't see stale
        warmup values:

        * ``entry_bar`` ← current ``ctx.bar_index`` (so
          ``bar_index - position.entry_bar`` measures "bars since we
          observed this position", not "bars since the warmup
          simulation imagined entering").
        * ``_mfe`` / ``_mae`` ← 0 (we have no real intra-position
          excursion history for a synced position).
        * ``_entry_count`` ← 1 if synced into a position, else 0
          (pyramiding count restarts from this single observation).
        """
        if not self._runtime or not self._runtime.strategy_ctx:
            return
        from quantforge.pine.interpreter.builtins.strategy import Direction
        sc = self._runtime.strategy_ctx
        current_bar_index = self._runtime.ctx.bar_index
        if side:
            sc.position.direction = (
                Direction.LONG if side == "long" else Direction.SHORT
            )
            sc.position.qty = float(qty)
            sc.position.entry_price = float(entry_price)
            sc.position.entry_bar = current_bar_index
            sc.position.comment = ""
            sc.position._mfe = 0.0
            sc.position._mae = 0.0
            sc._entry_count = 1
        else:
            sc.position.direction = None
            sc.position.qty = 0.0
            sc.position.entry_price = 0.0
            sc.position.entry_bar = 0
            sc.position.comment = ""
            sc.position._mfe = 0.0
            sc.position._mae = 0.0
            sc._entry_count = 0

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
