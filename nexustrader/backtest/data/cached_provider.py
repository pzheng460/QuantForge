"""Smart data provider with local SQLite caching and multi-source validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

from nexustrader.backtest.data.ccxt_provider import CCXTDataProvider
from nexustrader.backtest.data.database import KlineDatabase
from nexustrader.constants import KlineInterval


# Map KlineInterval enum → short string for the database
_INTERVAL_STR = {
    KlineInterval.MINUTE_1: "1m",
    KlineInterval.MINUTE_5: "5m",
    KlineInterval.MINUTE_15: "15m",
    KlineInterval.HOUR_1: "1h",
    KlineInterval.HOUR_4: "4h",
    KlineInterval.DAY_1: "1d",
}


@dataclass
class ValidatedData:
    """Result of multi-source cross-validation.

    Attributes
    ----------
    primary_data : pd.DataFrame
        OHLCV from the primary exchange (first in *sources*).
    validation_report : dict
        Per-source comparison statistics (mean_diff, max_diff, correlation).
    anomalies : pd.DataFrame
        Rows where close prices diverge beyond the threshold, with columns
        showing per-source close values and ``max_deviation``.
    is_valid : bool
        ``True`` if all sources agree within tolerance.
    """

    primary_data: pd.DataFrame
    validation_report: dict = field(default_factory=dict)
    anomalies: pd.DataFrame = field(default_factory=lambda: pd.DataFrame())
    is_valid: bool = True


class CachedDataProvider:
    """Fetch OHLCV data with transparent local caching.

    Data is stored in a SQLite database on first fetch and served from
    the cache on subsequent calls.  Only gaps (missing time ranges) are
    fetched from the exchange.

    Parameters
    ----------
    exchanges : list[str]
        Exchange identifiers available for fetching (default ``["bitget"]``).
    db_path : str or Path, optional
        Custom path for the SQLite database file.
    """

    def __init__(
        self,
        exchanges: Optional[List[str]] = None,
        db_path: Optional[str | Path] = None,
    ) -> None:
        self._exchanges = exchanges or ["bitget"]
        self._db = KlineDatabase(db_path)

    # ------------------------------------------------------------------
    # Core fetch (cache-aware)
    # ------------------------------------------------------------------

    async def fetch(
        self,
        symbol: str,
        interval: KlineInterval,
        start: datetime,
        end: datetime,
        exchange: str = "bitget",
    ) -> pd.DataFrame:
        """Fetch OHLCV data, pulling from cache first and filling gaps.

        Parameters
        ----------
        symbol : str
            Trading pair (e.g. ``"BTC/USDT:USDT"``).
        interval : KlineInterval
            Bar interval.
        start, end : datetime
            Inclusive time range.
        exchange : str
            Exchange to use for any uncached data.

        Returns
        -------
        pd.DataFrame
            Complete OHLCV DataFrame with DatetimeIndex.
        """
        iv_str = _INTERVAL_STR.get(interval)
        if iv_str is None:
            raise ValueError(f"Unsupported interval: {interval}")

        gaps = self._db.get_gaps(exchange, symbol, iv_str, start, end)

        if gaps:
            print(f"[cache] {len(gaps)} gap(s) detected — fetching from {exchange}...")
            async with CCXTDataProvider(exchange=exchange) as provider:
                for gap_start, gap_end in gaps:
                    chunk = await provider.fetch_klines(
                        symbol=symbol,
                        interval=interval,
                        start=gap_start,
                        end=gap_end,
                    )
                    if not chunk.empty:
                        self._db.save(exchange, symbol, iv_str, chunk)
                        print(
                            f"[cache] Cached {len(chunk)} bars "
                            f"({gap_start.date()} → {gap_end.date()})"
                        )
        else:
            print(f"[cache] Serving {symbol} {iv_str} from local cache")

        return self._db.load(exchange, symbol, iv_str, start, end)

    # ------------------------------------------------------------------
    # Multi-source validation
    # ------------------------------------------------------------------

    async def fetch_and_validate(
        self,
        symbol: str,
        interval: KlineInterval,
        start: datetime,
        end: datetime,
        sources: Optional[List[str]] = None,
        threshold: float = 0.01,
    ) -> ValidatedData:
        """Fetch from multiple exchanges and cross-validate.

        Parameters
        ----------
        symbol : str
            Trading pair.
        interval : KlineInterval
            Bar interval.
        start, end : datetime
            Time range.
        sources : list[str], optional
            Exchanges to compare (default: first two in ``self._exchanges``).
        threshold : float
            Maximum allowed relative deviation between sources
            (default 0.01 = 1 %).

        Returns
        -------
        ValidatedData
        """
        if sources is None:
            sources = self._exchanges[:2]

        if len(sources) < 2:
            # Only one source — just fetch without validation
            data = await self.fetch(symbol, interval, start, end, exchange=sources[0])
            return ValidatedData(primary_data=data, is_valid=True)

        # Fetch from all sources
        source_data: dict[str, pd.DataFrame] = {}
        for src in sources:
            print(f"[validate] Fetching from {src}...")
            df = await self.fetch(symbol, interval, start, end, exchange=src)
            if not df.empty:
                source_data[src] = df
            else:
                print(f"[validate] WARNING: {src} returned 0 bars")

        if not source_data:
            return ValidatedData(
                primary_data=pd.DataFrame(
                    columns=["open", "high", "low", "close", "volume"]
                ),
                is_valid=False,
                validation_report={"error": "No data from any source"},
            )

        primary_name = sources[0]
        primary = source_data.get(primary_name)
        if primary is None:
            # Fall back to first available
            primary_name = next(iter(source_data))
            primary = source_data[primary_name]

        # Build close-price comparison matrix
        close_frames: dict[str, pd.Series] = {}
        for name, df in source_data.items():
            close_frames[name] = df["close"]

        merged = pd.DataFrame(close_frames)
        merged = merged.dropna()

        report: dict = {}
        all_valid = True

        # Report sources that returned no data
        for src in sources:
            if src != primary_name and src not in source_data:
                report[src] = {"status": "no_data", "bars_fetched": 0}
                all_valid = False

        for name in source_data:
            if name == primary_name:
                continue
            if name not in merged.columns:
                report[name] = {"status": "no_overlapping_data"}
                all_valid = False
                continue

            diff = (merged[name] - merged[primary_name]) / merged[primary_name]
            abs_diff = diff.abs()

            report[name] = {
                "mean_diff_pct": float(diff.mean() * 100),
                "max_diff_pct": float(abs_diff.max() * 100),
                "std_diff_pct": float(diff.std() * 100),
                "correlation": float(merged[primary_name].corr(merged[name])),
                "bars_compared": len(merged),
                "bars_exceeding_threshold": int((abs_diff > threshold).sum()),
            }

            if abs_diff.max() > threshold:
                all_valid = False

        # Identify anomaly rows
        anomaly_mask = pd.Series(False, index=merged.index)
        for name in source_data:
            if name == primary_name or name not in merged.columns:
                continue
            dev = ((merged[name] - merged[primary_name]) / merged[primary_name]).abs()
            anomaly_mask |= dev > threshold

        anomalies = pd.DataFrame(index=merged.index[anomaly_mask])
        for name in source_data:
            if name in merged.columns:
                anomalies[f"close_{name}"] = merged.loc[anomaly_mask, name]
        if len(source_data) >= 2 and not anomalies.empty:
            cols = [c for c in anomalies.columns if c.startswith("close_")]
            if len(cols) >= 2:
                vals = anomalies[cols].values
                mean_val = np.mean(vals, axis=1)
                max_dev = np.max(
                    np.abs(vals - mean_val[:, None]) / mean_val[:, None], axis=1
                )
                anomalies["max_deviation"] = max_dev

        return ValidatedData(
            primary_data=primary,
            validation_report=report,
            anomalies=anomalies,
            is_valid=all_valid,
        )

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        """Return database statistics."""
        return self._db.stats()

    def close(self) -> None:
        """Close the underlying database connection."""
        self._db.close()
