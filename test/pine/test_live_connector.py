from __future__ import annotations

import ccxt

from quantforge.pine.live import connector


def test_fetch_klines_retries_load_markets_timeout(monkeypatch):
    class FlakyBitget:
        load_attempts = 0

        def __init__(self, _config):
            pass

        def load_markets(self):
            type(self).load_attempts += 1
            if type(self).load_attempts == 1:
                raise ccxt.RequestTimeout("bitget GET /api/v2/spot/public/coins")

        def fetch_ohlcv(self, *_args, **_kwargs):
            return [[1_700_000_000_000, 1.0, 2.0, 0.5, 1.5, 10.0]]

    monkeypatch.setattr(ccxt, "bitget", FlakyBitget)
    monkeypatch.setattr(connector.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(connector.time, "time", lambda: 1_800_000_000)

    rows = connector.fetch_klines(
        symbol="BTC/USDT:USDT",
        exchange_id="bitget",
        timeframe="1h",
        since_ms=1_700_000_000_000,
        end_ms=1_700_000_000_001,
    )

    assert FlakyBitget.load_attempts == 2
    assert rows == [[1_700_000_000_000, 1.0, 2.0, 0.5, 1.5, 10.0]]


def test_fetch_klines_retries_fetch_ohlcv_timeout(monkeypatch):
    class FlakyBitget:
        fetch_attempts = 0

        def __init__(self, _config):
            pass

        def load_markets(self):
            pass

        def fetch_ohlcv(self, *_args, **_kwargs):
            type(self).fetch_attempts += 1
            if type(self).fetch_attempts == 1:
                raise ccxt.RequestTimeout("bitget GET /api/v2/mix/market/candles")
            return [[1_700_000_000_000, 1.0, 2.0, 0.5, 1.5, 10.0]]

    monkeypatch.setattr(ccxt, "bitget", FlakyBitget)
    monkeypatch.setattr(connector.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(connector.time, "time", lambda: 1_800_000_000)

    rows = connector.fetch_klines(
        symbol="BTC/USDT:USDT",
        exchange_id="bitget",
        timeframe="1h",
        since_ms=1_700_000_000_000,
        end_ms=1_700_000_000_001,
    )

    assert FlakyBitget.fetch_attempts == 2
    assert rows == [[1_700_000_000_000, 1.0, 2.0, 0.5, 1.5, 10.0]]


def test_fetch_klines_excludes_end_boundary_bar(monkeypatch):
    class BoundaryBitget:
        def __init__(self, _config):
            pass

        def load_markets(self):
            pass

        def fetch_ohlcv(self, *_args, **_kwargs):
            return [
                [1_700_000_000_000, 1.0, 2.0, 0.5, 1.5, 10.0],
                [1_700_003_600_000, 1.5, 2.0, 1.0, 1.8, 10.0],
            ]

    monkeypatch.setattr(ccxt, "bitget", BoundaryBitget)
    monkeypatch.setattr(connector.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(connector.time, "time", lambda: 1_800_000_000)

    rows = connector.fetch_klines(
        symbol="BTC/USDT:USDT",
        exchange_id="bitget",
        timeframe="1h",
        since_ms=1_700_000_000_000,
        end_ms=1_700_003_600_000,
    )

    assert rows == [[1_700_000_000_000, 1.0, 2.0, 0.5, 1.5, 10.0]]
