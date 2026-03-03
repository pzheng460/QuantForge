"""
Data providers for backtesting.

Supports multiple data sources:
- CCXT: Fetch from exchanges via CCXT library
- File: Load from CSV/Parquet files
- SQLite cache: Local caching layer for historical klines
- Multi-source validation: Cross-validate data across exchanges
"""

from nexustrader.backtest.data.provider import DataProvider
from nexustrader.backtest.data.ccxt_provider import CCXTDataProvider
from nexustrader.backtest.data.file_provider import FileDataProvider
from nexustrader.backtest.data.funding_rate import FundingRateProvider
from nexustrader.backtest.data.database import KlineDatabase
from nexustrader.backtest.data.cached_provider import CachedDataProvider, ValidatedData

__all__ = [
    "DataProvider",
    "CCXTDataProvider",
    "FileDataProvider",
    "FundingRateProvider",
    "KlineDatabase",
    "CachedDataProvider",
    "ValidatedData",
]
