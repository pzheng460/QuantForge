"""
EMA Crossover Trading Strategy

A trend-following strategy that uses:
- Fast EMA / Slow EMA crossover for signal generation
- Price-based stop loss for risk management
"""

from strategy.strategies.ema_crossover.core import (
    EMAConfig,
    calculate_ema,
    calculate_ema_single,
)
from strategy.strategies.ema_crossover.indicator import (
    EMACrossoverIndicator,
    Signal,
)

__all__ = [
    "calculate_ema",
    "calculate_ema_single",
    "EMAConfig",
    "EMACrossoverIndicator",
    "Signal",
]
