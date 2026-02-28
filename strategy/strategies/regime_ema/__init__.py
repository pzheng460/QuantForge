"""
EMA Crossover + Regime Filter Trading Strategy

A trend-following strategy that uses:
- Fast/Slow EMA crossover for signal generation
- ATR + ADX regime detection to filter out ranging markets
- Automatic position flattening in non-trending regimes
"""

from strategy.strategies.regime_ema.core import (
    MarketRegime,
    RegimeEMAConfig,
    calculate_adx,
    calculate_atr,
    calculate_ema,
    calculate_ema_single,
    classify_regime,
)

__all__ = [
    "MarketRegime",
    "RegimeEMAConfig",
    "calculate_adx",
    "calculate_atr",
    "calculate_ema",
    "calculate_ema_single",
    "classify_regime",
]
