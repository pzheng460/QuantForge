"""Momentum core — re-exports from strategy.strategies.momentum.core.

All algorithm implementations live in the exchange-agnostic module.
This file preserves backward-compatible imports for live trading code.
"""

from strategy.strategies.momentum.core import (  # noqa: F401
    MomentumConfig,
    calculate_atr,
    calculate_ema,
    calculate_ema_single,
    calculate_roc,
    calculate_sma,
)

__all__ = [
    "MomentumConfig",
    "calculate_atr",
    "calculate_ema",
    "calculate_ema_single",
    "calculate_roc",
    "calculate_sma",
]
