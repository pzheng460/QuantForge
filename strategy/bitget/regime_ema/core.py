"""RegimeEMA core — re-exports from strategy.strategies.regime_ema.core.

All algorithm implementations live in the exchange-agnostic module.
This file preserves backward-compatible imports for live trading code.
"""

from strategy.strategies.regime_ema.core import (  # noqa: F401
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
