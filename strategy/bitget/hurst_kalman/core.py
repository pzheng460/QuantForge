"""
Core algorithms for Hurst-Kalman trading strategy.

This module contains pure numpy implementations that can be shared
between backtesting (vectorbt) and live trading (NexusTrader).
"""

from collections import deque
from dataclasses import dataclass, field
from typing import List

import numpy as np


def calculate_hurst(prices: np.ndarray, window: int = 100) -> float:
    """
    Calculate the Hurst Exponent using R/S (Rescaled Range) Analysis.

    The Hurst Exponent H indicates:
    - H < 0.45: Mean-reverting (anti-persistent)
    - 0.45 <= H <= 0.55: Random walk
    - H > 0.55: Trending (persistent)

    Args:
        prices: Array of price data (at least `window` elements)
        window: Number of periods to use for calculation

    Returns:
        Hurst exponent value between 0 and 1
    """
    if len(prices) < window:
        return 0.5  # Default to random walk if insufficient data

    # Use the most recent `window` prices
    series = prices[-window:]

    # Calculate log returns
    returns = np.diff(np.log(series))

    if len(returns) < 10:
        return 0.5

    # R/S analysis with multiple sub-periods
    n_values = []
    rs_values = []

    # Use different sub-period sizes
    for n in [10, 20, 40, 60, 80]:
        if n > len(returns):
            continue

        # Number of sub-periods
        num_periods = len(returns) // n

        if num_periods < 1:
            continue

        rs_list = []
        for i in range(num_periods):
            sub_returns = returns[i * n : (i + 1) * n]

            # Mean-adjusted cumulative sum
            mean_return = np.mean(sub_returns)
            adjusted = sub_returns - mean_return
            cumsum = np.cumsum(adjusted)

            # Range
            r = np.max(cumsum) - np.min(cumsum)

            # Standard deviation
            s = np.std(sub_returns, ddof=1)

            if s > 1e-10:  # Avoid division by zero
                rs_list.append(r / s)

        if rs_list:
            n_values.append(n)
            rs_values.append(np.mean(rs_list))

    if len(n_values) < 2:
        return 0.5

    # Linear regression in log-log space to find H
    log_n = np.log(n_values)
    log_rs = np.log(rs_values)

    # H is the slope of the regression line
    slope, _ = np.polyfit(log_n, log_rs, 1)

    # Clamp to valid range [0, 1]
    return float(np.clip(slope, 0.0, 1.0))


class KalmanFilter1D:
    """
    One-dimensional Kalman Filter for price estimation.

    This filter separates observed price into true value and noise,
    providing a "zero-lag" moving average superior to SMA/EMA.
    """

    def __init__(self, R: float = 0.1, Q: float = 1e-5):
        """
        Initialize the Kalman Filter.

        Args:
            R: Measurement noise covariance (higher = trust model more)
            Q: Process noise covariance (higher = allow faster state changes)
        """
        self.R = R  # Measurement noise
        self.Q = Q  # Process noise
        self.x: float | None = None  # State estimate
        self.P: float = 1.0  # Estimate covariance

        # History for slope calculation
        self._history: deque[float] = deque(maxlen=20)

    def update(self, observation: float) -> float:
        """
        Update the filter with a new observation.

        Args:
            observation: The observed price value

        Returns:
            The filtered (estimated) value
        """
        if self.x is None:
            # Initialize with first observation
            self.x = observation
            self._history.append(self.x)
            return self.x

        # Prediction step
        x_pred = self.x
        P_pred = self.P + self.Q

        # Update step
        K = P_pred / (P_pred + self.R)  # Kalman gain
        self.x = x_pred + K * (observation - x_pred)
        self.P = (1 - K) * P_pred

        # Store history
        self._history.append(self.x)

        return self.x

    def get_slope(self, lookback: int = 5) -> float:
        """
        Calculate the slope of recent Kalman estimates.

        Args:
            lookback: Number of recent estimates to use

        Returns:
            Slope value (positive = uptrend, negative = downtrend)
        """
        if len(self._history) < 2:
            return 0.0

        # Use available history up to lookback
        n = min(lookback, len(self._history))
        values = list(self._history)[-n:]

        if len(values) < 2:
            return 0.0

        # Simple linear regression slope
        x = np.arange(len(values))
        y = np.array(values)

        # slope = cov(x,y) / var(x)
        x_mean = np.mean(x)
        y_mean = np.mean(y)

        numerator = np.sum((x - x_mean) * (y - y_mean))
        denominator = np.sum((x - x_mean) ** 2)

        if denominator < 1e-10:
            return 0.0

        return float(numerator / denominator)

    def reset(self) -> None:
        """Reset the filter to initial state."""
        self.x = None
        self.P = 1.0
        self._history.clear()

    @property
    def estimate(self) -> float | None:
        """Current state estimate."""
        return self.x


def calculate_zscore(
    prices: np.ndarray, kalman_prices: np.ndarray, window: int = 50
) -> float:
    """
    Calculate the Z-Score of price deviation from Kalman estimate.

    Args:
        prices: Array of observed prices
        kalman_prices: Array of Kalman-filtered prices
        window: Rolling window for standard deviation calculation

    Returns:
        Z-Score value
    """
    if len(prices) < window or len(kalman_prices) < window:
        return 0.0

    # Calculate deviations
    deviations = prices[-window:] - kalman_prices[-window:]

    std = np.std(deviations)
    if std < 1e-10:
        return 0.0

    current_deviation = prices[-1] - kalman_prices[-1]
    return float(current_deviation / std)


@dataclass
class HurstKalmanConfig:
    """Configuration for the Hurst-Kalman strategy."""

    # Trading pairs
    symbols: List[str] = field(default_factory=lambda: ["BTCUSDT-PERP.BITGET"])

    # Core parameters
    timeframe: str = "15m"
    hurst_window: int = 100
    kalman_R: float = 0.1
    kalman_Q: float = 1e-5
    zscore_window: int = 50

    # Trading thresholds
    mean_reversion_threshold: float = 0.45
    trend_threshold: float = 0.55
    zscore_entry: float = 2.0
    zscore_stop: float = 4.0

    # Risk management
    position_size_pct: float = 0.10  # 10%
    stop_loss_pct: float = 0.02  # 2%
    daily_loss_limit: float = 0.03  # 3%
    min_expected_profit: float = 0.001  # 0.1% to cover fees
