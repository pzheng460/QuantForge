"""
Core algorithms for Dual Regime strategy (Momentum + Bollinger Band Mean Reversion).

Strategy logic:
- ADX >= adx_threshold (trending market): Use Momentum strategy
  * Entry: ROC > threshold + EMA fast > slow + price > EMA trend + volume confirm
  * Exit: ROC reversal or EMA crossover reversal or ATR trailing stop
- ADX < adx_threshold (ranging market): Use Bollinger Band mean reversion
  * Entry: price < lower band → BUY, price > upper band → SELL
  * Exit: price crosses middle band
- Regime switches cause position closure before new strategy activation
"""

from dataclasses import dataclass, field
from typing import List

import numpy as np

# Import calculation functions from existing strategies
from strategy.strategies.momentum.core import (
    calculate_adx,
    calculate_atr,
    calculate_ema,
    calculate_roc,
    calculate_sma,
)


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


@dataclass
class DualRegimeConfig:
    """Configuration for the Dual Regime strategy (Momentum + Bollinger Band)."""

    # Trading pairs (exchange-agnostic: no default)
    symbols: List[str] = field(default_factory=list)

    # Timeframe
    timeframe: str = "1h"

    # === Regime Detection (ADX) ===
    adx_period: int = 14
    adx_trend_threshold: float = 25.0  # ADX >= this = trending (momentum), < this = ranging (BB)

    # === Momentum Strategy Parameters (ADX >= threshold) ===
    # Momentum indicator
    roc_period: int = 5              # Mesa #3 validated
    roc_threshold: float = 0.02       # Mesa #3 validated

    # Trend confirmation EMAs
    ema_fast: int = 8                 # Mesa #3 validated
    ema_slow: int = 15                # Mesa #3 validated
    ema_trend: int = 50               # Mesa #3 validated

    # Volatility filter / stop
    atr_period: int = 14
    atr_multiplier: float = 1.5       # Mesa #3 validated

    # Volume confirmation
    volume_sma_period: int = 20
    volume_threshold: float = 1.2

    # === Bollinger Band Strategy Parameters (ADX < threshold) ===
    bb_period: int = 20               # To be optimized [15, 20, 30]
    bb_std: float = 2.0               # To be optimized [1.5, 2.0, 2.5]

    # === Risk Management (Shared) ===
    position_size_pct: float = 0.20   # 20% per trade
    stop_loss_pct: float = 0.03       # Mesa #3 validated, to be tested with [0.02, 0.03]
    daily_loss_limit: float = 0.03    # 3% daily loss circuit breaker