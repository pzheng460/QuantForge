"""
Core algorithms for VWAP Mean Reversion strategy.

Exchange-agnostic version: symbols default to empty list.
"""

from dataclasses import dataclass, field
from typing import List

import numpy as np
import pandas as pd


def calculate_vwap(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    timestamps: pd.DatetimeIndex | None = None,
) -> np.ndarray:
    """Calculate cumulative VWAP with daily reset at 00:00 UTC.

    Uses typical price = (H + L + C) / 3.

    Args:
        highs: Array of high prices.
        lows: Array of low prices.
        closes: Array of close prices.
        volumes: Array of volumes.
        timestamps: Optional DatetimeIndex for day-boundary detection.
            If None, no daily reset is applied.

    Returns:
        Array of VWAP values (same length as input).
    """
    n = len(closes)
    vwap = np.full(n, np.nan)

    typical_price = (highs + lows + closes) / 3.0
    cum_tp_vol = 0.0
    cum_vol = 0.0
    prev_day = None

    for i in range(n):
        # Detect day boundary for daily reset
        if timestamps is not None:
            current_day = timestamps[i].date()
            if prev_day is not None and current_day != prev_day:
                cum_tp_vol = 0.0
                cum_vol = 0.0
            prev_day = current_day

        vol = volumes[i]
        if vol <= 0:
            vwap[i] = vwap[i - 1] if i > 0 and not np.isnan(vwap[i - 1]) else np.nan
            continue

        cum_tp_vol += typical_price[i] * vol
        cum_vol += vol

        if cum_vol > 0:
            vwap[i] = cum_tp_vol / cum_vol

    return vwap


def calculate_rsi(prices: np.ndarray, period: int = 14) -> np.ndarray:
    """Calculate RSI using Wilder's smoothing method.

    Args:
        prices: Array of close prices.
        period: RSI lookback period.

    Returns:
        Array of RSI values (same length, NaN before period).
    """
    n = len(prices)
    rsi = np.full(n, np.nan)

    if n < period + 1:
        return rsi

    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # SMA seed for first period
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    if avg_loss < 1e-10:
        rsi[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi[period] = 100.0 - 100.0 / (1.0 + rs)

    # Wilder's smoothing
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss < 1e-10:
            rsi[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i + 1] = 100.0 - 100.0 / (1.0 + rs)

    return rsi


def calculate_vwap_zscore(
    prices: np.ndarray, vwap: np.ndarray, std_window: int
) -> np.ndarray:
    """Calculate rolling Z-score of price deviation from VWAP.

    Args:
        prices: Array of close prices.
        vwap: Array of VWAP values.
        std_window: Rolling window for standard deviation.

    Returns:
        Array of Z-score values (same length, NaN before std_window).
    """
    n = len(prices)
    zscore = np.full(n, np.nan)

    deviation = prices - vwap

    for i in range(std_window - 1, n):
        window = deviation[i - std_window + 1 : i + 1]
        valid = window[~np.isnan(window)]
        if len(valid) < 2:
            continue
        std = np.std(valid, ddof=0)
        if std > 1e-10:
            zscore[i] = deviation[i] / std

    return zscore


@dataclass
class VWAPConfig:
    """Configuration for the VWAP Mean Reversion strategy."""

    # Trading pairs (exchange-agnostic: no default)
    symbols: List[str] = field(default_factory=list)

    # Core parameters
    timeframe: str = "5m"
    std_window: int = 200  # Rolling sigma window
    rsi_period: int = 14

    # Entry/exit thresholds
    zscore_entry: float = 2.0  # Entry threshold
    zscore_exit: float = 0.0  # TP at VWAP (Z ~ 0)
    zscore_stop: float = 3.5  # Model failure stop

    # RSI filters
    rsi_oversold: float = 30.0  # RSI long filter
    rsi_overbought: float = 70.0  # RSI short filter

    # Risk management
    position_size_pct: float = 0.20  # 20% per trade
    stop_loss_pct: float = 0.03  # 3% hard stop
    daily_loss_limit: float = 0.03
