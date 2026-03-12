"""
File-based DataProvider for loading historical data from CSV/Parquet files.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from quantforge.backtest.data.provider import DataProvider
from quantforge.constants import KlineInterval


class FileDataProvider(DataProvider):
    """
    Data provider that loads historical data from local files.

    Supports CSV and Parquet file formats.
    """

    SUPPORTED_FORMATS = {".csv", ".parquet", ".pq"}

    def __init__(
        self,
        file_path: Union[str, Path],
        timestamp_column: str = "timestamp",
    ):
        """
        Initialize file data provider.

        Args:
            file_path: Path to the data file (CSV or Parquet)
            timestamp_column: Name of the timestamp column

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file format is not supported
        """
        self.file_path = Path(file_path)
        self.timestamp_column = timestamp_column

        # Validate file exists
        if not self.file_path.exists():
            raise FileNotFoundError(f"Data file not found: {self.file_path}")

        # Validate file format
        suffix = self.file_path.suffix.lower()
        if suffix not in self.SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported file format: {suffix}. "
                f"Supported formats: {self.SUPPORTED_FORMATS}"
            )

        self._cached_df: Optional[pd.DataFrame] = None

    def _load_file(self) -> pd.DataFrame:
        """Load data from file."""
        if self._cached_df is not None:
            return self._cached_df

        suffix = self.file_path.suffix.lower()

        if suffix == ".csv":
            df = pd.read_csv(self.file_path)
        elif suffix in {".parquet", ".pq"}:
            df = pd.read_parquet(self.file_path)
        else:
            raise ValueError(f"Unsupported file format: {suffix}")

        # Convert timestamp column to datetime
        if self.timestamp_column in df.columns:
            df[self.timestamp_column] = pd.to_datetime(df[self.timestamp_column])
            df.set_index(self.timestamp_column, inplace=True)
        elif not isinstance(df.index, pd.DatetimeIndex):
            # Try to convert index to datetime
            df.index = pd.to_datetime(df.index)

        # Rename index to 'timestamp' for consistency
        df.index.name = "timestamp"

        # Validate and cache
        df = self.validate_dataframe(df)
        self._cached_df = df

        return df

    async def fetch_klines(
        self,
        symbol: str,
        interval: KlineInterval,
        start: datetime,
        end: datetime,
        limit: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Fetch historical kline data from file.

        Note: symbol and interval parameters are ignored for file provider,
        as the file contains pre-determined data.

        Args:
            symbol: Trading pair symbol (ignored)
            interval: Kline interval (ignored)
            start: Start datetime
            end: End datetime
            limit: Optional limit on number of candles

        Returns:
            pd.DataFrame with OHLCV data
        """
        df = self._load_file()

        # Filter by date range
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)

        # Remove timezone if present
        if start_ts.tz is not None:
            start_ts = start_ts.tz_localize(None)
        if end_ts.tz is not None:
            end_ts = end_ts.tz_localize(None)

        mask = (df.index >= start_ts) & (df.index <= end_ts)
        result = df.loc[mask].copy()

        # Apply limit
        if limit and len(result) > limit:
            result = result.head(limit)

        return result

    def clear_cache(self):
        """Clear the cached DataFrame."""
        self._cached_df = None
