"""Shared date-range and multi-asset OHLCV data plumbing."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

_PERIOD_DAYS = {
    "1w": 7,
    "1m": 30,
    "3m": 90,
    "6m": 180,
    "1y": 365,
    "2y": 730,
    "3y": 1095,
    "5y": 1825,
}

_DEFAULT_SYMBOLS = {
    "bitget": "BTC/USDT:USDT",
    "binance": "BTC/USDT:USDT",
    "okx": "BTC/USDT:USDT",
    "bybit": "BTC/USDT:USDT",
    "hyperliquid": "BTC/USDT:USDT",
    "schwab": "AAPL",
}

_TF_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "12h": 43200,
    "1d": 86400,
    "1w": 604800,
}
_TF_MS = {name: seconds * 1000 for name, seconds in _TF_SECONDS.items()}


def timeframe_to_seconds(timeframe: str) -> int:
    try:
        return _TF_SECONDS[timeframe]
    except KeyError as exc:
        raise ValueError(f"Unsupported timeframe: {timeframe}") from exc


def _resolve_date_range(
    period: str | None, start_date: str | None, end_date: str | None
) -> tuple[str, str]:
    if start_date:
        return start_date, end_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    days = _PERIOD_DAYS.get(period or "1y", 365)
    end = (
        datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if end_date
        else datetime.now(timezone.utc)
    )
    return (end - timedelta(days=days)).strftime("%Y-%m-%d"), end.strftime(
        "%Y-%m-%d"
    )


def _retry(
    operation: Callable[[], T],
    errors: tuple[type[BaseException], ...],
    attempts: int = 3,
) -> T:
    last_error: BaseException | None = None
    for attempt in range(attempts):
        try:
            return operation()
        except errors as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    assert last_error is not None
    raise last_error


def _fetch_crypto_ohlcv(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    since_ms: int,
    end_ms: int,
) -> list[list]:
    import ccxt

    exchange_cls = getattr(ccxt, exchange_id, None)
    if exchange_cls is None:
        raise ValueError(f"Exchange '{exchange_id}' not found in ccxt")
    exchange = exchange_cls({"enableRateLimit": True, "timeout": 30_000})
    transient = (
        ccxt.NetworkError,
        ccxt.RequestTimeout,
        TimeoutError,
        ConnectionError,
    )
    _retry(exchange.load_markets, transient)
    page_limit = 1000
    bar_ms = timeframe_to_seconds(timeframe) * 1000
    # Align with the canonical fetcher (quantforge/adapters/ccxt.py
    # fetch_klines): drop only the currently-in-progress bar. Exchanges
    # return it as the last row of fetch_ohlcv with partial OHLC; letting it
    # through would make dashboard backtests diverge from CLI/quantforge
    # backtests, which only ever see completed bars. The boundary is the
    # START of the forming bar (not the previous bar's start, which would
    # also drop the most recent completed bar).
    now_ms = int(time.time() * 1000)
    current_start_ms = (now_ms // bar_ms) * bar_ms
    effective_end_ms = min(end_ms, current_start_ms)
    seen: set[int] = set()
    rows: list[list] = []
    cursor = since_ms
    while cursor < effective_end_ms:
        chunk = _retry(
            lambda: exchange.fetch_ohlcv(
                symbol, timeframe, since=cursor, limit=page_limit
            ),
            transient,
        )
        if not chunk:
            cursor += bar_ms * page_limit
            continue
        for row in chunk:
            if since_ms <= row[0] < effective_end_ms and row[0] not in seen:
                rows.append(row)
                seen.add(row[0])
        cursor = max(cursor + 1, chunk[-1][0] + 1)
    return sorted(rows, key=lambda row: row[0])


def _fetch_schwab_ohlcv(
    symbol: str, timeframe: str, since_ms: int, end_ms: int
) -> list[list]:
    from quantforge.brokers.schwab import SchwabConnector, credentials_for

    config_path = Path.home() / ".quantforge/schwab/config.json"
    try:
        account_hash = json.loads(config_path.read_text()).get("account_hash")
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        account_hash = None
    connector = SchwabConnector(
        credentials_for("trading"),
        market_credentials=credentials_for("market_data"),
        account_hash=account_hash,
    )
    rows = connector.fetch_chart_bars(symbol, timeframe)
    return [row for row in rows if since_ms <= row[0] < end_ms]


def _fetch_ohlcv(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    since_ms: int,
    end_ms: int,
) -> list[list]:
    if exchange_id == "schwab":
        rows = _fetch_schwab_ohlcv(symbol, timeframe, since_ms, end_ms)
    else:
        rows = _fetch_crypto_ohlcv(
            exchange_id, symbol, timeframe, since_ms, end_ms
        )
    if not rows:
        raise ValueError("No OHLCV data returned")
    return rows
