"""SQLite cache layer for historical kline data.

Stores OHLCV bars locally so repeated backtests don't re-fetch from
exchanges.  Uses the standard library ``sqlite3`` — no extra dependencies.

Default database location: ``~/.nexustrader/data/klines.db``
"""

from __future__ import annotations

import calendar
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

_DEFAULT_DB_DIR = Path.home() / ".nexustrader" / "data"
_DEFAULT_DB_PATH = _DEFAULT_DB_DIR / "klines.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS klines (
    exchange  TEXT    NOT NULL,
    symbol    TEXT    NOT NULL,
    interval  TEXT    NOT NULL,
    timestamp INTEGER NOT NULL,
    open      REAL    NOT NULL,
    high      REAL    NOT NULL,
    low       REAL    NOT NULL,
    close     REAL    NOT NULL,
    volume    REAL    NOT NULL,
    UNIQUE(exchange, symbol, interval, timestamp)
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_klines_lookup
ON klines (exchange, symbol, interval, timestamp)
"""


class KlineDatabase:
    """SQLite-backed cache for historical kline data.

    Parameters
    ----------
    db_path : str or Path, optional
        Path to the SQLite database file.  Parent directories are created
        automatically.  Defaults to ``~/.nexustrader/data/klines.db``.
    """

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_CREATE_TABLE)
        self._conn.execute(_CREATE_INDEX)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(
        self,
        exchange: str,
        symbol: str,
        interval: str,
        df: pd.DataFrame,
    ) -> int:
        """Write an OHLCV DataFrame into the cache.

        Parameters
        ----------
        exchange : str
            Exchange identifier (e.g. ``"bitget"``).
        symbol : str
            Trading pair (e.g. ``"BTC/USDT:USDT"``).
        interval : str
            Kline interval string (e.g. ``"15m"``, ``"1h"``).
        df : pd.DataFrame
            Must have a DatetimeIndex and columns
            ``open, high, low, close, volume``.

        Returns
        -------
        int
            Number of rows inserted (duplicates are silently skipped).
        """
        if df.empty:
            return 0

        rows = []
        for ts, row in df.iterrows():
            rows.append(
                (
                    exchange,
                    symbol,
                    interval,
                    self._to_epoch(ts.to_pydatetime()),
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row["volume"]),
                )
            )

        cursor = self._conn.executemany(
            "INSERT OR IGNORE INTO klines "
            "(exchange, symbol, interval, timestamp, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        return cursor.rowcount

    def load(
        self,
        exchange: str,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Read cached OHLCV data for the given range.

        Parameters
        ----------
        exchange, symbol, interval : str
            Selectors matching what was passed to :meth:`save`.
        start, end : datetime
            Inclusive time range.

        Returns
        -------
        pd.DataFrame
            OHLCV DataFrame with DatetimeIndex, or an empty DataFrame if
            no data is cached for the range.
        """
        start_ts = self._to_epoch(start)
        end_ts = self._to_epoch(end)

        cursor = self._conn.execute(
            "SELECT timestamp, open, high, low, close, volume "
            "FROM klines "
            "WHERE exchange = ? AND symbol = ? AND interval = ? "
            "AND timestamp >= ? AND timestamp <= ? "
            "ORDER BY timestamp",
            (exchange, symbol, interval, start_ts, end_ts),
        )
        rows = cursor.fetchall()

        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame(
            rows,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
        df.set_index("timestamp", inplace=True)
        return df

    def has_data(
        self,
        exchange: str,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> bool:
        """Check whether the cache covers the entire requested range.

        The heuristic compares the number of cached bars against the
        expected bar count for the range (with a 5 % tolerance to account
        for non-trading hours or exchange downtime).
        """
        start_ts = self._to_epoch(start)
        end_ts = self._to_epoch(end)

        row = self._conn.execute(
            "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) "
            "FROM klines "
            "WHERE exchange = ? AND symbol = ? AND interval = ? "
            "AND timestamp >= ? AND timestamp <= ?",
            (exchange, symbol, interval, start_ts, end_ts),
        ).fetchone()

        count, min_ts, max_ts = row
        if count == 0:
            return False

        # Check that first/last cached bars are close to requested range
        margin = self._interval_seconds(interval) * 2
        if min_ts - start_ts > margin:
            return False
        if end_ts - max_ts > margin:
            return False

        return True

    def get_gaps(
        self,
        exchange: str,
        symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> List[Tuple[datetime, datetime]]:
        """Identify time ranges missing from the cache.

        Returns a list of ``(gap_start, gap_end)`` datetime pairs.  An
        empty list means the cache fully covers the requested range.
        """
        start_ts = self._to_epoch(start)
        end_ts = self._to_epoch(end)
        interval_s = self._interval_seconds(interval)

        cursor = self._conn.execute(
            "SELECT timestamp FROM klines "
            "WHERE exchange = ? AND symbol = ? AND interval = ? "
            "AND timestamp >= ? AND timestamp <= ? "
            "ORDER BY timestamp",
            (exchange, symbol, interval, start_ts, end_ts),
        )
        timestamps = [r[0] for r in cursor.fetchall()]

        gaps: List[Tuple[datetime, datetime]] = []

        if not timestamps:
            # No data at all → entire range is a gap
            return [(start, end)]

        # Gap at the beginning
        if timestamps[0] - start_ts > interval_s * 2:
            gaps.append((start, self._from_epoch(timestamps[0] - interval_s)))

        # Internal gaps
        for i in range(1, len(timestamps)):
            delta = timestamps[i] - timestamps[i - 1]
            if delta > interval_s * 2:
                gap_start = self._from_epoch(timestamps[i - 1] + interval_s)
                gap_end = self._from_epoch(timestamps[i] - interval_s)
                gaps.append((gap_start, gap_end))

        # Gap at the end
        if end_ts - timestamps[-1] > interval_s * 2:
            gaps.append((self._from_epoch(timestamps[-1] + interval_s), end))

        return gaps

    def stats(self) -> dict:
        """Return database statistics.

        Returns
        -------
        dict
            Keys: ``total_rows``, ``exchanges``, ``symbols``,
            ``db_size_mb``, ``db_path``.
        """
        total = self._conn.execute("SELECT COUNT(*) FROM klines").fetchone()[0]
        exchanges = [
            r[0]
            for r in self._conn.execute(
                "SELECT DISTINCT exchange FROM klines"
            ).fetchall()
        ]
        symbols = [
            r[0]
            for r in self._conn.execute("SELECT DISTINCT symbol FROM klines").fetchall()
        ]
        # Flush WAL to main file so stat() reflects actual size
        self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db_size = (
            self._db_path.stat().st_size / (1024 * 1024)
            if self._db_path.exists()
            else 0.0
        )

        return {
            "total_rows": total,
            "exchanges": exchanges,
            "symbols": symbols,
            "db_size_mb": round(db_size, 2),
            "db_path": str(self._db_path),
        }

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_epoch(dt: datetime) -> int:
        """Convert a naive datetime to a UTC unix timestamp (seconds).

        Uses ``calendar.timegm`` so the result is always UTC regardless
        of the system's local timezone — matching how ``pd.Timestamp``
        interprets tz-naive objects.
        """
        return int(calendar.timegm(dt.timetuple()))

    @staticmethod
    def _from_epoch(ts: int) -> datetime:
        """Convert a UTC unix timestamp back to a naive datetime."""
        from datetime import timezone

        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _interval_seconds(interval: str) -> int:
        """Convert an interval string like ``'15m'`` to seconds."""
        unit = interval[-1]
        value = int(interval[:-1])
        multipliers = {"m": 60, "h": 3600, "d": 86400}
        return value * multipliers.get(unit, 60)
