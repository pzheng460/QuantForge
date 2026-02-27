"""
NexusTrader Indicator wrapper for VWAP Mean Reversion strategy.

Delegates all indicator calculations to VWAPSignalCore, ensuring
100% parity with the backtest signal generator.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from nexustrader.constants import KlineInterval
from nexustrader.indicator import Indicator
from nexustrader.schema import BookL1, BookL2, Kline, Trade

from strategy.bitget.vwap.core import VWAPConfig
from strategy.indicators.vwap import BUY, CLOSE, HOLD, SELL, VWAPSignalCore


class Signal(Enum):
    """Trading signals."""

    HOLD = "hold"
    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"


# Map int constants to Signal enum
_SIGNAL_MAP = {
    HOLD: Signal.HOLD,
    BUY: Signal.BUY,
    SELL: Signal.SELL,
    CLOSE: Signal.CLOSE,
}


class VWAPIndicator(Indicator):
    """
    Indicator that calculates VWAP, Z-Score, and RSI for mean-reversion signals.

    All indicator calculations are delegated to VWAPSignalCore,
    which is shared with the backtest signal generator.
    """

    def __init__(
        self,
        config: Optional[VWAPConfig] = None,
        warmup_period: Optional[int] = None,
        kline_interval: KlineInterval = KlineInterval.MINUTE_5,
    ):
        self._config = config or VWAPConfig()

        self._real_warmup_period = self._config.std_window + self._config.rsi_period

        super().__init__(
            params={
                "std_window": self._config.std_window,
                "rsi_period": self._config.rsi_period,
                "zscore_entry": self._config.zscore_entry,
            },
            name="VWAP",
            warmup_period=warmup_period,
            kline_interval=kline_interval,
        )

        # Shared core
        self._core = VWAPSignalCore(self._config)

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
        price = float(kline.close)
        high = float(kline.high)
        low = float(kline.low)
        volume = float(kline.volume)
        self._last_price = price

        # Day boundary detection
        ts = kline.timestamp
        if hasattr(ts, "date"):
            day = ts.date()
        else:
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            day = dt.date()

        self._core.update_indicators_only(
            close=price, high=high, low=low, volume=volume, day=day
        )

        raw = self._core.get_raw_signal()
        self._signal = _SIGNAL_MAP.get(raw, Signal.HOLD)

    def handle_bookl1(self, bookl1: BookL1) -> None:
        pass

    def handle_bookl2(self, bookl2: BookL2) -> None:
        pass

    def handle_trade(self, trade: Trade) -> None:
        pass

    @property
    def value(self) -> dict:
        return {
            "vwap": self._core.vwap_value,
            "zscore": self._core.zscore_value,
            "rsi": self._core.rsi_value,
            "signal": self._signal.value,
        }

    @property
    def vwap(self) -> Optional[float]:
        return self._core.vwap_value

    @property
    def zscore(self) -> float:
        return self._core.zscore_value

    @property
    def rsi(self) -> float:
        return self._core.rsi_value

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
        if pnl_pct < -self._config.stop_loss_pct:
            return True
        if abs(self._core.zscore_value) > self._config.zscore_stop:
            return True
        return False

    def reset(self) -> None:
        self._core.reset()
        self._signal = Signal.HOLD
        self._last_price = None
        self._confirmed_bar_count = 0
        if hasattr(self, "_current_bar_start"):
            del self._current_bar_start
        if hasattr(self, "_current_bar_kline"):
            del self._current_bar_kline
