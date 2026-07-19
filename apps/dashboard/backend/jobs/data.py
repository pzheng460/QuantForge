"""Shared data plumbing: Pine source / date-range resolution and OHLCV fetching."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


def _apply_sizing_override(
    runtime, position_size_usdt: Optional[float], leverage: float
) -> None:
    """Thin shim around ``PineRuntime.apply_sizing_override`` — keeps the
    backend-side call sites stable while routing through the single source
    of truth so every backtest/optimize/live path produces the same trades
    for the same params.
    """
    runtime.apply_sizing_override(position_size_usdt, leverage)


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
}

_STRATEGIES_DIR = (
    Path(__file__).resolve().parents[4] / "quantforge" / "pine" / "strategies"
)


def _apply_config_override(source: str, config_override: Optional[dict] = None) -> str:
    # Apply config_override: replace input default values
    if config_override:
        import json
        import re

        number_pattern = r"-?(?:\d+(?:\.\d*)?|\.\d+)"
        string_pattern = r'"(?:\\.|[^"\\])*"'
        bool_pattern = r"(?:true|false)"
        literal_pattern = rf"(?:{number_pattern}|{string_pattern}|{bool_pattern})"

        def pine_literal(value: Any) -> str:
            if isinstance(value, bool):
                return "true" if value else "false"
            if isinstance(value, (int, float)):
                return str(value)
            return json.dumps(str(value))

        for var_name, value in config_override.items():
            replacement = pine_literal(value)
            defval_pattern = (
                rf"(^\s*{re.escape(var_name)}\s*=\s*"
                rf"input\.(?:int|float|bool|string)\([^\n)]*\bdefval\s*=\s*)"
                rf"({literal_pattern})"
            )
            source, count = re.subn(
                defval_pattern,
                lambda match, replacement=replacement: f"{match.group(1)}{replacement}",
                source,
                flags=re.MULTILINE,
            )
            if count:
                continue

            positional_pattern = (
                rf"(^\s*{re.escape(var_name)}\s*=\s*"
                rf"input\.(?:int|float|bool|string)\(\s*)"
                rf"({literal_pattern})"
            )
            source = re.sub(
                positional_pattern,
                lambda match, replacement=replacement: f"{match.group(1)}{replacement}",
                source,
                flags=re.MULTILINE,
            )

    return source


def _resolve_pine_source(
    strategy: Optional[str],
    pine_source: Optional[str],
    config_override: Optional[dict] = None,
) -> str:
    """Return Pine Script source from either raw source or strategy file name."""
    if pine_source:
        return _apply_config_override(pine_source, config_override)

    pine_file = _STRATEGIES_DIR / f"{strategy}.pine"
    if not pine_file.exists():
        raise FileNotFoundError(f"Strategy file not found: {pine_file}")

    return _apply_config_override(pine_file.read_text(), config_override)


def _resolve_date_range(
    period: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
) -> tuple[str, str]:
    """Return (start_str, end_str) from either explicit dates or period shorthand."""
    if start_date:
        return start_date, end_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    days = _PERIOD_DAYS.get(period or "1y", 365)
    end_dt = (
        datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if end_date
        else datetime.now(timezone.utc)
    )
    start_dt = end_dt - timedelta(days=days)
    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")


# Derived from quantforge.pine.live.connector._TF_SECONDS so the live
# engine and the backend's WFO window math never disagree on
# which timeframes exist or how long they are.
def _build_tf_ms() -> dict[str, int]:
    from quantforge.pine.live.connector import _TF_SECONDS

    return {tf: secs * 1000 for tf, secs in _TF_SECONDS.items()}


_TF_MS = _build_tf_ms()


def _fetch_ohlcv(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    since_ms: int,
    end_ms: int,
) -> list[list]:
    """Fetch OHLCV bars — delegates to the shared :func:`fetch_klines`.

    Both backtest and live engines use the same pager so they agree on
    which bars belong to a given time range (no off-by-one or dedup
    discrepancies at boundaries).
    """
    from quantforge.pine.live.connector import fetch_klines

    rows = fetch_klines(
        symbol=symbol,
        exchange_id=exchange_id,
        timeframe=timeframe,
        since_ms=since_ms,
        end_ms=end_ms,
        page_limit=200,
    )
    if not rows:
        raise ValueError("No OHLCV data returned from exchange")
    return rows


def _ohlcv_to_bars(all_ohlcv: list[list]) -> list:
    """Convert raw OHLCV lists to BarData objects."""
    from quantforge.pine.interpreter.context import BarData

    return [
        BarData(
            open=bar[1],
            high=bar[2],
            low=bar[3],
            close=bar[4],
            volume=bar[5],
            time=bar[0] // 1000,
        )
        for bar in all_ohlcv
    ]
