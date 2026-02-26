"""
Core algorithms for Multi-Timeframe Momentum strategy.

Exchange-agnostic version: symbols default to empty list.

Strategy logic:
- Entry (all must hold):
  1. ROC > threshold  (momentum present)
  2. EMA fast > EMA slow  (short-term trend alignment)
  3. Price > EMA trend  (long-term trend alignment; reversed for short)
  4. Volume > SMA_vol * volume_threshold  (volume confirmation)

- Exit (any one triggers):
  1. ROC reversal (momentum dies)
  2. EMA fast crosses below EMA slow (trend reversal)
  3. ATR trailing stop (price moves against by ATR * multiplier)

- Long and short are symmetric.
"""

from dataclasses import dataclass, field
from typing import List

import numpy as np


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def calculate_ema(prices: np.ndarray, period: int) -> np.ndarray:
    """Calculate EMA array using SMA seed + recursive formula."""
    n = len(prices)
    ema = np.full(n, np.nan)
    if n < period:
        return ema
    ema[period - 1] = np.mean(prices[:period])
    k = 2.0 / (period + 1)
    for i in range(period, n):
        ema[i] = prices[i] * k + ema[i - 1] * (1 - k)
    return ema


def calculate_ema_single(prev_ema: float, price: float, period: int) -> float:
    """Single-step EMA update for live/streaming."""
    k = 2.0 / (period + 1)
    return price * k + prev_ema * (1 - k)


def calculate_roc(prices: np.ndarray, period: int) -> np.ndarray:
    """Calculate Rate of Change: (price - price[n-period]) / price[n-period]."""
    n = len(prices)
    roc = np.full(n, np.nan)
    for i in range(period, n):
        prev = prices[i - period]
        if prev != 0:
            roc[i] = (prices[i] - prev) / prev
    return roc


def calculate_atr(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int,
) -> np.ndarray:
    """Calculate Average True Range using Wilder smoothing."""
    n = len(closes)
    atr = np.full(n, np.nan)
    if n < period + 1:
        return atr

    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hc, lc)

    # SMA seed for first period
    atr[period] = np.mean(tr[1 : period + 1])
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def calculate_sma(values: np.ndarray, period: int) -> np.ndarray:
    """Calculate Simple Moving Average."""
    n = len(values)
    sma = np.full(n, np.nan)
    for i in range(period - 1, n):
        sma[i] = np.mean(values[i - period + 1 : i + 1])
    return sma


def calculate_adx(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """Calculate Average Directional Index (ADX) using Wilder smoothing.

    ADX measures trend strength regardless of direction.
    High ADX (>25) = strong trend, low ADX (<20) = ranging/choppy.
    """
    n = len(closes)
    adx = np.full(n, np.nan)
    if n < 2 * period + 1:
        return adx

    # True Range, +DM, -DM
    tr = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)

    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hc, lc)

        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]

        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0

    # Wilder smoothed TR, +DM, -DM
    atr_s = np.zeros(n)
    plus_dm_s = np.zeros(n)
    minus_dm_s = np.zeros(n)

    atr_s[period] = np.sum(tr[1 : period + 1])
    plus_dm_s[period] = np.sum(plus_dm[1 : period + 1])
    minus_dm_s[period] = np.sum(minus_dm[1 : period + 1])

    for i in range(period + 1, n):
        atr_s[i] = atr_s[i - 1] - atr_s[i - 1] / period + tr[i]
        plus_dm_s[i] = plus_dm_s[i - 1] - plus_dm_s[i - 1] / period + plus_dm[i]
        minus_dm_s[i] = minus_dm_s[i - 1] - minus_dm_s[i - 1] / period + minus_dm[i]

    # +DI, -DI, DX
    dx = np.full(n, np.nan)
    for i in range(period, n):
        if atr_s[i] > 0:
            plus_di = 100.0 * plus_dm_s[i] / atr_s[i]
            minus_di = 100.0 * minus_dm_s[i] / atr_s[i]
            di_sum = plus_di + minus_di
            dx[i] = 100.0 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0

    # ADX = smoothed DX
    first_adx = 2 * period
    if first_adx < n:
        valid_dx = [dx[i] for i in range(period, first_adx + 1) if not np.isnan(dx[i])]
        if len(valid_dx) >= period:
            adx[first_adx] = np.mean(valid_dx[:period])
            for i in range(first_adx + 1, n):
                if not np.isnan(dx[i]):
                    adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    return adx


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class MomentumConfig:
    """Configuration for the Multi-Timeframe Momentum strategy."""

    # Trading pairs (exchange-agnostic: no default)
    symbols: List[str] = field(default_factory=list)

    # Timeframe
    timeframe: str = "1h"

    # Momentum indicator
    roc_period: int = 14              # Rate of Change lookback
    roc_threshold: float = 0.02       # ROC > 2% considered momentum

    # Trend confirmation EMAs
    ema_fast: int = 8                 # Short-term EMA
    ema_slow: int = 21                # Medium-term EMA
    ema_trend: int = 50               # Long-term trend EMA

    # Volatility filter / stop
    atr_period: int = 14
    atr_multiplier: float = 1.5       # ATR-based trailing stop multiplier

    # Volume confirmation
    volume_sma_period: int = 20
    volume_threshold: float = 1.2     # Volume must exceed SMA * this

    # Regime filter (ADX-based)
    adx_period: int = 14
    adx_trend_threshold: float = 25.0  # ADX > this = trending, allow trades
    # When ADX < threshold, skip new entries and close existing positions

    # Risk management
    position_size_pct: float = 0.20   # 20% per trade
    stop_loss_pct: float = 0.03       # 3% hard stop
    daily_loss_limit: float = 0.03    # 3% daily loss circuit breaker
