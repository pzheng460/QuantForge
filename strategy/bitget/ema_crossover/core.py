"""EMA Crossover core — re-exports from strategy.strategies.ema_crossover.core.

All algorithm implementations live in the exchange-agnostic module.
This file preserves backward-compatible imports for live trading code.
"""

from strategy.strategies.ema_crossover.core import (  # noqa: F401
    EMAConfig,
    calculate_ema,
    calculate_ema_single,
)

__all__ = [
    "calculate_ema",
    "calculate_ema_single",
    "EMAConfig",
]
