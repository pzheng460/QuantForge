"""
Hurst-Kalman Trading Strategy

A quantitative trading strategy that uses:
- Hurst Exponent (R/S Analysis) for market state identification
- Kalman Filter for true value estimation
"""

from strategy.strategies.hurst_kalman.core import (
    HurstKalmanConfig,
    KalmanFilter1D,
    calculate_hurst,
    calculate_zscore,
)

__all__ = [
    "calculate_hurst",
    "calculate_zscore",
    "KalmanFilter1D",
    "HurstKalmanConfig",
]
