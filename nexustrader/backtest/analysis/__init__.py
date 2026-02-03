"""
Analysis and reporting tools.

Includes performance metrics calculation, regime classification,
and HTML report generation.
"""

from nexustrader.backtest.analysis.performance import PerformanceAnalyzer
from nexustrader.backtest.analysis.regime import MarketRegime, RegimeClassifier
from nexustrader.backtest.analysis.report import ReportGenerator

__all__ = [
    "PerformanceAnalyzer",
    "MarketRegime",
    "RegimeClassifier",
    "ReportGenerator",
]
