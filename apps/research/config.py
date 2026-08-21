"""Shared configuration and small helpers for the research app."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")
META = os.path.join(DATA, "meta", "sp500.csv")
EARN = os.path.join(DATA, "earnings")
PRIC = os.path.join(DATA, "prices")
FDIR = os.path.join(DATA, "financials")  # optional quarterly revenue hook
CRYPTO = os.path.join(DATA, "crypto")  # crypto OHLCV / funding / open-interest raws
OPTN = os.path.join(DATA, "options")  # option chain snapshots
MANIFEST = os.path.join(DATA, "manifest.json")
DB = os.path.join(DATA, "market.duckdb")
EVENTS = os.path.join(DATA, "events.parquet")  # legacy pandas artifact (no longer produced)
SNAP_DIR = os.path.join(os.path.expanduser("~"), ".quantforge", "data-snapshots")

#: Extra high-momentum / deep-surge names not currently in the S&P 500 list,
#: kept so the study's universe always includes them.
EXTRA_SYMBOLS = [
    "SLNO", "AAOI", "BITF", "BTBT", "HOOD", "FIX", "WBD", "NEM", "AXON",
    "TPL", "GEV", "VST", "WDC", "STX", "LRCX",
]

#: Multi-asset research universes (crypto + options).
CRYPTO_SPOTS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "DOGE/USDT"]
CRYPTO_PERPS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]
CRYPTO_TIMEFRAME = "1d"
CRYPTO_LOOKBACK_DAYS = 720  # target depth of daily OHLCV history

#: Liquid equity + ETF universe for live option-chain research (IV/skew/term).
OPTIONS_UNIVERSE = [
    "SPY", "QQQ", "IWM", "TSLA", "NVDA", "AAPL", "MSFT", "AMZN", "META", "NFLX",
]


def crypto_key(symbol: str) -> str:
    """Filesystem-safe key for a ccxt symbol.

    "BTC/USDT" -> "BTCUSDT"; "BTC/USDT:USDT" -> "BTCUSDT_USDT".
    """
    return symbol.replace("/", "").replace(":USDT", "_USDT").replace(":", "_")

#: Event-table report window (inclusive, local Eastern calendar dates).
REPORT_WINDOW_START = "2023-01-01"
REPORT_WINDOW_END = "2026-06-01"


def load_universe() -> list[str]:
    if not os.path.exists(META):
        return []
    df = pd.read_csv(META)
    return [str(s) for s in df["Symbol"].dropna().unique() if str(s) != "nan"]


def load_themes() -> dict[str, str]:
    """Symbol -> theme map from the SP500 meta (GICS Sub-Industry, unbiased taxonomy).

    Symbols absent from the CSV get theme `"solo:<SYM>"` so they never cluster
    artificially into a shared bucket.
    """
    themes: dict[str, str] = {}
    if os.path.exists(META):
        df = pd.read_csv(META)
        for _, r in df.iterrows():
            sub = str(r.get("GICS Sub-Industry") or "").strip()
            themes[str(r["Symbol"])] = sub or "未分类"
    return themes


def dir_stats(path: str) -> dict[str, Any]:
    if not os.path.isdir(path):
        return {"files": 0, "size_mb": 0.0, "latest_mtime": None}
    names = [f for f in os.listdir(path) if f.endswith(".csv")]
    total = sum(os.path.getsize(os.path.join(path, f)) for f in names)
    mtimes = [os.path.getmtime(os.path.join(path, f)) for f in names]
    return {
        "files": len(names),
        "size_mb": round(total / 1e6, 1),
        "latest_mtime": (
            datetime.fromtimestamp(max(mtimes), tz=timezone.utc).isoformat() if mtimes else None
        ),
    }


def open_db(read_only: bool = True):
    """Open the DuckDB warehouse; raise a clear error if it does not exist yet."""
    import duckdb

    if not os.path.exists(DB):
        raise SystemExit("no database yet — run: python -m apps.research events")
    return duckdb.connect(DB, read_only=read_only)


def db_status() -> dict[str, Any] | None:
    """Table-level overview of market.duckdb (or None if it does not exist)."""
    if not os.path.exists(DB):
        return None
    import duckdb

    out: dict[str, Any] = {}
    con = duckdb.connect(DB, read_only=True)
    try:
        tables = con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
        out["tables"] = [t[0] for t in tables]
        for t in out["tables"]:
            if t == "catalog":
                continue
            out[t] = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            try:
                rng = con.execute(f'SELECT min(date), max(date) FROM "{t}"').fetchone()
                out[f"{t}_range"] = [str(x) for x in rng] if rng and any(rng) else None
            except Exception:  # noqa: BLE001
                pass
    finally:
        con.close()
    return out


def load_manifest() -> dict[str, Any] | None:
    if not os.path.exists(MANIFEST):
        return None
    with open(MANIFEST) as fh:
        return json.load(fh)
