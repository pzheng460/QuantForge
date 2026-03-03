"""
Core configuration for SMA Trend strategy.

Exchange-agnostic version: symbols default to empty list.

Strategy overview:
  - Long-only daily SMA100 trend following on 1h bars
  - Buy when close > daily SMA, close when close < daily SMA
  - No shorting — designed for spot or long-only perp exposure
  - No stop loss — exits purely on SMA crossunder
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class SMATrendConfig:
    """Configuration for the SMA Trend strategy."""

    # Trading pairs (exchange-agnostic: no default)
    symbols: List[str] = field(default_factory=list)

    # Timeframe for OHLCV data
    timeframe: str = "1h"

    # ------- SMA parameters -------
    # SMA period in daily bars (applied to daily-resampled close)
    sma_period: int = 100

    # ------- Position management -------
    # Capital fraction per trade
    position_size_pct: float = 1.0  # 100% — long-only trend following
    # Daily loss circuit breaker
    daily_loss_limit: float = 0.05  # 5%
