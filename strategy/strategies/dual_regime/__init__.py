"""Dual Regime strategy module."""

from strategy.strategies._base.signal_generator import TradeFilterConfig
from strategy.strategies.dual_regime.core import DualRegimeConfig

DualRegimeTradeFilterConfig = TradeFilterConfig  # backward compat alias

__all__ = [
    "DualRegimeConfig",
    "DualRegimeTradeFilterConfig",
]