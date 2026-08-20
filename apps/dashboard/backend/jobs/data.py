"""Shared date-range and multi-asset OHLCV data plumbing."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from quantforge.domain.timeframes import timeframe_to_seconds

# Hard ceiling on the number of bars a single backtest/optimize job may
# request. Explicit start/end dates bypass the bounded period enum, so this is
# the guard that stops a 1970→now 1m request from paging an unbounded dataset
# or running an unbounded CPU loop.
MAX_BACKTEST_BARS = 2_000_000

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


def check_bar_budget(timeframe: str, start_str: str, end_str: str) -> None:
    """Reject spans whose bar count exceeds MAX_BACKTEST_BARS.

    Called by the backtest/optimize jobs before any fetching; the request
    models bound the span too, but this is the authoritative, timeframe-aware
    ceiling (a 10-year 1d window is fine, a 10-year 1m window is not).
    """
    start_dt = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    tf_sec = timeframe_to_seconds(timeframe)
    bars = int((end_dt - start_dt).total_seconds() // tf_sec) + 1
    if bars > MAX_BACKTEST_BARS:
        raise ValueError(
            f"requested span is about {bars:,} {timeframe} bars; the server "
            f"allows at most {MAX_BACKTEST_BARS:,} — shorten the period or "
            "use a larger timeframe"
        )


def _fetch_crypto_ohlcv(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    since_ms: int,
    end_ms: int,
    cancel_check: Callable[[], None] | None = None,
) -> list[list]:
    """Page ccxt OHLCV through the canonical ``fetch_klines``.

    The dashboard pager used to be a hand-rolled copy of the same pagination;
    the two drifted on the in-progress-bar boundary (review #3). Delegating
    here means the backtest/optimize data can never diverge from the
    quantforge fetcher again.
    """
    from quantforge.adapters.ccxt import fetch_klines

    return fetch_klines(
        symbol,
        exchange_id,
        timeframe,
        since_ms,
        end_ms,
        cancel_check=cancel_check,
    )


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
    cancel_check: Callable[[], None] | None = None,
) -> list[list]:
    if exchange_id == "schwab":
        rows = _fetch_schwab_ohlcv(symbol, timeframe, since_ms, end_ms)
    else:
        rows = _fetch_crypto_ohlcv(
            exchange_id,
            symbol,
            timeframe,
            since_ms,
            end_ms,
            cancel_check=cancel_check,
        )
    if cancel_check is not None:
        cancel_check()
    if not rows:
        raise ValueError("No OHLCV data returned")
    return rows
