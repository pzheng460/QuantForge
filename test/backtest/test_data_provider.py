"""
Tests for DataProvider base class and implementations.

US-1: 数据源抽象层
- DataProvider 基类定义统一接口
- CCXTDataProvider, FileDataProvider 实现
- 所有 provider 返回相同格式的 DataFrame
"""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quantforge.backtest.data.provider import DataProvider
from quantforge.backtest.data.ccxt_provider import CCXTDataProvider
from quantforge.backtest.data.file_provider import FileDataProvider
from quantforge.constants import KlineInterval


class TestDataProviderInterface:
    """Test DataProvider abstract interface."""

    def test_data_provider_is_abstract(self):
        """DataProvider cannot be instantiated directly."""
        with pytest.raises(TypeError):
            DataProvider()

    def test_data_provider_has_fetch_klines_method(self):
        """DataProvider must define fetch_klines method."""
        assert hasattr(DataProvider, "fetch_klines")


class TestCCXTDataProvider:
    """Test CCXTDataProvider implementation."""

    @pytest.fixture
    def provider(self):
        """Create a CCXT provider for Bitget."""
        return CCXTDataProvider(exchange="bitget")

    def test_provider_initialization(self, provider):
        """Provider initializes with exchange name."""
        assert provider.exchange_name == "bitget"

    @pytest.mark.asyncio
    async def test_fetch_klines_returns_dataframe(self, provider):
        """fetch_klines returns a pandas DataFrame."""
        end = datetime.now()
        start = end - timedelta(days=1)

        df = await provider.fetch_klines(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start=start,
            end=end,
        )

        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0

    @pytest.mark.asyncio
    async def test_fetch_klines_has_required_columns(self, provider):
        """DataFrame has required OHLCV columns."""
        end = datetime.now()
        start = end - timedelta(days=1)

        df = await provider.fetch_klines(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start=start,
            end=end,
        )

        required_columns = ["open", "high", "low", "close", "volume"]
        for col in required_columns:
            assert col in df.columns, f"Missing column: {col}"

    @pytest.mark.asyncio
    async def test_fetch_klines_index_is_datetime(self, provider):
        """DataFrame index is DatetimeIndex."""
        end = datetime.now()
        start = end - timedelta(days=1)

        df = await provider.fetch_klines(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start=start,
            end=end,
        )

        assert isinstance(df.index, pd.DatetimeIndex)

    @pytest.mark.asyncio
    async def test_fetch_klines_data_types(self, provider):
        """DataFrame columns have correct numeric types."""
        end = datetime.now()
        start = end - timedelta(days=1)

        df = await provider.fetch_klines(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start=start,
            end=end,
        )

        assert df["open"].dtype in [np.float64, np.float32]
        assert df["close"].dtype in [np.float64, np.float32]
        assert df["volume"].dtype in [np.float64, np.float32]

    @pytest.mark.asyncio
    async def test_fetch_klines_no_duplicates(self, provider):
        """DataFrame has no duplicate timestamps."""
        end = datetime.now()
        start = end - timedelta(days=1)

        df = await provider.fetch_klines(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start=start,
            end=end,
        )

        assert not df.index.duplicated().any()

    @pytest.mark.asyncio
    async def test_fetch_klines_sorted_by_time(self, provider):
        """DataFrame is sorted by timestamp ascending."""
        end = datetime.now()
        start = end - timedelta(days=1)

        df = await provider.fetch_klines(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start=start,
            end=end,
        )

        assert df.index.is_monotonic_increasing


