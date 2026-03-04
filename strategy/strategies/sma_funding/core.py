"""
Core configuration for SMA Funding dual-leg strategy.

Exchange-agnostic version: symbols default to empty list.

Strategy overview:
  Two legs run simultaneously on the same symbol:

  Leg 1 — SMA Trend (80% of capital, dominant):
    Long when daily close > daily SMA100, flat otherwise.
    Protected by ATR trailing stop and hard stop loss.
    Signal only evaluated on daily-close bars (last 1h bar of each day).

  Leg 2 — Funding Arb (20% of capital, secondary):
    Short perp when avg_funding_rate > min_funding_rate AND trend is flat.
    Collects positive funding payments delta-neutrally (conceptual spot hedge).
    Exits when funding turns adverse or hard stop is hit.

  Net position = leg1 + leg2:
    Trend long + Arb flat  → net LONG  (80%)
    Trend long + Arb short → net LONG  (60% effective, backtest uses 80%)
    Trend flat + Arb short → net SHORT (20%)
    Both flat              → HOLD

  In backtest, position_size_pct controls the dominant (trend) leg size.
  Live trading: trend_size_pct and arb_size_pct enable precise sizing.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class SMAFundingConfig:
    """Configuration for the SMA Funding dual-leg strategy."""

    # Trading pairs (exchange-agnostic: no default)
    symbols: List[str] = field(default_factory=list)

    # Timeframe for OHLCV data
    timeframe: str = "1h"

    # ------- SMA parameters (Leg 1: trend) -------
    # SMA period in daily bars (applied to daily-resampled close)
    sma_period: int = 100

    # ------- ATR parameters (Leg 1: trailing stop) -------
    atr_period: int = 14
    atr_trail_mult: float = 2.0

    # ------- Funding rate parameters (Leg 2: arb) -------
    # Minimum average funding rate to open arb short (per 8h settlement)
    min_funding_rate: float = 0.0003  # 0.03%
    # Lookback period for funding rate smoothing (number of 8h settlements)
    funding_lookback: int = 24  # 24 x 8h = 8 days

    # ------- Position sizing -------
    # Trend leg fraction of capital (used in live trading)
    trend_size_pct: float = 0.80  # 80%
    # Arb leg fraction of capital (used in live trading)
    arb_size_pct: float = 0.20  # 20%
    # Backtest position size (maps to trend leg; arb leg sizing is approximate)
    position_size_pct: float = 0.80

    # ------- Stop losses -------
    # Hard stop for trend leg (long position)
    trend_stop_loss_pct: float = 0.03  # 3%
    # Hard stop for arb leg (short position; adverse = price rises)
    arb_stop_loss_pct: float = 0.02  # 2%

    # ------- Daily loss circuit breaker -------
    daily_loss_limit: float = 0.03  # 3%
