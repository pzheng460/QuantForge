"""
NexusTrader Indicator wrapper for EMA Crossover strategy.

Delegates all indicator calculations to EMASignalCore, ensuring
100% parity with the backtest signal generator.
"""

from enum import Enum
from typing import Optional

from nexustrader.constants import KlineInterval
from nexustrader.indicator import Indicator
from nexustrader.schema import BookL1, BookL2, Kline, Trade

from strategy.bitget.ema_crossover.core import EMAConfig
from strategy.indicators.ema_crossover import BUY, HOLD, EMASignalCore, SELL


class Signal(Enum):
    """Trading signals."""

    HOLD = "hold"
    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"


# Map EMASignalCore int constants to Signal enum
_SIGNAL_MAP = {
    HOLD: Signal.HOLD,
    BUY: Signal.BUY,
    SELL: Signal.SELL,
}


class EMACrossoverIndicator(Indicator):
    """
    Indicator that calculates fast/slow EMA crossover signals.

    All indicator calculations are delegated to EMASignalCore,
    which is shared with the backtest signal generator.
    """

    def __init__(
        self,
        config: Optional[EMAConfig] = None,
        warmup_period: Optional[int] = None,
        kline_interval: KlineInterval = KlineInterval.MINUTE_15,
    ):
        self._config = config or EMAConfig()

        self._real_warmup_period = self._config.slow_period + 10

        super().__init__(
            params={
                "fast_period": self._config.fast_period,
                "slow_period": self._config.slow_period,
            },
            name="EMACrossover",
            warmup_period=warmup_period,
            kline_interval=kline_interval,
        )

        # Shared core (indicators only - no position management)
        self._core = EMASignalCore(self._config)

        self._confirmed_bar_count: int = 0
        self._signal: Signal = Signal.HOLD
        self._last_price: Optional[float] = None

    def handle_kline(self, kline: Kline) -> None:
        """Process a new kline using bar confirmation via timestamp change."""
        bar_start = int(kline.start)

        if not hasattr(self, "_current_bar_start"):
            self._current_bar_start = bar_start
            self._current_bar_kline = kline
            return

        if bar_start != self._current_bar_start:
            confirmed_kline = self._current_bar_kline
            self._confirmed_bar_count += 1
            self._process_kline_data(confirmed_kline)

            self._current_bar_start = bar_start
            self._current_bar_kline = kline
        else:
            self._current_bar_kline = kline

    @property
    def is_warmed_up(self) -> bool:
        return self._confirmed_bar_count >= self._real_warmup_period

    def _process_kline_data(self, kline: Kline) -> None:
        """Core kline processing: delegate to EMASignalCore."""
        price = float(kline.close)
        self._last_price = price

        self._core.update_indicators_only(close=price)

        # Update signal from raw indicator values
        raw = self._core.get_raw_signal()
        self._signal = _SIGNAL_MAP.get(raw, Signal.HOLD)

    def handle_bookl1(self, bookl1: BookL1) -> None:
        pass

    def handle_bookl2(self, bookl2: BookL2) -> None:
        pass

    def handle_trade(self, trade: Trade) -> None:
        pass

    # ---------- Properties (delegate to core) ----------

    @property
    def value(self) -> dict:
        return {
            "fast_ema": self._core.ema_fast_value,
            "slow_ema": self._core.ema_slow_value,
            "signal": self._signal.value,
        }

    @property
    def fast_ema(self) -> Optional[float]:
        return self._core.ema_fast_value

    @property
    def slow_ema(self) -> Optional[float]:
        return self._core.ema_slow_value

    @property
    def signal(self) -> Signal:
        return self._signal

    def get_signal(self) -> Signal:
        return self._signal

    def should_stop_loss(
        self, entry_price: float, current_price: float, is_long: bool
    ) -> bool:
        if entry_price <= 0:
            return False
        if is_long:
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - current_price) / entry_price
        return pnl_pct < -self._config.stop_loss_pct

    def reset(self) -> None:
        self._core.reset()
        self._signal = Signal.HOLD
        self._last_price = None
        self._confirmed_bar_count = 0
        if hasattr(self, "_current_bar_start"):
            del self._current_bar_start
        if hasattr(self, "_current_bar_kline"):
            del self._current_bar_kline
