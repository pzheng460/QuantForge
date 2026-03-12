"""
Backtest engines.

Supports both vectorized and event-driven backtesting modes.
"""

from quantforge.backtest.engine.cost_model import CostConfig, CostModel
from quantforge.backtest.engine.vectorized import Signal, VectorizedBacktest
from quantforge.backtest.engine.event_driven import (
    BaseStrategy,
    EventDrivenBacktest,
    Order,
    OrderSide,
    OrderType,
    Position,
)

__all__ = [
    "CostConfig",
    "CostModel",
    "Signal",
    "VectorizedBacktest",
    "BaseStrategy",
    "EventDrivenBacktest",
    "Order",
    "OrderSide",
    "OrderType",
    "Position",
]
