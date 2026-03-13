"""Exchange connector wrapper for Pine live trading.

Handles fetching warmup klines and subscribing to live kline streams
using ccxt for historical data.  The actual live WebSocket kline feed
should be wired in by the caller (e.g. via QuantForge engine connectors).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from quantforge.pine.interpreter.context import BarData

logger = logging.getLogger(__name__)

# Timeframe → seconds mapping (subset)
_TF_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "1d": 86400,
}


def timeframe_to_seconds(tf: str) -> int:
    """Convert a timeframe string like '15m' to seconds."""
    if tf in _TF_SECONDS:
        return _TF_SECONDS[tf]
    raise ValueError(f"Unsupported timeframe: {tf}")


def fetch_warmup_bars(
    symbol: str,
    exchange_id: str,
    timeframe: str,
    num_bars: int = 500,
) -> list[BarData]:
    """Fetch historical bars for indicator warmup via ccxt.

    Returns a list of ``BarData`` sorted oldest-first.
    """
    import ccxt

    exchange_cls = getattr(ccxt, exchange_id, None)
    if exchange_cls is None:
        raise ValueError(f"Exchange '{exchange_id}' not found in ccxt")

    exchange = exchange_cls({"enableRateLimit": True})
    exchange.load_markets()

    tf_sec = timeframe_to_seconds(timeframe)
    now = datetime.now(timezone.utc)
    since_dt = now - timedelta(seconds=tf_sec * num_bars)
    since_ms = int(since_dt.timestamp() * 1000)

    all_ohlcv: list[list] = []
    current_since = since_ms
    limit = 1000

    while len(all_ohlcv) < num_bars:
        ohlcv = exchange.fetch_ohlcv(
            symbol, timeframe, since=current_since, limit=limit
        )
        if not ohlcv:
            break
        all_ohlcv.extend(ohlcv)
        last_ts = ohlcv[-1][0]
        if last_ts <= current_since:
            break
        current_since = last_ts + 1

    # De-dup by timestamp and sort
    seen: set[int] = set()
    unique: list[list] = []
    for bar in all_ohlcv:
        ts = bar[0]
        if ts not in seen:
            seen.add(ts)
            unique.append(bar)
    unique.sort(key=lambda b: b[0])

    # Convert to BarData
    bars = [
        BarData(
            open=bar[1],
            high=bar[2],
            low=bar[3],
            close=bar[4],
            volume=bar[5],
            time=bar[0] // 1000,
        )
        for bar in unique[-num_bars:]
    ]

    logger.info("Fetched %d warmup bars for %s (%s)", len(bars), symbol, exchange_id)
    return bars


def ohlcv_to_bar(ohlcv: list) -> BarData:
    """Convert a single ccxt OHLCV list ``[ts, o, h, l, c, v]`` to BarData."""
    return BarData(
        open=ohlcv[1],
        high=ohlcv[2],
        low=ohlcv[3],
        close=ohlcv[4],
        volume=ohlcv[5],
        time=ohlcv[0] // 1000,
    )
