"""
Core algorithms for EMA Crossover strategy.

Exchange-agnostic version: symbols default to empty list.
"""

from dataclasses import dataclass, field
from typing import List

import numpy as np


def calculate_ema(prices: np.ndarray, period: int) -> np.ndarray:
    """Calculate EMA array using SMA seed + recursive EMA formula.

    Args:
        prices: Array of close prices.
        period: EMA period.

    Returns:
        Array of same length with NaN before initial period.
    """
    n = len(prices)
    ema = np.full(n, np.nan)

    if n < period:
        return ema

    # SMA seed
    ema[period - 1] = np.mean(prices[:period])

    k = 2.0 / (period + 1)
    for i in range(period, n):
        ema[i] = prices[i] * k + ema[i - 1] * (1 - k)

    return ema


def calculate_ema_single(prev_ema: float, price: float, period: int) -> float:
    """Single-step EMA update for live/streaming."""
    k = 2.0 / (period + 1)
    return price * k + prev_ema * (1 - k)


@dataclass
class EMAConfig:
    """Configuration for the EMA Crossover strategy."""

    # Trading pairs (exchange-agnostic: no default)
    symbols: List[str] = field(default_factory=list)

    # Core parameters
    timeframe: str = "15m"
    fast_period: int = 12
    slow_period: int = 26

    # Risk management
    position_size_pct: float = 0.10
    stop_loss_pct: float = 0.05
    daily_loss_limit: float = 0.03