class TestFileDataProvider:
    """Test FileDataProvider implementation."""

    @pytest.fixture
    def sample_csv(self, tmp_path):
        """Create a sample CSV file for testing."""
        data = {
            "timestamp": pd.date_range("2024-01-01", periods=100, freq="15min"),
            "open": np.random.uniform(40000, 50000, 100),
            "high": np.random.uniform(40000, 50000, 100),
            "low": np.random.uniform(40000, 50000, 100),
            "close": np.random.uniform(40000, 50000, 100),
            "volume": np.random.uniform(100, 1000, 100),
        }
        df = pd.DataFrame(data)
        csv_path = tmp_path / "test_data.csv"
        df.to_csv(csv_path, index=False)
        return csv_path

    @pytest.fixture
    def sample_parquet(self, tmp_path):
        """Create a sample Parquet file for testing."""
        data = {
            "timestamp": pd.date_range("2024-01-01", periods=100, freq="15min"),
            "open": np.random.uniform(40000, 50000, 100),
            "high": np.random.uniform(40000, 50000, 100),
            "low": np.random.uniform(40000, 50000, 100),
            "close": np.random.uniform(40000, 50000, 100),
            "volume": np.random.uniform(100, 1000, 100),
        }
        df = pd.DataFrame(data)
        parquet_path = tmp_path / "test_data.parquet"
        df.to_parquet(parquet_path, index=False)
        return parquet_path

    def test_provider_initialization_csv(self, sample_csv):
        """Provider initializes with CSV file path."""
        provider = FileDataProvider(file_path=sample_csv)
        assert provider.file_path == sample_csv

    def test_provider_initialization_parquet(self, sample_parquet):
        """Provider initializes with Parquet file path."""
        provider = FileDataProvider(file_path=sample_parquet)
        assert provider.file_path == sample_parquet

    @pytest.mark.asyncio
    async def test_fetch_klines_from_csv(self, sample_csv):
        """fetch_klines loads data from CSV file."""
        provider = FileDataProvider(file_path=sample_csv)

        df = await provider.fetch_klines(
            symbol="BTC/USDT",  # Ignored for file provider
            interval=KlineInterval.MINUTE_15,
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 2),
        )

        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0

    @pytest.mark.asyncio
    async def test_fetch_klines_from_parquet(self, sample_parquet):
        """fetch_klines loads data from Parquet file."""
        provider = FileDataProvider(file_path=sample_parquet)

        df = await provider.fetch_klines(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 2),
        )

        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0

    @pytest.mark.asyncio
    async def test_fetch_klines_filters_by_date_range(self, sample_csv):
        """fetch_klines filters data by start and end dates."""
        provider = FileDataProvider(file_path=sample_csv)

        start = datetime(2024, 1, 1, 6, 0)
        end = datetime(2024, 1, 1, 12, 0)

        df = await provider.fetch_klines(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start=start,
            end=end,
        )

        assert df.index.min() >= pd.Timestamp(start)
        assert df.index.max() <= pd.Timestamp(end)

    @pytest.mark.asyncio
    async def test_fetch_klines_has_required_columns(self, sample_csv):
        """DataFrame has required OHLCV columns."""
        provider = FileDataProvider(file_path=sample_csv)

        df = await provider.fetch_klines(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 2),
        )

        required_columns = ["open", "high", "low", "close", "volume"]
        for col in required_columns:
            assert col in df.columns

    def test_provider_raises_for_nonexistent_file(self, tmp_path):
        """Provider raises error for non-existent file."""
        with pytest.raises(FileNotFoundError):
            FileDataProvider(file_path=tmp_path / "nonexistent.csv")

    def test_provider_raises_for_unsupported_format(self, tmp_path):
        """Provider raises error for unsupported file format."""
        unsupported_file = tmp_path / "test.txt"
        unsupported_file.write_text("test")
        with pytest.raises(ValueError, match="Unsupported file format"):
            FileDataProvider(file_path=unsupported_file)


class TestDataProviderConsistency:
    """Test that all providers return consistent data format."""

    @pytest.fixture
    def file_provider(self, tmp_path):
        """Create a file provider with sample data."""
        data = {
            "timestamp": pd.date_range("2024-01-01", periods=100, freq="15min"),
            "open": np.random.uniform(40000, 50000, 100),
            "high": np.random.uniform(40000, 50000, 100),
            "low": np.random.uniform(40000, 50000, 100),
            "close": np.random.uniform(40000, 50000, 100),
            "volume": np.random.uniform(100, 1000, 100),
        }
        df = pd.DataFrame(data)
        csv_path = tmp_path / "test_data.csv"
        df.to_csv(csv_path, index=False)
        return FileDataProvider(file_path=csv_path)

    @pytest.mark.asyncio
    async def test_all_providers_return_same_column_names(self, file_provider):
        """All providers return DataFrames with same column names."""
        df = await file_provider.fetch_klines(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 2),
        )

        expected_columns = {"open", "high", "low", "close", "volume"}
        assert expected_columns.issubset(set(df.columns))

    @pytest.mark.asyncio
    async def test_all_providers_return_datetime_index(self, file_provider):
        """All providers return DataFrames with DatetimeIndex."""
        df = await file_provider.fetch_klines(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 2),
        )

        assert isinstance(df.index, pd.DatetimeIndex)
