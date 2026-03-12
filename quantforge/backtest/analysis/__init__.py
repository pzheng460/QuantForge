"""
Analysis and reporting tools.

Includes performance metrics calculation, regime classification,
and HTML report generation.
"""

from quantforge.backtest.analysis.performance import PerformanceAnalyzer
from quantforge.backtest.analysis.regime import MarketRegime, RegimeClassifier
from quantforge.backtest.analysis.report import ReportGenerator

__all__ = [
    "PerformanceAnalyzer",
    "MarketRegime",
    "RegimeClassifier",
    "ReportGenerator",
]
