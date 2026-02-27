"""Grid Trading strategy module."""

from strategy.strategies._base.signal_generator import TradeFilterConfig
from strategy.strategies.grid_trading.core import GridConfig

GridTradeFilterConfig = TradeFilterConfig  # backward compat alias

__all__ = [
    "GridConfig",
    "GridTradeFilterConfig",
]