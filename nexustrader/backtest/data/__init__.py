"""
Data providers for backtesting.

Supports multiple data sources:
- CCXT: Fetch from exchanges via CCXT library
- File: Load from CSV/Parquet files
- PostgreSQL: Load from database
"""

from nexustrader.backtest.data.provider import DataProvider
from nexustrader.backtest.data.ccxt_provider import CCXTDataProvider
from nexustrader.backtest.data.file_provider import FileDataProvider
from nexustrader.backtest.data.funding_rate import FundingRateProvider

__all__ = [
    "DataProvider",
    "CCXTDataProvider",
    "FileDataProvider",
    "FundingRateProvider",
]
