"""Dual Regime strategy module."""

from strategy.strategies.dual_regime.core import DualRegimeConfig
from strategy.strategies.dual_regime.signal import (
    DualRegimeSignalGenerator,
    DualRegimeTradeFilterConfig,
)

__all__ = [
    "DualRegimeConfig",
    "DualRegimeSignalGenerator", 
    "DualRegimeTradeFilterConfig",
]