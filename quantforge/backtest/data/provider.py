"""
Base DataProvider class for backtesting.

Defines the unified interface for all data sources.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

import pandas as pd

from quantforge.constants import KlineInterval


class DataProvider(ABC):
    """
    Abstract base class for data providers.

    All data providers must implement the fetch_klines method
    and return data in a consistent format.
    """

    @abstractmethod
    async def fetch_klines(
        self,
        symbol: str,
        interval: KlineInterval,
        start: datetime,
        end: datetime,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Fetch historical kline/candlestick data.

        Args:
            symbol: Trading pair symbol (e.g., "BTC/USDT")
            interval: Kline interval (e.g., KlineInterval.MINUTE_15)
            start: Start datetime (inclusive)
            end: End datetime (inclusive)
            limit: Optional limit on number of candles

        Returns:
            pd.DataFrame with columns:
                - index: DatetimeIndex (timestamp)
                - open: float
                - high: float
                - low: float
                - close: float
                - volume: float

            DataFrame is sorted by timestamp ascending with no duplicates.
        """
        pass

    @staticmethod
    def validate_dataframe(df: pd.DataFrame) -> pd.DataFrame:
        """
        Validate and normalize a DataFrame to standard format.

        Args:
            df: Input DataFrame

        Returns:
            Validated DataFrame with correct format

        Raises:
            ValueError: If required columns are missing
        """
        required_columns = {"open", "high", "low", "close", "volume"}

        # Check for required columns
        missing = required_columns - set(df.columns)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        # Ensure numeric types
        for col in required_columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # Ensure index is DatetimeIndex
        if not isinstance(df.index, pd.DatetimeIndex):
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df.set_index("timestamp", inplace=True)
            else:
                raise ValueError("DataFrame must have a 'timestamp' column or DatetimeIndex")

        # Remove timezone info if present
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        # Remove duplicates
        df = df[~df.index.duplicated(keep="first")]

        # Sort by timestamp
        df = df.sort_index()

        return df
