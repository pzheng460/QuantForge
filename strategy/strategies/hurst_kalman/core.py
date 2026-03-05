"""
Core algorithms for Hurst-Kalman Mean Reversion strategy.

Exchange-agnostic version: symbols default to empty list.
"""

from collections import deque
from dataclasses import dataclass, field
from typing import List

import numpy as np


def calculate_hurst(prices: np.ndarray, window: int = 100) -> float:
    """
    Calculate the Hurst Exponent using R/S (Rescaled Range) Analysis.

    Uses log-returns and multiple sub-division scales with averaged R/S
    per scale for robust estimation.

    H < 0.45: Mean-reverting
    0.45 <= H <= 0.55: Random walk
    H > 0.55: Trending

    Args:
        prices: Array of prices.
        window: Lookback window length.

    Returns:
        Hurst exponent (float).
    """
    if len(prices) < window:
        return 0.5

    ts = prices[-window:]
    n = len(ts)

    if n < 20:
        return 0.5

    # Use log-returns for stationarity
    log_returns = np.diff(np.log(ts))
    m = len(log_returns)  # n - 1

    # Generate multiple chunk sizes (powers of 2 that fit, plus intermediate)
    # We need at least 8 bars per chunk and at least 2 chunks per size
    sizes = []
    s = 8
    while s <= m // 2:
        sizes.append(s)
        # Add intermediate size for more data points
        mid = int(s * 1.5)
        if mid < m // 2 and mid != s:
            sizes.append(mid)
        s *= 2
    sizes = sorted(set(sizes))

    if len(sizes) < 2:
        return 0.5

    avg_log_rs = []
    log_sizes = []

    for size in sizes:
        num_chunks = m // size
        if num_chunks < 1:
            continue

        rs_values = []
        for chunk_idx in range(num_chunks):
            chunk = log_returns[chunk_idx * size : (chunk_idx + 1) * size]
            if len(chunk) < 8:
                continue

            mean = np.mean(chunk)
            deviations = chunk - mean
            cum_dev = np.cumsum(deviations)

            r = np.max(cum_dev) - np.min(cum_dev)
            s_std = np.std(chunk, ddof=1)

            if s_std > 1e-10:
                rs_values.append(r / s_std)

        if rs_values:
            # Average R/S across all chunks of this size
            avg_log_rs.append(np.log(np.mean(rs_values)))
            log_sizes.append(np.log(size))

    if len(avg_log_rs) < 2:
        return 0.5

    log_sizes = np.array(log_sizes)
    avg_log_rs = np.array(avg_log_rs)

    if len(set(log_sizes)) < 2:
        return 0.5

    coeffs = np.polyfit(log_sizes, avg_log_rs, 1)
    hurst = max(0.0, min(1.0, coeffs[0]))

    return hurst


def calculate_zscore(
    prices: np.ndarray,
    kalman_prices: np.ndarray,
    window: int = 60,
) -> float:
    """Calculate Z-Score of price deviation from Kalman estimate."""
    if len(prices) < window or len(kalman_prices) < window:
        return 0.0

    recent_prices = prices[-window:]
    recent_kalman = kalman_prices[-window:]
    deviations = recent_prices - recent_kalman
    std = np.std(deviations)

    if std < 1e-10:
        return 0.0

    return float((prices[-1] - kalman_prices[-1]) / std)


class KalmanFilter1D:
    """One-dimensional Kalman Filter for price estimation."""

    def __init__(self, R: float = 0.1, Q: float = 1e-5):
        self.R = R
        self.Q = Q
        self.x = 0.0
        self.P = 1.0
        self.initialized = False
        self._history = deque(maxlen=200)

    def update(self, measurement: float) -> float:
        if not self.initialized:
            self.x = measurement
            self.P = 1.0
            self.initialized = True
            self._history.append(self.x)
            return self.x

        # Predict
        x_pred = self.x
        P_pred = self.P + self.Q

        # Update
        K = P_pred / (P_pred + self.R)
        self.x = x_pred + K * (measurement - x_pred)
        self.P = (1 - K) * P_pred

        self._history.append(self.x)
        return self.x

    def get_slope(self, lookback: int = 5) -> float:
        if len(self._history) < lookback + 1:
            return 0.0
        recent = list(self._history)[-lookback:]
        return (recent[-1] - recent[0]) / lookback

    def reset(self):
        self.x = 0.0
        self.P = 1.0
        self.initialized = False
        self._history.clear()

    @property
    def estimate(self) -> float | None:
        """Current state estimate (None if not initialized)."""
        return self.x if self.initialized else None


@dataclass
class HurstKalmanConfig:
    """Configuration for the Hurst-Kalman Mean Reversion strategy."""

    # Trading pairs (exchange-agnostic: no default)
    symbols: List[str] = field(default_factory=list)

    # Core parameters
    timeframe: str = "15m"
    hurst_window: int = 100
    kalman_R: float = 0.2
    kalman_Q: float = 5e-05
    zscore_window: int = 60

    # Trading thresholds
    mean_reversion_threshold: float = 0.48
    trend_threshold: float = 0.60
    zscore_entry: float = 2.0
    zscore_stop: float = 3.5

    # Risk management
    position_size_pct: float = 0.10
    stop_loss_pct: float = 0.03
    daily_loss_limit: float = 0.03
