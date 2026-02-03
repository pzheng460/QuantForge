"""
NexusTrader Indicator wrapper for Hurst-Kalman strategy.

This module wraps the core algorithms into a NexusTrader Indicator class
that can be registered with a Strategy.
"""

from collections import deque
from enum import Enum
from typing import Optional

import numpy as np

from nexustrader.constants import KlineInterval
from nexustrader.indicator import Indicator
from nexustrader.schema import BookL1, BookL2, Kline, Trade

from strategy.bitget.hurst_kalman.core import (
    HurstKalmanConfig,
    KalmanFilter1D,
    calculate_hurst,
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


class HurstKalmanIndicator(Indicator):
    """
    Indicator that calculates Hurst exponent and Kalman-filtered price.

    This indicator provides:
    - Hurst exponent for market state identification
    - Kalman-filtered price estimate
    - Z-Score of price deviation
    - Kalman slope for trend direction
    - Trading signals based on market state
    """

    def __init__(
        self,
        config: Optional[HurstKalmanConfig] = None,
        warmup_period: int = 150,
        kline_interval: KlineInterval = KlineInterval.MINUTE_15,
    ):
        """
        Initialize the Hurst-Kalman indicator.

        Args:
            config: Strategy configuration (uses defaults if None)
            warmup_period: Number of klines for warmup
            kline_interval: Kline interval for warmup data
        """
        self._config = config or HurstKalmanConfig()

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

        # Kalman filter
        self._kalman = KalmanFilter1D(
            R=self._config.kalman_R,
            Q=self._config.kalman_Q,
        )

        # Price history for Hurst calculation
        self._price_history: deque[float] = deque(maxlen=self._config.hurst_window + 50)

        # Kalman estimate history for Z-Score
        self._kalman_history: deque[float] = deque(
            maxlen=self._config.zscore_window + 10
        )

        # Current values
        self._hurst: float = 0.5
        self._kalman_price: Optional[float] = None
        self._zscore: float = 0.0
        self._slope: float = 0.0
        self._market_state: MarketState = MarketState.UNKNOWN
        self._signal: Signal = Signal.HOLD
        self._last_price: Optional[float] = None

    def handle_kline(self, kline: Kline) -> None:
        """
        Process a new kline and update indicator values.

        Args:
            kline: The new kline data
        """
        # Manual warmup tracking (in case framework warmup fails)
        if not self._is_warmed_up:
            self._warmup_data_count += 1
            if self._warmup_data_count >= self.warmup_period:
                self._is_warmed_up = True

        price = float(kline.close)
        self._last_price = price

        # Update price history
        self._price_history.append(price)

        # Update Kalman filter
        self._kalman_price = self._kalman.update(price)
        self._kalman_history.append(self._kalman_price)

        # Calculate Hurst (need enough data)
        if len(self._price_history) >= self._config.hurst_window:
            prices_array = np.array(self._price_history)
            self._hurst = calculate_hurst(prices_array, self._config.hurst_window)

        # Calculate Z-Score
        if len(self._kalman_history) >= self._config.zscore_window:
            recent_prices = list(self._price_history)[-self._config.zscore_window :]
            recent_kalman = list(self._kalman_history)[-self._config.zscore_window :]

            deviations = np.array(recent_prices) - np.array(recent_kalman)
            std = np.std(deviations)

            if std > 1e-10:
                self._zscore = (price - self._kalman_price) / std
            else:
                self._zscore = 0.0

        # Get slope
        self._slope = self._kalman.get_slope(lookback=5)

        # Determine market state
        self._update_market_state()

        # Generate signal
        self._update_signal()

    def _update_market_state(self) -> None:
        """Update market state based on Hurst value."""
        if self._hurst < self._config.mean_reversion_threshold:
            self._market_state = MarketState.MEAN_REVERTING
        elif self._hurst > self._config.trend_threshold:
            self._market_state = MarketState.TRENDING
        elif (
            self._config.mean_reversion_threshold
            <= self._hurst
            <= self._config.trend_threshold
        ):
            self._market_state = MarketState.RANDOM_WALK
        else:
            self._market_state = MarketState.UNKNOWN

    def _update_signal(self) -> None:
        """Generate trading signal based on market state and indicators."""
        if self._kalman_price is None or self._last_price is None:
            self._signal = Signal.HOLD
            return

        # Random walk - close positions, don't trade
        if self._market_state == MarketState.RANDOM_WALK:
            self._signal = Signal.CLOSE
            return

        # Mean reversion mode
        if self._market_state == MarketState.MEAN_REVERTING:
            if self._zscore < -self._config.zscore_entry:
                self._signal = Signal.BUY
            elif self._zscore > self._config.zscore_entry:
                self._signal = Signal.SELL
            elif abs(self._zscore) < 0.5:
                self._signal = Signal.CLOSE
            else:
                self._signal = Signal.HOLD
            return

        # Trend following mode
        if self._market_state == MarketState.TRENDING:
            if self._last_price > self._kalman_price and self._slope > 0:
                self._signal = Signal.BUY
            elif self._last_price < self._kalman_price and self._slope < 0:
                self._signal = Signal.SELL
            elif self._slope * (1 if self._signal == Signal.BUY else -1) < 0:
                # Slope reversed
                self._signal = Signal.CLOSE
            else:
                self._signal = Signal.HOLD
            return

        self._signal = Signal.HOLD

    def handle_bookl1(self, bookl1: BookL1) -> None:
        """Handle bookl1 data (not used for this indicator)."""
        pass

    def handle_bookl2(self, bookl2: BookL2) -> None:
        """Handle bookl2 data (not used for this indicator)."""
        pass

    def handle_trade(self, trade: Trade) -> None:
        """Handle trade data (not used for this indicator)."""
        pass

    # Properties for accessing indicator values

    @property
    def value(self) -> dict:
        """Get all indicator values as a dictionary."""
        return {
            "hurst": self._hurst,
            "kalman_price": self._kalman_price,
            "zscore": self._zscore,
            "slope": self._slope,
            "market_state": self._market_state.value,
            "signal": self._signal.value,
        }

    @property
    def hurst(self) -> float:
        """Get current Hurst exponent."""
        return self._hurst

    @property
    def kalman_price(self) -> Optional[float]:
        """Get current Kalman-filtered price."""
        return self._kalman_price

    @property
    def zscore(self) -> float:
        """Get current Z-Score."""
        return self._zscore

    @property
    def slope(self) -> float:
        """Get current Kalman slope."""
        return self._slope

    @property
    def market_state(self) -> MarketState:
        """Get current market state."""
        return self._market_state

    @property
    def signal(self) -> Signal:
        """Get current trading signal."""
        return self._signal

    def get_signal(self) -> Signal:
        """Get current trading signal (method form for compatibility)."""
        return self._signal

    def should_stop_loss(self, entry_price: float, current_price: float) -> bool:
        """
        Check if stop loss should be triggered.

        Args:
            entry_price: Position entry price
            current_price: Current market price

        Returns:
            True if stop loss should be triggered
        """
        if entry_price <= 0:
            return False

        pnl_pct = (current_price - entry_price) / entry_price

        # 2% hard stop loss
        if abs(pnl_pct) > self._config.stop_loss_pct:
            return True

        # Z-Score > 4.0 (model failure)
        if abs(self._zscore) > self._config.zscore_stop:
            return True

        return False

    def reset(self) -> None:
        """Reset indicator to initial state."""
        self._kalman.reset()
        self._price_history.clear()
        self._kalman_history.clear()
        self._hurst = 0.5
        self._kalman_price = None
        self._zscore = 0.0
        self._slope = 0.0
        self._market_state = MarketState.UNKNOWN
        self._signal = Signal.HOLD
        self._last_price = None
        self.reset_warmup()
