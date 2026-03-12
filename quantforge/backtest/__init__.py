"""
QuantForge Backtest Module.

This module provides a comprehensive backtesting framework for trading strategies.
It supports both vectorized and event-driven backtesting modes.
"""

# Core result types
from quantforge.backtest.result import BacktestConfig, BacktestResult, TradeRecord

# Engine components
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

# Analysis tools
from quantforge.backtest.analysis.performance import PerformanceAnalyzer
from quantforge.backtest.analysis.regime import MarketRegime, RegimeClassifier, SimpleRegime
from quantforge.backtest.analysis.report import ReportGenerator
from quantforge.backtest.analysis.comprehensive_report import ComprehensiveReportGenerator

# Optimization tools
from quantforge.backtest.optimization.grid_search import (
    GridSearchOptimizer,
    OptimizationResult,
    ParameterGrid,
)
from quantforge.backtest.optimization.walk_forward import (
    WalkForwardAnalyzer,
    WalkForwardResult,
    WindowType,
)

# Simulation tools
from quantforge.backtest.simulation import (
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
