"""
NexusTrader Indicator wrapper for Hurst-Kalman strategy.

Delegates all indicator calculations to HurstKalmanSignalCore, ensuring
100% parity with the backtest signal generator.
"""

from enum import Enum
from typing import Optional

from nexustrader.constants import KlineInterval
from nexustrader.indicator import Indicator
from nexustrader.schema import BookL1, BookL2, Kline, Trade

from strategy.strategies.hurst_kalman.core import HurstKalmanConfig
from strategy.indicators.hurst_kalman import (
    BUY,
    CLOSE,
    HOLD,
    HurstKalmanSignalCore,
    SELL,
)


class MarketState(Enum):
    """Market state based on Hurst exponent."""

    UNKNOWN = "unknown"
    MEAN_REVERTING = "mean_reverting"
    RANDOM_WALK = "random_walk"
    TRENDING = "trending"


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

# Map core market state strings to MarketState enum
_MARKET_STATE_MAP = {
    "unknown": MarketState.UNKNOWN,
    "mean_reverting": MarketState.MEAN_REVERTING,
    "random_walk": MarketState.RANDOM_WALK,
    "trending": MarketState.TRENDING,
}


class HurstKalmanIndicator(Indicator):
    """
    Indicator that calculates Hurst exponent and Kalman-filtered price.

    All indicator calculations are delegated to HurstKalmanSignalCore,
    which is shared with the backtest signal generator.
    """

    def __init__(
        self,
        config: Optional[HurstKalmanConfig] = None,
        warmup_period: Optional[int] = None,
        kline_interval: KlineInterval = KlineInterval.MINUTE_15,
    ):
        self._config = config or HurstKalmanConfig()

        self._real_warmup_period = (
            self._config.hurst_window + self._config.zscore_window
        )

        super().__init__(
            params={
                "hurst_window": self._config.hurst_window,
                "kalman_R": self._config.kalman_R,
                "kalman_Q": self._config.kalman_Q,
                "zscore_window": self._config.zscore_window,
            },
            name="HurstKalman",
            warmup_period=warmup_period,
            kline_interval=kline_interval,
        )

        # Shared core
        self._core = HurstKalmanSignalCore(self._config)

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
        self._last_price = price

        self._core.update_indicators_only(close=price)

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
            "hurst": self._core.hurst_value,
            "kalman_price": self._core.kalman_price_value,
            "zscore": self._core.zscore_value,
            "slope": self._core.slope_value,
            "market_state": self._core.market_state,
            "signal": self._signal.value,
        }

    @property
    def hurst(self) -> float:
        return self._core.hurst_value

    @property
    def kalman_price(self) -> Optional[float]:
        return self._core.kalman_price_value

    @property
    def zscore(self) -> float:
        return self._core.zscore_value

    @property
    def slope(self) -> float:
        return self._core.slope_value

    @property
    def market_state(self) -> MarketState:
        return _MARKET_STATE_MAP.get(self._core.market_state, MarketState.UNKNOWN)

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
