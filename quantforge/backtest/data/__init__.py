"""
Data providers for backtesting.

Supports multiple data sources:
- CCXT: Fetch from exchanges via CCXT library
- File: Load from CSV/Parquet files
- SQLite cache: Local caching layer for historical klines
- Multi-source validation: Cross-validate data across exchanges
"""

from quantforge.backtest.data.provider import DataProvider
from quantforge.backtest.data.ccxt_provider import CCXTDataProvider
from quantforge.backtest.data.file_provider import FileDataProvider
from quantforge.backtest.data.funding_rate import FundingRateProvider
from quantforge.backtest.data.database import KlineDatabase
from quantforge.backtest.data.cached_provider import CachedDataProvider, ValidatedData

__all__ = [
    "DataProvider",
    "CCXTDataProvider",
    "FileDataProvider",
    "FundingRateProvider",
    "KlineDatabase",
    "CachedDataProvider",
    "ValidatedData",
]
