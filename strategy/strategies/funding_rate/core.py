"""
Core algorithms for Funding Rate Arbitrage strategy.

Exchange-agnostic version: symbols default to empty list.

Strategy overview:
  - Monitor perpetual contract funding rates
  - When funding rate is positive (longs pay shorts), open short position
    to collect the funding payment
  - Enter before funding settlement, exit after settlement to minimize
    directional price exposure
  - Use SMA trend filter to avoid shorting into strong uptrends
  - Hard stop-loss to cap drawdown from adverse price moves
"""

from dataclasses import dataclass, field
from typing import List

import numpy as np


def calculate_sma(prices: np.ndarray, period: int) -> np.ndarray:
    """Calculate Simple Moving Average.

    Args:
        prices: Array of close prices.
        period: SMA lookback period.

    Returns:
        Array of same length with NaN before initial period.
    """
    n = len(prices)
    sma = np.full(n, np.nan)
    if n < period:
        return sma
    # Cumulative sum trick for efficient SMA
    cumsum = np.cumsum(prices)
    sma[period - 1:] = (cumsum[period - 1:] - np.concatenate(([0.0], cumsum[:-period]))) / period
    return sma


@dataclass
class FundingRateConfig:
    """Configuration for the Funding Rate Arbitrage strategy."""

    # Trading pairs (exchange-agnostic: no default)
    symbols: List[str] = field(default_factory=list)

    # Timeframe for OHLCV data
    timeframe: str = "1h"

    # ------- Funding rate parameters -------
    # Minimum funding rate to justify opening a position (per 8h settlement)
    min_funding_rate: float = 0.0005  # 0.05%
    # Maximum funding rate — extreme values indicate market stress, skip
    max_funding_rate: float = 0.01  # 1.0%
    # How many past funding periods to average for smoothing
    funding_lookback: int = 24  # 24 x 8h = 8 days

    # ------- Price protection parameters -------
    # SMA period for trend detection (in bars of `timeframe`)
    price_sma_period: int = 50
    # Maximum tolerated adverse price move while holding position
    max_adverse_move_pct: float = 0.02  # 2%

    # ------- Position management -------
    # Capital fraction per trade (funding arb = lower risk → larger size)
    position_size_pct: float = 0.30  # 30%
    # Hard stop-loss percentage
    stop_loss_pct: float = 0.03  # 3%
    # Daily loss circuit breaker
    daily_loss_limit: float = 0.02  # 2%

    # ------- Timing parameters -------
    # Enter position this many hours before the next funding settlement
    hours_before_funding: int = 2
    # Exit position this many hours after funding settlement
    hours_after_funding: int = 1
