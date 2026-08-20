"""Single source of truth for bar-timeframe arithmetic.

Replaces the previously duplicated tables in ``quantforge/adapters/ccxt.py``
and ``apps/dashboard/backend/jobs/data.py``. Drifting copies of this table
are exactly how the in-progress-bar divergence (review #3) was born — any
new consumer must import from here, never re-declare the map.
"""

from __future__ import annotations

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


def timeframe_to_ms(timeframe: str) -> int:
    try:
        return _TF_MS[timeframe]
    except KeyError as exc:
        raise ValueError(f"Unsupported timeframe: {timeframe}") from exc
