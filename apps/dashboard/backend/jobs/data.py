"""Shared date-range and multi-asset OHLCV data plumbing."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def _fetch_crypto_ohlcv(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    since_ms: int,
    end_ms: int,
) -> list[list]:
    """Page ccxt OHLCV through the canonical ``fetch_klines``.

    The dashboard pager used to be a hand-rolled copy of the same pagination;
    the two drifted on the in-progress-bar boundary (review #3). Delegating
    here means the backtest/optimize data can never diverge from the
    quantforge fetcher again.
    """
    from quantforge.adapters.ccxt import fetch_klines

    return fetch_klines(symbol, exchange_id, timeframe, since_ms, end_ms)


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
