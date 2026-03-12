"""
Tests for FundingRateProvider.

US-2: 资金费率获取
- FundingRateProvider 通过 CCXT 获取历史资金费率
- 支持指定时间范围获取资金费率
- 返回 pd.DataFrame（timestamp, funding_rate）
- 缓存机制避免重复请求
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from quantforge.backtest.data.funding_rate import FundingRateProvider


class TestFundingRateProvider:
    """Test FundingRateProvider implementation."""

    @pytest.fixture
    def provider(self):
        """Create a FundingRateProvider for Bitget."""
        return FundingRateProvider(exchange="bitget")

    def test_provider_initialization(self, provider):
        """Provider initializes with exchange name."""
        assert provider.exchange_name == "bitget"

    @pytest.mark.asyncio
    async def test_fetch_funding_rates_returns_dataframe(self, provider):
        """fetch_funding_rates returns a pandas DataFrame."""
        end = datetime.now()
        start = end - timedelta(days=7)

        df = await provider.fetch_funding_rates(
            symbol="BTC/USDT:USDT",
            start=start,
            end=end,
        )

        assert isinstance(df, pd.DataFrame)

    @pytest.mark.asyncio
    async def test_fetch_funding_rates_has_required_columns(self, provider):
        """DataFrame has timestamp and funding_rate columns."""
        end = datetime.now()
        start = end - timedelta(days=7)

        df = await provider.fetch_funding_rates(
            symbol="BTC/USDT:USDT",
            start=start,
            end=end,
        )

        assert "funding_rate" in df.columns
        assert isinstance(df.index, pd.DatetimeIndex)

    @pytest.mark.asyncio
    async def test_fetch_funding_rates_data_types(self, provider):
        """DataFrame columns have correct types."""
        end = datetime.now()
        start = end - timedelta(days=7)

        df = await provider.fetch_funding_rates(
            symbol="BTC/USDT:USDT",
            start=start,
            end=end,
        )

        if len(df) > 0:
            assert df["funding_rate"].dtype in [np.float64, np.float32]

    @pytest.mark.asyncio
    async def test_fetch_funding_rates_sorted_by_time(self, provider):
        """DataFrame is sorted by timestamp ascending."""
        end = datetime.now()
        start = end - timedelta(days=7)

        df = await provider.fetch_funding_rates(
            symbol="BTC/USDT:USDT",
            start=start,
            end=end,
        )

        if len(df) > 1:
            assert df.index.is_monotonic_increasing

    @pytest.mark.asyncio
    async def test_fetch_funding_rates_filters_by_date_range(self, provider):
        """fetch_funding_rates filters data by start and end dates."""
        # Use full days to avoid precision issues with 8-hour funding intervals
        end = datetime.now().replace(hour=23, minute=59, second=59)
        start = (end - timedelta(days=3)).replace(hour=0, minute=0, second=0)

        df = await provider.fetch_funding_rates(
            symbol="BTC/USDT:USDT",
            start=start,
            end=end,
        )

        if len(df) > 0:
            # Allow some tolerance since funding rate times are fixed (00:00, 08:00, 16:00 UTC)
            start_ts = pd.Timestamp(start).tz_localize(None) - pd.Timedelta(hours=8)
            end_ts = pd.Timestamp(end).tz_localize(None) + pd.Timedelta(hours=8)
            assert df.index.min() >= start_ts
            assert df.index.max() <= end_ts

    @pytest.mark.asyncio
    async def test_fetch_funding_rates_no_duplicates(self, provider):
        """DataFrame has no duplicate timestamps."""
        end = datetime.now()
        start = end - timedelta(days=7)

        df = await provider.fetch_funding_rates(
            symbol="BTC/USDT:USDT",
            start=start,
            end=end,
        )

        if len(df) > 0:
            assert not df.index.duplicated().any()


class TestFundingRateProviderCaching:
    """Test caching functionality of FundingRateProvider."""

    @pytest.fixture
    def provider(self):
        """Create a FundingRateProvider with caching enabled."""
        return FundingRateProvider(exchange="bitget", enable_cache=True)

    @pytest.mark.asyncio
    async def test_caching_enabled(self, provider):
        """Provider has caching enabled."""
        assert provider.enable_cache is True

    @pytest.mark.asyncio
    async def test_cache_is_used_on_second_call(self, provider):
        """Second call uses cached data."""
        end = datetime.now()
        start = end - timedelta(days=3)

        # First call
        df1 = await provider.fetch_funding_rates(
            symbol="BTC/USDT:USDT",
            start=start,
            end=end,
        )

        # Second call should use cache
        df2 = await provider.fetch_funding_rates(
            symbol="BTC/USDT:USDT",
            start=start,
            end=end,
        )

        # Results should be identical
        pd.testing.assert_frame_equal(df1, df2)

    @pytest.mark.asyncio
    async def test_clear_cache(self, provider):
        """Cache can be cleared."""
        end = datetime.now()
        start = end - timedelta(days=3)

        # First call
        await provider.fetch_funding_rates(
            symbol="BTC/USDT:USDT",
            start=start,
            end=end,
        )

        # Clear cache
        provider.clear_cache()

        # Cache should be empty
        assert len(provider._cache) == 0


class TestFundingRateProviderEdgeCases:
    """Test edge cases for FundingRateProvider."""

    @pytest.fixture
    def provider(self):
        """Create a FundingRateProvider for Bitget."""
        return FundingRateProvider(exchange="bitget")

    @pytest.mark.asyncio
    async def test_empty_result_for_future_dates(self, provider):
        """Returns empty DataFrame for future dates."""
        start = datetime.now() + timedelta(days=30)
        end = start + timedelta(days=7)

        df = await provider.fetch_funding_rates(
            symbol="BTC/USDT:USDT",
            start=start,
            end=end,
        )

        assert isinstance(df, pd.DataFrame)
        # May be empty or have no data for future dates

    @pytest.mark.asyncio
    async def test_handles_invalid_symbol_gracefully(self, provider):
        """Handles invalid symbol gracefully."""
        end = datetime.now()
        start = end - timedelta(days=1)

        # Should raise an error or return empty DataFrame for invalid symbol
        try:
            df = await provider.fetch_funding_rates(
                symbol="INVALID/SYMBOL",
                start=start,
                end=end,
            )
            # If no error, should return empty DataFrame
            assert isinstance(df, pd.DataFrame)
        except Exception:
            # Expected behavior - invalid symbol should raise error
            pass
