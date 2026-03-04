"""
Core configuration for Dynamic Grid strategy.

Extends GridConfig with volatility-adaptive leverage parameters.

Strategy overview:
  Classic grid trading (SMA + ATR bounds, level-based entry/exit) enhanced
  with a dynamic leverage multiplier that adjusts aggressiveness based on:

  1. Volatility regime (ATR/price ratio):
       < vol_low_threshold (1%)  → low_vol_mult  (4.0x, max aggression)
       < vol_med_threshold (2%)  → med_vol_mult  (2.0x)
       < vol_high_threshold (3%) → normal_vol_mult (1.0x)
       ≥ vol_high_threshold      → high_vol_mult (0.5x, most conservative)

  2. ADX trend filter:
       ADX > adx_trend_threshold → reduce leverage_mult by trend_leverage_reduction (50%)
       (Strong trends are bad for grid trading.)

  3. Volatility expansion filter:
       ATR > SMA(ATR, atr_sma_period) → reduce leverage_mult by vol_expanding_reduction (25%)
       (Expanding volatility adds directional risk.)

  In backtest, leverage_mult adjusts the effective entry threshold:
    High leverage → fewer grid lines needed to enter (more aggressive)
    Low leverage  → more grid lines needed (more conservative)

  In live trading, calculate_position_size_fn uses:
    effective_size = base_position_size_pct * effective_leverage_mult
"""

from dataclasses import dataclass, field
from typing import List

from strategy.strategies.grid_trading.core import GridConfig


@dataclass
class DynamicGridConfig(GridConfig):
    """Configuration for the Dynamic Grid strategy.

    Inherits all GridConfig fields and adds dynamic leverage parameters.
    Override key GridConfig defaults for a standalone dynamic grid style.
    """

    # Trading pairs (override parent default)
    symbols: List[str] = field(default_factory=list)

    # Override GridConfig position sizing defaults
    position_size_pct: float = 0.20  # 20% base per trade
    stop_loss_pct: float = 0.05  # 5% hard stop

    # ------- Dynamic leverage parameters -------
    # Baseline leverage multiplier (applied when vol regime is "normal")
    base_leverage_mult: float = 2.0

    # ATR/price ratio thresholds defining volatility regimes
    vol_low_threshold: float = 0.01  # < 1% ATR/price → very low vol
    vol_med_threshold: float = 0.02  # 1-2% → low-medium vol
    vol_high_threshold: float = 0.03  # 2-3% → medium vol; > 3% → high vol

    # Leverage multipliers for each vol regime
    low_vol_mult: float = 4.0  # Very low vol → most aggressive
    med_vol_mult: float = 2.0  # Low-medium vol
    normal_vol_mult: float = 1.0  # Medium vol → standard
    high_vol_mult: float = 0.5  # High vol → most conservative

    # ------- ADX trend filter -------
    adx_period: int = 14  # ADX calculation period
    adx_trend_threshold: float = 30.0  # ADX > this → reduce leverage
    trend_leverage_reduction: float = 0.5  # Fractional reduction (50%)

    # ------- Volatility expansion filter -------
    # SMA period for ATR smoothing (ATR > SMA(ATR) → vol expanding)
    atr_sma_period: int = 20
    vol_expanding_reduction: float = 0.25  # Fractional reduction (25%)

    # ------- Daily loss circuit breaker -------
    daily_loss_limit: float = 0.02  # 2% daily loss → pause for rest of day
