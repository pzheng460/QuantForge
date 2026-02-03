"""
NexusTrader Backtest Module.

This module provides a comprehensive backtesting framework for trading strategies.
It supports both vectorized and event-driven backtesting modes.
"""

# Core result types
from nexustrader.backtest.result import BacktestConfig, BacktestResult, TradeRecord

# Engine components
from nexustrader.backtest.engine.cost_model import CostConfig, CostModel
from nexustrader.backtest.engine.vectorized import Signal, VectorizedBacktest
from nexustrader.backtest.engine.event_driven import (
    BaseStrategy,
    EventDrivenBacktest,
    Order,
    OrderSide,
    OrderType,
    Position,
)

# Analysis tools
from nexustrader.backtest.analysis.performance import PerformanceAnalyzer
from nexustrader.backtest.analysis.regime import MarketRegime, RegimeClassifier
from nexustrader.backtest.analysis.report import ReportGenerator

# Optimization tools
from nexustrader.backtest.optimization.grid_search import (
    GridSearchOptimizer,
    OptimizationResult,
    ParameterGrid,
)
from nexustrader.backtest.optimization.walk_forward import (
    WalkForwardAnalyzer,
    WalkForwardResult,
    WindowType,
)

__all__ = [
    # Core
    "BacktestConfig",
    "BacktestResult",
    "TradeRecord",
    # Engine
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
    # Analysis
    "PerformanceAnalyzer",
    "MarketRegime",
    "RegimeClassifier",
    "ReportGenerator",
    # Optimization
    "GridSearchOptimizer",
    "OptimizationResult",
    "ParameterGrid",
    "WalkForwardAnalyzer",
    "WalkForwardResult",
    "WindowType",
]
