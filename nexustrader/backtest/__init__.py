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
from nexustrader.backtest.analysis.regime import MarketRegime, RegimeClassifier, SimpleRegime
from nexustrader.backtest.analysis.report import ReportGenerator
from nexustrader.backtest.analysis.comprehensive_report import ComprehensiveReportGenerator

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

# Simulation tools
from nexustrader.backtest.simulation import (
    BlockBootstrap,
    GBMGenerator,
    JumpDiffusionGenerator,
    SimulationReport,
    StressTestGenerator,
    StressTestResult,
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
    "SimpleRegime",
    "ReportGenerator",
    "ComprehensiveReportGenerator",
    # Optimization
    "GridSearchOptimizer",
    "OptimizationResult",
    "ParameterGrid",
    "WalkForwardAnalyzer",
    "WalkForwardResult",
    "WindowType",
    # Simulation
    "BlockBootstrap",
    "GBMGenerator",
    "JumpDiffusionGenerator",
    "SimulationReport",
    "StressTestGenerator",
    "StressTestResult",
]
