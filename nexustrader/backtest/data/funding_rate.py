"""
FundingRateProvider for fetching historical funding rates.
"""

from datetime import datetime
from typing import Dict, Optional, Tuple

import ccxt.async_support as ccxt
import pandas as pd


class FundingRateProvider:
    """
    Provider for fetching historical funding rates from exchanges.

    Funding rates are typically settled every 8 hours on perpetual contracts.
    """

    def __init__(
        self,
        exchange: str = "bitget",
        enable_cache: bool = True,
    ):
        """
        Initialize funding rate provider.

        Args:
            exchange: Exchange name (e.g., "bitget", "binance")
            enable_cache: Whether to cache fetched data
        """
        self.exchange_name = exchange
        self.enable_cache = enable_cache
        self._exchange: Optional[ccxt.Exchange] = None
        self._cache: Dict[Tuple[str, datetime, datetime], pd.DataFrame] = {}

    def _get_exchange(self) -> ccxt.Exchange:
        """Get or create CCXT exchange instance."""
        if self._exchange is None:
            exchange_class = getattr(ccxt, self.exchange_name)
            self._exchange = exchange_class({
                "enableRateLimit": True,
            })
        return self._exchange

    async def _close_exchange(self):
        """Close the exchange connection."""
        if self._exchange is not None:
            await self._exchange.close()
            self._exchange = None

    async def fetch_funding_rates(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """
        Fetch historical funding rates for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., "BTC/USDT:USDT")
            start: Start datetime
            end: End datetime

        Returns:
            pd.DataFrame with columns:
                - index: DatetimeIndex (timestamp)
                - funding_rate: float

            DataFrame is sorted by timestamp ascending with no duplicates.
        """
        # Check cache
        cache_key = (symbol, start, end)
        if self.enable_cache and cache_key in self._cache:
            return self._cache[cache_key].copy()

        exchange = self._get_exchange()

        try:
            # Convert datetimes to timestamps
            since = int(start.timestamp() * 1000)
            end_ts = int(end.timestamp() * 1000)

            all_rates = []

            # Some exchanges support fetch_funding_rate_history
            if exchange.has.get("fetchFundingRateHistory"):
                current_since = since

                while current_since < end_ts:
                    try:
                        rates = await exchange.fetch_funding_rate_history(
                            symbol,
                            since=current_since,
                            limit=1000,
                        )

                        if not rates:
                            break

                        for rate in rates:
                            rate_ts = rate.get("timestamp", 0)
                            if since <= rate_ts <= end_ts:
                                all_rates.append({
                                    "timestamp": rate_ts,
                                    "funding_rate": rate.get("fundingRate", 0),
                                })

                        # Move to next batch
                        current_since = rates[-1]["timestamp"] + 1

                        # Check if we've reached the end
                        if rates[-1]["timestamp"] >= end_ts:
                            break

                    except Exception:
                        break
            else:
                # Fallback: try to get current funding rate
                # This is less useful for historical data
                try:
                    rate = await exchange.fetch_funding_rate(symbol)
                    if rate:
                        all_rates.append({
                            "timestamp": rate.get("timestamp", int(datetime.now().timestamp() * 1000)),
                            "funding_rate": rate.get("fundingRate", 0),
                        })
                except Exception:
                    pass

            # Convert to DataFrame
            if not all_rates:
                df = pd.DataFrame(columns=["funding_rate"])
                df.index.name = "timestamp"
                return df

            df = pd.DataFrame(all_rates)

            # Convert timestamp to datetime
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)

            # Remove timezone info if present
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)

            # Remove duplicates and sort
            df = df[~df.index.duplicated(keep="first")]
            df = df.sort_index()

            # Cache result
            if self.enable_cache:
                self._cache[cache_key] = df.copy()

            return df

        finally:
            await self._close_exchange()

    def clear_cache(self):
        """Clear the cached data."""
        self._cache.clear()

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self._close_exchange()
