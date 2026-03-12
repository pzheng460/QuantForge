"""
CCXT-based DataProvider for fetching historical data from exchanges.
"""

import calendar
from datetime import datetime
from typing import Optional

import ccxt.async_support as ccxt
import pandas as pd

from quantforge.backtest.data.provider import DataProvider
from quantforge.constants import KlineInterval


class CCXTDataProvider(DataProvider):
    """
    Data provider that fetches historical data via CCXT.

    Supports any exchange that CCXT supports, with pagination
    for fetching large date ranges.
    """

    # Map KlineInterval to CCXT timeframe strings
    INTERVAL_MAP = {
        KlineInterval.MINUTE_1: "1m",
        KlineInterval.MINUTE_5: "5m",
        KlineInterval.MINUTE_15: "15m",
        KlineInterval.HOUR_1: "1h",
        KlineInterval.HOUR_4: "4h",
        KlineInterval.DAY_1: "1d",
    }

    # Map KlineInterval to milliseconds (for correct pagination)
    INTERVAL_MS = {
        KlineInterval.MINUTE_1: 60_000,
        KlineInterval.MINUTE_5: 300_000,
        KlineInterval.MINUTE_15: 900_000,
        KlineInterval.HOUR_1: 3_600_000,
        KlineInterval.HOUR_4: 14_400_000,
        KlineInterval.DAY_1: 86_400_000,
    }

    # Maximum candles per request.  Bitget silently ignores the ``since``
    # parameter when ``limit`` exceeds 200, returning the most recent bars
    # instead.  Cap at 200 for safety across all exchanges (the loop
    # handles pagination for larger ranges).
    _BATCH_LIMIT = 200

    def __init__(
        self,
        exchange: str,
        rate_limit: bool = True,
    ):
        """
        Initialize CCXT data provider.

        Args:
            exchange: Exchange name (e.g., "bitget", "binance")
            rate_limit: Whether to enable rate limiting
        """
        self.exchange_name = exchange
        self.rate_limit = rate_limit
        self._exchange: Optional[ccxt.Exchange] = None

    def _get_exchange(self) -> ccxt.Exchange:
        """Get or create CCXT exchange instance."""
        if self._exchange is None:
            exchange_class = getattr(ccxt, self.exchange_name)
            self._exchange = exchange_class(
                {
                    "enableRateLimit": self.rate_limit,
                }
            )
        return self._exchange

    async def _close_exchange(self):
        """Close the exchange connection."""
        if self._exchange is not None:
            await self._exchange.close()
            self._exchange = None

    async def fetch_klines(
        self,
        symbol: str,
        interval: KlineInterval,
        start: datetime,
        end: datetime,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Fetch historical kline data from exchange via CCXT.

        Args:
            symbol: Trading pair symbol (e.g., "BTC/USDT")
            interval: Kline interval
            start: Start datetime
            end: End datetime
            limit: Optional limit on number of candles

        Returns:
            pd.DataFrame with OHLCV data
        """
        exchange = self._get_exchange()

        try:
            # Convert interval to CCXT timeframe
            timeframe = self.INTERVAL_MAP.get(interval)
            if timeframe is None:
                raise ValueError(f"Unsupported interval: {interval}")

            interval_ms = self.INTERVAL_MS.get(interval)
            if interval_ms is None:
                raise ValueError(f"Unsupported interval: {interval}")

            # Convert datetimes to UTC timestamps (ms).
            # Use calendar.timegm() so naive datetimes are always treated
            # as UTC, matching the UTC timestamps returned by exchanges.
            since = int(calendar.timegm(start.timetuple()) * 1000)
            end_ts = int(calendar.timegm(end.timetuple()) * 1000)

            all_candles = []
            current_since = since

            while current_since < end_ts:
                # Fetch batch of candles (capped at _BATCH_LIMIT to
                # prevent Bitget from ignoring the ``since`` parameter)
                candles = await exchange.fetch_ohlcv(
                    symbol,
                    timeframe,
                    since=current_since,
                    limit=self._BATCH_LIMIT,
                )

                if not candles:
                    break

                # Filter candles within range
                for candle in candles:
                    if candle[0] <= end_ts:
                        all_candles.append(candle)

                # Advance by one full interval to avoid confusing exchanges (e.g.
                # Bitget) that return only 200 bars and jump forward 8 days when
                # given since = last_ts + 1ms.
                current_since = candles[-1][0] + interval_ms

                # Check if we've reached the end
                if candles[-1][0] >= end_ts:
                    break

                # Check limit
                if limit and len(all_candles) >= limit:
                    all_candles = all_candles[:limit]
                    break

            # Convert to DataFrame
            if not all_candles:
                return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

            df = pd.DataFrame(
                all_candles,
                columns=["timestamp", "open", "high", "low", "close", "volume"],
            )

            # Deduplicate by timestamp (overlapping batches can occur when
            # exchanges return fewer bars than requested)
            df.drop_duplicates(subset="timestamp", keep="first", inplace=True)

            # Convert timestamp to datetime
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)

            # Validate and return
            return self.validate_dataframe(df)

        finally:
            await self._close_exchange()

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self._close_exchange()
