"""Tests for KlineDatabase SQLite cache and CachedDataProvider."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nexustrader.backtest.data.database import KlineDatabase
from nexustrader.backtest.data.cached_provider import CachedDataProvider, ValidatedData


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ohlcv(
    n: int = 100,
    start: str = "2024-01-01",
    freq: str = "1h",
    base_price: float = 40000.0,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate synthetic OHLCV data."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq=freq)
    close = base_price + np.cumsum(rng.normal(0, 50, size=n))
    close = np.maximum(close, 100)  # keep positive
    spread = np.abs(rng.normal(0, 20, size=n))
    return pd.DataFrame(
        {
            "open": close + rng.normal(0, 10, size=n),
            "high": close + spread,
            "low": close - spread,
            "close": close,
            "volume": rng.lognormal(10, 1, size=n),
        },
        index=idx,
    )


# ===================================================================
# KlineDatabase
# ===================================================================


class TestKlineDatabase:
    @pytest.fixture
    def db(self, tmp_path: Path):
        """Create a temp database."""
        db = KlineDatabase(tmp_path / "test.db")
        yield db
        db.close()

    @pytest.fixture
    def sample_df(self) -> pd.DataFrame:
        return _make_ohlcv(100, start="2024-06-01", freq="1h")

    def test_save_and_load(self, db: KlineDatabase, sample_df: pd.DataFrame) -> None:
        """Round-trip save → load returns the same data."""
        n = db.save("bitget", "BTC/USDT:USDT", "1h", sample_df)
        assert n == len(sample_df)

        loaded = db.load(
            "bitget",
            "BTC/USDT:USDT",
            "1h",
            start=sample_df.index[0].to_pydatetime(),
            end=sample_df.index[-1].to_pydatetime(),
        )
        assert len(loaded) == len(sample_df)
        assert set(loaded.columns) == {"open", "high", "low", "close", "volume"}
        # Close prices should match (within float precision)
        np.testing.assert_allclose(
            loaded["close"].values, sample_df["close"].values, rtol=1e-6
        )

    def test_save_empty_dataframe(self, db: KlineDatabase) -> None:
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        assert db.save("bitget", "BTC/USDT:USDT", "1h", df) == 0

    def test_duplicates_ignored(
        self, db: KlineDatabase, sample_df: pd.DataFrame
    ) -> None:
        """Inserting the same data twice doesn't produce duplicates."""
        db.save("bitget", "BTC/USDT:USDT", "1h", sample_df)
        db.save("bitget", "BTC/USDT:USDT", "1h", sample_df)

        loaded = db.load(
            "bitget",
            "BTC/USDT:USDT",
            "1h",
            start=sample_df.index[0].to_pydatetime(),
            end=sample_df.index[-1].to_pydatetime(),
        )
        assert len(loaded) == len(sample_df)

    def test_has_data_true(self, db: KlineDatabase, sample_df: pd.DataFrame) -> None:
        db.save("bitget", "BTC/USDT:USDT", "1h", sample_df)
        assert db.has_data(
            "bitget",
            "BTC/USDT:USDT",
            "1h",
            start=sample_df.index[0].to_pydatetime(),
            end=sample_df.index[-1].to_pydatetime(),
        )

    def test_has_data_false_empty(self, db: KlineDatabase) -> None:
        assert not db.has_data(
            "bitget",
            "BTC/USDT:USDT",
            "1h",
            start=datetime(2024, 1, 1),
            end=datetime(2024, 6, 1),
        )

    def test_has_data_false_partial(self, db: KlineDatabase) -> None:
        """Data covering only middle of range → has_data returns False."""
        middle = _make_ohlcv(50, start="2024-03-01", freq="1h")
        db.save("bitget", "BTC/USDT:USDT", "1h", middle)
        assert not db.has_data(
            "bitget",
            "BTC/USDT:USDT",
            "1h",
            start=datetime(2024, 1, 1),
            end=datetime(2024, 6, 1),
        )

    def test_get_gaps_no_data(self, db: KlineDatabase) -> None:
        gaps = db.get_gaps(
            "bitget",
            "BTC/USDT:USDT",
            "1h",
            start=datetime(2024, 1, 1),
            end=datetime(2024, 6, 1),
        )
        assert len(gaps) == 1

    def test_get_gaps_full_coverage(
        self, db: KlineDatabase, sample_df: pd.DataFrame
    ) -> None:
        db.save("bitget", "BTC/USDT:USDT", "1h", sample_df)
        gaps = db.get_gaps(
            "bitget",
            "BTC/USDT:USDT",
            "1h",
            start=sample_df.index[0].to_pydatetime(),
            end=sample_df.index[-1].to_pydatetime(),
        )
        assert len(gaps) == 0

    def test_get_gaps_with_hole(self, db: KlineDatabase) -> None:
        """Save two separate chunks → gap in the middle."""
        chunk1 = _make_ohlcv(24, start="2024-06-01 00:00", freq="1h", seed=1)
        chunk2 = _make_ohlcv(24, start="2024-06-05 00:00", freq="1h", seed=2)
        db.save("bitget", "BTC/USDT:USDT", "1h", chunk1)
        db.save("bitget", "BTC/USDT:USDT", "1h", chunk2)

        gaps = db.get_gaps(
            "bitget",
            "BTC/USDT:USDT",
            "1h",
            start=datetime(2024, 6, 1),
            end=datetime(2024, 6, 6),
        )
        # There should be a gap between the two chunks
        assert len(gaps) >= 1

    def test_stats(self, db: KlineDatabase, sample_df: pd.DataFrame) -> None:
        db.save("bitget", "BTC/USDT:USDT", "1h", sample_df)
        s = db.stats()
        assert s["total_rows"] == len(sample_df)
        assert "bitget" in s["exchanges"]
        assert "BTC/USDT:USDT" in s["symbols"]
        assert s["db_size_mb"] > 0
        assert "db_path" in s

    def test_load_empty_range(self, db: KlineDatabase) -> None:
        loaded = db.load(
            "bitget",
            "BTC/USDT:USDT",
            "1h",
            start=datetime(2020, 1, 1),
            end=datetime(2020, 1, 2),
        )
        assert loaded.empty
        assert set(loaded.columns) == {"open", "high", "low", "close", "volume"}

    def test_isolation_by_exchange(self, db: KlineDatabase) -> None:
        """Data saved under one exchange is not visible for another."""
        df = _make_ohlcv(10, start="2024-06-01", freq="1h")
        db.save("bitget", "BTC/USDT:USDT", "1h", df)
        loaded = db.load(
            "binance",
            "BTC/USDT:USDT",
            "1h",
            start=df.index[0].to_pydatetime(),
            end=df.index[-1].to_pydatetime(),
        )
        assert loaded.empty

    def test_isolation_by_symbol(self, db: KlineDatabase) -> None:
        df = _make_ohlcv(10, start="2024-06-01", freq="1h")
        db.save("bitget", "BTC/USDT:USDT", "1h", df)
        loaded = db.load(
            "bitget",
            "ETH/USDT:USDT",
            "1h",
            start=df.index[0].to_pydatetime(),
            end=df.index[-1].to_pydatetime(),
        )
        assert loaded.empty

    def test_isolation_by_interval(self, db: KlineDatabase) -> None:
        df = _make_ohlcv(10, start="2024-06-01", freq="1h")
        db.save("bitget", "BTC/USDT:USDT", "1h", df)
        loaded = db.load(
            "bitget",
            "BTC/USDT:USDT",
            "15m",
            start=df.index[0].to_pydatetime(),
            end=df.index[-1].to_pydatetime(),
        )
        assert loaded.empty

    def test_context_manager(self, tmp_path: Path) -> None:
        with KlineDatabase(tmp_path / "ctx.db") as db:
            df = _make_ohlcv(5, start="2024-01-01", freq="1h")
            db.save("bitget", "BTC/USDT:USDT", "1h", df)
            assert db.stats()["total_rows"] == 5

    def test_interval_seconds(self) -> None:
        assert KlineDatabase._interval_seconds("1m") == 60
        assert KlineDatabase._interval_seconds("15m") == 900
        assert KlineDatabase._interval_seconds("1h") == 3600
        assert KlineDatabase._interval_seconds("4h") == 14400
        assert KlineDatabase._interval_seconds("1d") == 86400


# ===================================================================
# ValidatedData dataclass
# ===================================================================


class TestValidatedData:
    def test_defaults(self) -> None:
        vd = ValidatedData(primary_data=pd.DataFrame())
        assert vd.is_valid is True
        assert vd.validation_report == {}
        assert vd.anomalies.empty

    def test_with_report(self) -> None:
        vd = ValidatedData(
            primary_data=pd.DataFrame({"close": [1, 2, 3]}),
            validation_report={"binance": {"max_diff_pct": 0.5}},
            is_valid=False,
        )
        assert not vd.is_valid
        assert "binance" in vd.validation_report


# ===================================================================
# CachedDataProvider (unit tests — no actual exchange calls)
# ===================================================================


class TestCachedDataProviderUnit:
    """Unit tests for CachedDataProvider that don't require network access."""

    def test_stats_empty(self, tmp_path: Path) -> None:
        provider = CachedDataProvider(exchanges=["bitget"], db_path=tmp_path / "t.db")
        s = provider.stats()
        assert s["total_rows"] == 0
        provider.close()

    def test_close(self, tmp_path: Path) -> None:
        """close() should not raise."""
        provider = CachedDataProvider(db_path=tmp_path / "t.db")
        provider.close()
