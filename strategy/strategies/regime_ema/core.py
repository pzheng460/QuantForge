"""
Core algorithms for EMA Crossover + Regime Filter strategy.

Exchange-agnostic version: symbols default to empty list.

The regime filter adds market-state detection (ATR + ADX) on top of the
classic EMA crossover to avoid trading during ranging / choppy markets.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List

import numpy as np


# ---------------------------------------------------------------------------
# Market regime classification
# ---------------------------------------------------------------------------

class MarketRegime(Enum):
    """Detected market regime."""

    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

def calculate_ema(prices: np.ndarray, period: int) -> np.ndarray:
    """Calculate EMA array using SMA seed + recursive EMA formula."""
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


def calculate_atr(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int,
) -> np.ndarray:
    """Calculate Average True Range (ATR) using Wilder's smoothing.

    Returns array of same length; NaN before period.
    """
    n = len(closes)
    atr = np.full(n, np.nan)
    if n < 2:
        return atr

    tr = np.empty(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hc, lc)

    if n < period:
        return atr

    # SMA seed
    atr[period - 1] = np.mean(tr[:period])

    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    return atr


def calculate_adx(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int,
) -> np.ndarray:
    """Calculate ADX (Average Directional Index).

    Uses Wilder's smoothing.  Returns array of same length; NaN before
    enough data is available (~2*period bars).
    """
    n = len(closes)
    adx = np.full(n, np.nan)
    if n < period + 1:
        return adx

    # +DM / -DM
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)
    tr = np.zeros(n)

    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]

        if up_move > down_move and up_move > 0:
            plus_dm[i] = up_move
        if down_move > up_move and down_move > 0:
            minus_dm[i] = down_move

        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hc, lc)

    # Wilder's smoothing for ATR, +DM, -DM
    smoothed_tr = np.full(n, np.nan)
    smoothed_plus = np.full(n, np.nan)
    smoothed_minus = np.full(n, np.nan)

    idx = period  # first valid smoothed index
    smoothed_tr[idx] = np.sum(tr[1 : idx + 1])
    smoothed_plus[idx] = np.sum(plus_dm[1 : idx + 1])
    smoothed_minus[idx] = np.sum(minus_dm[1 : idx + 1])

    for i in range(idx + 1, n):
        smoothed_tr[i] = smoothed_tr[i - 1] - smoothed_tr[i - 1] / period + tr[i]
        smoothed_plus[i] = (
            smoothed_plus[i - 1] - smoothed_plus[i - 1] / period + plus_dm[i]
        )
        smoothed_minus[i] = (
            smoothed_minus[i - 1] - smoothed_minus[i - 1] / period + minus_dm[i]
        )

    # DI+ / DI- / DX
    dx = np.full(n, np.nan)
    for i in range(idx, n):
        if smoothed_tr[i] < 1e-10:
            continue
        di_plus = 100.0 * smoothed_plus[i] / smoothed_tr[i]
        di_minus = 100.0 * smoothed_minus[i] / smoothed_tr[i]
        di_sum = di_plus + di_minus
        if di_sum < 1e-10:
            dx[i] = 0.0
        else:
            dx[i] = 100.0 * abs(di_plus - di_minus) / di_sum

    # ADX = smoothed DX
    first_adx = idx + period
    if first_adx >= n:
        return adx

    # SMA seed of DX for ADX
    dx_window = dx[idx:first_adx]
    valid_dx = dx_window[~np.isnan(dx_window)]
    if len(valid_dx) == 0:
        return adx
    adx[first_adx - 1] = np.mean(valid_dx)

    for i in range(first_adx, n):
        if np.isnan(dx[i]):
            adx[i] = adx[i - 1] if not np.isnan(adx[i - 1]) else np.nan
        elif np.isnan(adx[i - 1]):
            adx[i] = dx[i]
        else:
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    return adx


def classify_regime(
    atr_val: float,
    atr_mean: float,
    adx_val: float,
    fast_ema: float,
    slow_ema: float,
    trend_atr_threshold: float,
    ranging_atr_threshold: float,
    adx_trend_threshold: float,
) -> MarketRegime:
    """Classify the current market regime.

    Rules:
    1. HIGH_VOLATILITY: ATR > 2 * atr_mean (regardless of ADX).
    2. TRENDING_UP / DOWN: ADX > adx_trend_threshold AND ATR > atr_mean * trend_atr_threshold.
       Direction from fast vs slow EMA.
    3. RANGING: everything else (including low ATR/ADX).
    """
    if atr_mean < 1e-10:
        return MarketRegime.RANGING

    atr_ratio = atr_val / atr_mean

    # Extreme volatility check
    if atr_ratio > 2.0:
        return MarketRegime.HIGH_VOLATILITY

    # Trending conditions
    if adx_val > adx_trend_threshold and atr_ratio > trend_atr_threshold:
        if fast_ema > slow_ema:
            return MarketRegime.TRENDING_UP
        else:
            return MarketRegime.TRENDING_DOWN

    # Everything else = ranging
    return MarketRegime.RANGING


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class RegimeEMAConfig:
    """Configuration for the EMA Crossover + Regime Filter strategy."""

    # Trading pairs (exchange-agnostic: no default)
    symbols: List[str] = field(default_factory=list)

    # Core parameters
    timeframe: str = "1h"

    # EMA parameters
    fast_period: int = 20
    slow_period: int = 50

    # Regime detection parameters
    atr_period: int = 14
    regime_lookback: int = 50       # lookback window for ATR mean
    trend_atr_threshold: float = 1.5
    ranging_atr_threshold: float = 0.8
    adx_period: int = 14
    adx_trend_threshold: float = 25.0

    # Risk management
    position_size_pct: float = 0.20
    stop_loss_pct: float = 0.03
    daily_loss_limit: float = 0.03
