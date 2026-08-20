"""Canonical OHLCV fetcher boundary tests.

``fetch_klines`` must agree with the dashboard's historical pager on the
currently-in-progress (partial) bar: drop ONLY the forming candle, keep every
completed bar. A regression here drops the most recent completed bar too and
silently lags every live/backtest signal by one full bar.
"""

from __future__ import annotations

import sys
import types

import ccxt as real_ccxt

from quantforge.adapters import ccxt as ccxt_mod

NOW_MS = 1_700_000_000_000
BAR_MS = 3_600_000  # 1h


def _fake_exchange(rows: list[list]):
    class _Exchange:
        def __init__(self, _cfg: dict):
            self._rows = sorted(rows)

        def load_markets(self) -> dict:
            return {}

        def fetch_ohlcv(self, symbol, timeframe, since=0, limit=1000):
            return [r for r in self._rows if r[0] >= since][:limit]

    return _Exchange


def _stub_ccxt(monkeypatch, rows: list[list]) -> None:
    fake = types.ModuleType("ccxt")
    for name in ("NetworkError", "RequestTimeout"):
        setattr(fake, name, getattr(real_ccxt, name))
    fake.fake_exchange = _fake_exchange(rows)
    monkeypatch.setitem(sys.modules, "ccxt", fake)
    monkeypatch.setattr(ccxt_mod.time, "time", lambda: NOW_MS / 1000.0)


def _closed_rows() -> list[list]:
    last_closed_start_ms = ((NOW_MS // BAR_MS) - 1) * BAR_MS
    rows = [
        [last_closed_start_ms - 4 * BAR_MS, 99, 100, 98, 99.5, 1000],
        [last_closed_start_ms - 3 * BAR_MS, 100, 101, 99, 100.5, 1000],
        [last_closed_start_ms - 2 * BAR_MS, 100, 102, 99, 101.0, 1000],
        [last_closed_start_ms - 1 * BAR_MS, 101, 102, 100, 101.5, 1000],
        [last_closed_start_ms, 102, 103, 101, 102.0, 1000],  # most recent closed
        [last_closed_start_ms + BAR_MS, 103, 104, 102, 103.0, 1000],  # partial
    ]
    return rows


def test_fetch_klines_drops_only_the_in_progress_bar(monkeypatch):
    last_closed_start_ms = ((NOW_MS // BAR_MS) - 1) * BAR_MS
    _stub_ccxt(monkeypatch, _closed_rows())

    out = ccxt_mod.fetch_klines(
        "BTC/USDT:USDT",
        "fake_exchange",
        "1h",
        since_ms=last_closed_start_ms - 5 * BAR_MS,
        end_ms=NOW_MS,
    )

    timestamps = [row[0] for row in out]
    assert timestamps == sorted(
        [last_closed_start_ms - i * BAR_MS for i in range(5)]
    )
    # The most recent COMPLETED bar must survive (off-by-one regression:
    # using (now//bar)-1 as the boundary silently dropped it).
    assert last_closed_start_ms in timestamps
    # The partial candle is out.
    assert last_closed_start_ms + BAR_MS not in timestamps


def test_fetch_klines_historical_window_is_left_alone(monkeypatch):
    """A fully-past window (end < the forming bar) is unaffected."""
    last_closed_start_ms = ((NOW_MS // BAR_MS) - 1) * BAR_MS
    _stub_ccxt(monkeypatch, _closed_rows())

    out = ccxt_mod.fetch_klines(
        "BTC/USDT:USDT",
        "fake_exchange",
        "1h",
        since_ms=last_closed_start_ms - 2 * BAR_MS,
        end_ms=last_closed_start_ms,
    )

    assert [row[0] for row in out] == [
        last_closed_start_ms - 2 * BAR_MS,
        last_closed_start_ms - 1 * BAR_MS,
    ]
