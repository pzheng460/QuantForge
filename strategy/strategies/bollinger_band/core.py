"""
Core algorithms for Bollinger Band Mean Reversion strategy.

Exchange-agnostic version: symbols default to empty list.
"""

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np


def calculate_bollinger_bands(
    prices: np.ndarray, period: int = 20, multiplier: float = 2.0
) -> tuple:
    """Calculate Bollinger Bands over a price array.

    Returns:
        (sma, upper_band, lower_band) - all same length as prices,
        with NaN for positions before period bars.
    """
    n = len(prices)
    sma = np.full(n, np.nan)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)

    for i in range(period - 1, n):
        window = prices[i - period + 1 : i + 1]
        m = np.mean(window)
        s = np.std(window, ddof=0)
        sma[i] = m
        upper[i] = m + multiplier * s
        lower[i] = m - multiplier * s

    return sma, upper, lower


def calculate_bb_single(prices_window: np.ndarray, multiplier: float = 2.0) -> tuple:
    """Single-step Bollinger Band calculation for live/streaming."""
    m = float(np.mean(prices_window))
    s = float(np.std(prices_window, ddof=0))
    return m, m + multiplier * s, m - multiplier * s


@dataclass
class BBConfig:
    """Configuration for the Bollinger Band Mean Reversion strategy."""

    # Trading pairs (exchange-agnostic: no default)
    symbols: List[str] = field(default_factory=list)

    # Core parameters
    timeframe: str = "15m"
    bb_period: int = 20
    bb_multiplier: float = 2.0

    # Mean reversion exit threshold
    exit_threshold: float = 0.3

    # Trend bias: None, "long_only", "short_only", "auto"
    trend_bias: Optional[str] = None

    # Multiplier for the trend-detection SMA window
    trend_sma_multiplier: int = 3

    # Risk management
    position_size_pct: float = 0.10
    stop_loss_pct: float = 0.05
    daily_loss_limit: float = 0.03
