"""Shared Hurst-Kalman signal core for backtest and live trading.

This module contains HurstKalmanSignalCore — a streaming, bar-by-bar signal
generator that encapsulates ALL indicator + entry/exit + position management
logic. Both the backtest signal generator and the live indicator wrapper
delegate to this class, guaranteeing 100% code parity.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Optional

import numpy as np

if TYPE_CHECKING:
    from strategy.strategies.hurst_kalman.core import HurstKalmanConfig


def _lazy_hurst_imports():
    """Lazy import to avoid circular dependency with strategy.strategies.__init__."""
    from strategy.strategies.hurst_kalman.core import KalmanFilter1D, calculate_hurst

    return KalmanFilter1D, calculate_hurst


# Signal constants
HOLD = 0
BUY = 1
SELL = -1
CLOSE = 2


class HurstKalmanSignalCore:
    """Shared signal logic for Hurst-Kalman backtest and live trading.

    Uses Kalman filter for price estimation, Hurst exponent for market
    state classification, and z-score for entry/exit signals.
    """

    def __init__(
        self,
        config: HurstKalmanConfig,
        min_holding_bars: int = 8,
        cooldown_bars: int = 4,
        signal_confirmation: int = 1,
        only_mean_reversion: bool = True,
    ):
        self._config = config

        # Filter params
        self._min_holding_bars = min_holding_bars
        self._cooldown_bars = cooldown_bars
        self._signal_confirmation = signal_confirmation
        self._only_mean_reversion = only_mean_reversion

        # Kalman filter
        KalmanFilter1D, _ = _lazy_hurst_imports()
        self._kalman = KalmanFilter1D(R=config.kalman_R, Q=config.kalman_Q)
        self._kalman_prices: list[float] = []

        # Price history for Hurst calculation
        self._price_history: deque[float] = deque(maxlen=config.hurst_window + 50)

        # Current values
        self._hurst: float = 0.5
        self._kalman_price: Optional[float] = None
        self._zscore: float = 0.0
        self._slope: float = 0.0
        self._market_state: str = "unknown"

        # Position management state
        self.position = 0
        self.entry_bar = 0
        self.entry_price = 0.0
        self.cooldown_until = 0
        self.signal_count = {BUY: 0, SELL: 0}
        self.bar_index = 0
        self._last_trending_signal = HOLD

    def update_indicators_only(self, close: float) -> None:
        """Update all indicators without generating a trading signal."""
        self._price_history.append(close)
        self._kalman_price = self._kalman.update(close)
        self._kalman_prices.append(self._kalman_price)
        self.bar_index += 1

        if len(self._price_history) >= self._config.hurst_window:
            _, calculate_hurst = _lazy_hurst_imports()
            self._hurst = calculate_hurst(
                np.array(self._price_history), self._config.hurst_window
            )

        if len(self._kalman_prices) >= self._config.zscore_window:
            recent_prices = list(self._price_history)[-self._config.zscore_window :]
            recent_kalman = self._kalman_prices[-self._config.zscore_window :]
            deviations = np.array(recent_prices) - np.array(recent_kalman)
            std = np.std(deviations)
            if std > 1e-10:
                self._zscore = (close - self._kalman_price) / std
            else:
                self._zscore = 0.0

        self._slope = self._kalman.get_slope(lookback=5)
        self._update_market_state()

    def _update_market_state(self) -> None:
        """Update market state based on Hurst value."""
        if self._hurst < self._config.mean_reversion_threshold:
            self._market_state = "mean_reverting"
        elif self._hurst > self._config.trend_threshold:
            self._market_state = "trending"
        else:
            self._market_state = "random_walk"

    def update(self, close: float) -> int:
        """Process one bar and return a trading signal.

        Returns:
            Signal value: HOLD(0), BUY(1), SELL(-1), or CLOSE(2).
        """
        price = close
        self._price_history.append(price)
        self._kalman_price = self._kalman.update(price)
        self._kalman_prices.append(self._kalman_price)

        self.bar_index += 1
        i = self.bar_index

        # Need enough data for both Hurst and zscore
        if i <= self._config.hurst_window + self._config.zscore_window:
            return HOLD

        # Calculate Hurst
        _, calculate_hurst = _lazy_hurst_imports()
        self._hurst = calculate_hurst(
            np.array(self._price_history), self._config.hurst_window
        )

        # Calculate zscore
        recent_prices = list(self._price_history)[-self._config.zscore_window :]
        recent_kalman = self._kalman_prices[-self._config.zscore_window :]
        deviations = np.array(recent_prices) - np.array(recent_kalman)
        std = np.std(deviations)
        zscore = (price - self._kalman_price) / std if std > 1e-10 else 0.0
        self._zscore = zscore

        slope = self._kalman.get_slope(lookback=5)
        self._slope = slope

        # ---- 1. Stop loss check ----
        if self.position != 0 and self.entry_price > 0:
            is_long = self.position == 1
            if is_long:
                pnl_pct = (price - self.entry_price) / self.entry_price
            else:
                pnl_pct = (self.entry_price - price) / self.entry_price

            stop_triggered = False
            if pnl_pct < -self._config.stop_loss_pct:
                stop_triggered = True
            if abs(zscore) > self._config.zscore_stop:
                stop_triggered = True

            if stop_triggered:
                self.position = 0
                self.entry_price = 0.0
                self.entry_bar = i
                return CLOSE

        # ---- 2. Market classification ----
        self._update_market_state()
        market_state = self._market_state

        # ---- 3. Raw signal generation ----
        raw_signal = HOLD

        if market_state == "random_walk":
            raw_signal = CLOSE
        elif market_state == "mean_reverting":
            if zscore < -self._config.zscore_entry:
                raw_signal = BUY
            elif zscore > self._config.zscore_entry:
                raw_signal = SELL
            elif abs(zscore) < 0.5:
                raw_signal = CLOSE
            else:
                raw_signal = HOLD
        elif market_state == "trending":
            if price > self._kalman_price and slope > 0:
                raw_signal = BUY
            elif price < self._kalman_price and slope < 0:
                raw_signal = SELL
            elif slope * (1 if self._last_trending_signal == BUY else -1) < 0:
                raw_signal = CLOSE
            else:
                raw_signal = HOLD
            if raw_signal in (BUY, SELL):
                self._last_trending_signal = raw_signal

        # ---- 4. only_mean_reversion filter ----
        if self._only_mean_reversion and market_state != "mean_reverting":
            if raw_signal in (BUY, SELL):
                raw_signal = HOLD
            if raw_signal == CLOSE or (
                self.position != 0 and market_state != "mean_reverting"
            ):
                if self.position != 0 and i - self.entry_bar >= self._min_holding_bars:
                    self.position = 0
                    self.entry_price = 0.0
                    self.cooldown_until = i + self._cooldown_bars
                    return CLOSE
                return HOLD
            if raw_signal == HOLD:
                return HOLD

        # ---- 5. Cooldown check ----
        if i < self.cooldown_until:
            return HOLD

        # ---- 6. Signal confirmation ----
        if raw_signal == BUY:
            self.signal_count[BUY] += 1
            self.signal_count[SELL] = 0
        elif raw_signal == SELL:
            self.signal_count[SELL] += 1
            self.signal_count[BUY] = 0
        else:
            self.signal_count[BUY] = 0
            self.signal_count[SELL] = 0

        confirmed_signal = HOLD
        if self.signal_count[BUY] >= self._signal_confirmation:
            confirmed_signal = BUY
        elif self.signal_count[SELL] >= self._signal_confirmation:
            confirmed_signal = SELL
        elif raw_signal == CLOSE:
            confirmed_signal = CLOSE

        # ---- 7. Position management ----
        if confirmed_signal == BUY:
            if self.position == -1 and i - self.entry_bar >= self._min_holding_bars:
                self.position = 0
                self.entry_price = 0.0
                self.cooldown_until = i + self._cooldown_bars
                return CLOSE
            elif self.position == 0:
                self.position = 1
                self.entry_bar = i
                self.entry_price = price
                return BUY

        elif confirmed_signal == SELL:
            if self.position == 1 and i - self.entry_bar >= self._min_holding_bars:
                self.position = 0
                self.entry_price = 0.0
                self.cooldown_until = i + self._cooldown_bars
                return CLOSE
            elif self.position == 0:
                self.position = -1
                self.entry_bar = i
                self.entry_price = price
                return SELL

        elif confirmed_signal == CLOSE:
            if self.position != 0 and i - self.entry_bar >= self._min_holding_bars:
                self.position = 0
                self.entry_price = 0.0
                self.cooldown_until = i + self._cooldown_bars
                return CLOSE

        return HOLD

    def reset(self):
        """Reset all state."""
        self._kalman.reset()
        self._kalman_prices = []
        self._price_history.clear()
        self._hurst = 0.5
        self._kalman_price = None
        self._zscore = 0.0
        self._slope = 0.0
        self._market_state = "unknown"
        self.position = 0
        self.entry_bar = 0
        self.entry_price = 0.0
        self.cooldown_until = 0
        self.signal_count = {BUY: 0, SELL: 0}
        self.bar_index = 0
        self._last_trending_signal = HOLD

    # ---- Indicator value properties ----

    @property
    def hurst_value(self) -> float:
        return self._hurst

    @property
    def kalman_price_value(self) -> Optional[float]:
        return self._kalman_price

    @property
    def zscore_value(self) -> float:
        return self._zscore

    @property
    def slope_value(self) -> float:
        return self._slope

    @property
    def market_state(self) -> str:
        return self._market_state

    def get_raw_signal(self) -> int:
        """Compute raw signal from current indicator values."""
        if self._kalman_price is None:
            return HOLD

        if self._market_state == "random_walk":
            return CLOSE

        if self._market_state == "mean_reverting":
            if self._zscore < -self._config.zscore_entry:
                return BUY
            elif self._zscore > self._config.zscore_entry:
                return SELL
            elif abs(self._zscore) < 0.5:
                return CLOSE
            return HOLD

        if self._market_state == "trending":
            price = list(self._price_history)[-1] if self._price_history else None
            if price is None:
                return HOLD
            if price > self._kalman_price and self._slope > 0:
                return BUY
            elif price < self._kalman_price and self._slope < 0:
                return SELL
            return HOLD

        return HOLD
