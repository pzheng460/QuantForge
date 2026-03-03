"""
Core configuration for Funding Rate Arbitrage (delta-neutral) strategy.

Exchange-agnostic version: symbols default to empty list.

Strategy overview:
  - Delta-neutral funding rate arbitrage: short perp to collect positive
    funding payments while hedging with a conceptual spot long
  - Enter before funding settlement when smoothed funding rate exceeds
    a minimum threshold
  - Exit after settlement to minimize directional exposure window
  - Simpler signal logic than the existing funding_rate strategy:
    no trend filter, no adverse move check — pure funding arb
  - Live trading: TODO (requires simultaneous spot + perp orders)
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class FundingArbConfig:
    """Configuration for the Funding Rate Arbitrage (delta-neutral) strategy."""

    # Trading pairs (exchange-agnostic: no default)
    symbols: List[str] = field(default_factory=list)

    # Timeframe for OHLCV data
    timeframe: str = "1h"

    # ------- Funding rate parameters -------
    # Minimum average funding rate to open a position (per 8h settlement)
    min_funding_rate: float = 0.0003  # 0.03%
    # Maximum funding rate — extreme values indicate market stress, skip
    max_funding_rate: float = 0.01  # 1.0%
    # Lookback period for funding rate smoothing (number of 8h settlements)
    funding_lookback: int = 24  # 24 x 8h = 8 days

    # ------- Position management -------
    # Capital fraction per trade (delta-neutral = lower risk → larger size)
    position_size_pct: float = 0.50  # 50%
    # Hard stop-loss on the short perp leg
    stop_loss_pct: float = 0.02  # 2%
    # Daily loss circuit breaker
    daily_loss_limit: float = 0.02  # 2%

    # ------- Timing parameters -------
    # Enter position this many hours before the next funding settlement
    hours_before_funding: int = 1
    # Exit position this many hours after funding settlement
    hours_after_funding: int = 1
