"""OHLCV fetch parity for the dashboard's historical pager.

The dashboard backtest/optimize jobs get ccxt OHLCV through
``apps.dashboard.backend.jobs.data._fetch_crypto_ohlcv``, which now delegates
to the canonical fetcher ``quantforge.adapters.ccxt.fetch_klines``. Together
they must agree on the ONE bar that historically diverged: the
currently-in-progress (partial) candle that exchanges return as the last row
of ``fetch_ohlcv``. Backtests only ever see completed bars, so it must be
dropped here too.
"""

from __future__ import annotations

import sys
import types

import ccxt as real_ccxt

from apps.dashboard.backend.jobs import data as data_jobs
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


def _rows(monkeypatch, since_ms: int, end_ms: int) -> list[list]:
    last_closed_start_ms = ((NOW_MS // BAR_MS) - 1) * BAR_MS
    rows = [
        [last_closed_start_ms - 4 * BAR_MS, 99, 100, 98, 99.5, 1000],
        [last_closed_start_ms - 3 * BAR_MS, 100, 101, 99, 100.5, 1000],
        [last_closed_start_ms - 2 * BAR_MS, 100, 102, 99, 101.0, 1000],
        [last_closed_start_ms - 1 * BAR_MS, 101, 102, 100, 101.5, 1000],
        [last_closed_start_ms, 102, 103, 101, 102.0, 1000],  # last closed bar
        [last_closed_start_ms + BAR_MS, 103, 104, 102, 103.0, 1000],  # partial
    ]
    # fetch_klines does `import ccxt` inside the function, so stub it in
    # sys.modules with a module exposing real exception classes (needed for
    # the transient-error tuple) plus our scripted exchange class.
    fake = types.ModuleType("ccxt")
    for name in ("NetworkError", "RequestTimeout"):
        setattr(fake, name, getattr(real_ccxt, name))
    fake.fake_exchange = _fake_exchange(rows)
    monkeypatch.setitem(sys.modules, "ccxt", fake)
    monkeypatch.setattr(ccxt_mod.time, "time", lambda: NOW_MS / 1000.0)

    return data_jobs._fetch_crypto_ohlcv(
        "fake_exchange",
        "BTC/USDT:USDT",
        "1h",
        since_ms,
        end_ms,
    )


def test_dashboard_pager_drops_in_progress_bar_when_end_is_now(monkeypatch):
    """With end_ms = now, the partial candle must NOT reach the backtest."""
    last_closed_start_ms = ((NOW_MS // BAR_MS) - 1) * BAR_MS
    result = _rows(monkeypatch, since_ms=last_closed_start_ms - 5 * BAR_MS, end_ms=NOW_MS)

    timestamps = [row[0] for row in result]
    assert len(timestamps) == 5
    assert timestamps == sorted(
        [last_closed_start_ms - i * BAR_MS for i in range(5)]
    )
    assert last_closed_start_ms + BAR_MS not in timestamps  # partial candle out


def test_dashboard_pager_historical_window_unchanged(monkeypatch):
    """A fully-past window (end < any partial candle) is unaffected."""
    last_closed_start_ms = ((NOW_MS // BAR_MS) - 1) * BAR_MS
    end_ms = last_closed_start_ms  # window ends at the last closed bar
    since_ms = last_closed_start_ms - 2 * BAR_MS
    result = _rows(monkeypatch, since_ms=since_ms, end_ms=end_ms)

    assert [row[0] for row in result] == [
        last_closed_start_ms - 2 * BAR_MS,
        last_closed_start_ms - 1 * BAR_MS,
    ]
