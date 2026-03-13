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
import logging
import time
from pathlib import Path

from quantforge.pine.interpreter.context import BarData, ExecutionContext
from quantforge.pine.interpreter.runtime import PineRuntime
from quantforge.pine.live.connector import fetch_warmup_bars, timeframe_to_seconds
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
    ) -> None:
        self.pine_source = pine_source
        self.exchange = exchange
        self.symbol = symbol
        self.timeframe = timeframe
        self.demo = demo
        self.warmup_bars = warmup_bars
        self.position_size_usdt = position_size_usdt

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

        1. Fetch warmup klines and build indicator state.
        2. Poll for new confirmed klines and feed them to the interpreter.
        """
        logger.info(
            "Starting Pine live engine | symbol=%s exchange=%s tf=%s demo=%s",
            self.symbol,
            self.exchange,
            self.timeframe,
            self.demo,
        )

        # --- Setup ---
        self._bridge = OrderBridge(
            demo=self.demo,
            position_size_usdt=self.position_size_usdt,
        )

        ctx = ExecutionContext()
        self._runtime = PineRuntime(ctx)
        self._runtime.init_incremental(self.ast)

        # Wire signal callbacks
        if self._runtime.strategy_ctx:
            self._runtime.strategy_ctx.set_signal_callbacks(
                on_entry=self._bridge.on_entry,
                on_close=self._bridge.on_close,
                on_exit=self._bridge.on_exit,
            )

        # --- Warmup ---
        await self._run_warmup()

        # --- Live loop ---
        self._running = True
        logger.info("Live trading active — waiting for confirmed klines")
        await self._poll_loop()

    async def stop(self) -> None:
        """Stop the live trading loop."""
        self._running = False
        logger.info("Pine live engine stopped after %d bars", self._bars_processed)

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

        In a production deployment this would be replaced with a WebSocket
        kline subscription from the QuantForge connector layer.
        """
        import ccxt

        exchange_cls = getattr(ccxt, self.exchange)
        exchange = exchange_cls({"enableRateLimit": True})
        exchange.load_markets()

        tf_sec = timeframe_to_seconds(self.timeframe)

        while self._running:
            try:
                # Fetch last 2 bars
                ohlcv = exchange.fetch_ohlcv(
                    self.symbol, self.timeframe, limit=2
                )
                if ohlcv and len(ohlcv) >= 2:
                    # The second-to-last bar is the most recent *confirmed* bar
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

            except Exception:
                logger.exception("Error in poll loop")

            # Sleep until ~5s after next bar expected close
            await asyncio.sleep(min(tf_sec * 0.1, 30))

    def feed_bar(self, bar: BarData) -> list:
        """Manually feed a bar (for testing or WebSocket integration).

        Returns list of new orders placed during this bar.
        """
        if self._runtime is None:
            raise RuntimeError("Engine not started — call start() first or setup manually")
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
