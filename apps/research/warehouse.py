"""DuckDB warehouse: import raw CSVs, build the SQL `events` table, inspect,
verify, snapshot and restore the data layer."""
from __future__ import annotations

import json
import os
import tarfile
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from . import config
from .sql import SQL_BUILD_EVENTS

COLUMNS_PRICE = ("open", "high", "low", "close", "volume")


# --------------------------------------------------------------------------- #
# raw -> duckdb
# --------------------------------------------------------------------------- #
def _load_prices_df() -> pd.DataFrame:
    frames = []
    for f in sorted(os.listdir(config.PRIC)):
        if not f.endswith(".csv"):
            continue
        sym = f[:-4]
        df = pd.read_csv(os.path.join(config.PRIC, f), index_col=0, parse_dates=True)
        if df.empty or "Close" not in df.columns:
            continue
        df = df[["Open", "High", "Low", "Close", "Volume"]]
        df["symbol"] = sym
        frames.append(df.reset_index())
    return pd.concat(frames, ignore_index=True)


def _load_earnings_df() -> pd.DataFrame:
    frames = []
    for f in sorted(os.listdir(config.EARN)):
        if not f.endswith(".csv"):
            continue
        sym = f[:-4]
        df = pd.read_csv(os.path.join(config.EARN, f), index_col=0)
        idx = pd.to_datetime(df.index, errors="coerce", utc=True)
        df.index = idx.tz_localize(None)
        df.index.name = "_report_date"
        df = df.reset_index().rename(columns={"_report_date": "report_date"})
        df.insert(0, "symbol", sym)
        frames.append(df)
    merged = pd.concat(frames, ignore_index=True)
    return merged[pd.notna(merged["report_date"])]


def import_raw() -> None:
    """Rebuild prices/earnings/catalog tables from the raw CSVs, then the SQL events table."""
    import duckdb

    if os.path.exists(config.DB):
        os.remove(config.DB)
    con = duckdb.connect(config.DB)
    try:
        print("loading prices...", flush=True)
        pr = _load_prices_df()
        con.register("pr", pr)
        con.execute(
            f"CREATE TABLE prices AS SELECT symbol, CAST(date AS DATE) AS date, "
            f"{','.join(COLUMNS_PRICE)} FROM pr",
        )

        print("loading earnings...", flush=True)
        er = _load_earnings_df()
        con.register("er", er)
        con.execute(
            """CREATE TABLE earnings AS
               SELECT symbol, CAST(report_date AS TIMESTAMP) AS report_date,
                      "EPS Estimate" AS eps_est, "Reported EPS" AS eps_actual,
                      "Surprise(%)" AS surprise_pct FROM er"""
        )

        print("loading events via SQL...", flush=True)
        con.execute(SQL_BUILD_EVENTS)

        now = datetime.now(timezone.utc).isoformat()
        con.execute(
            """CREATE TABLE catalog AS
               SELECT 'prices' AS source, symbol, count(*) AS rows_,
                      min(date) AS first_date, max(date) AS last_date
               FROM prices GROUP BY symbol
               UNION ALL
               SELECT 'earnings', symbol, count(*), min(report_date), max(report_date)
               FROM earnings GROUP BY symbol"""
        )
        con.execute("ALTER TABLE catalog ADD COLUMN imported_at VARCHAR")
        con.execute("UPDATE catalog SET imported_at = ?", [now])

        for t in ("prices", "earnings", "events"):
            n = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            print(f"  {t}: {n:,} rows")
    finally:
        con.close()
    print(f"duckdb -> {config.DB}")


def build_events() -> None:
    """(Re)build only the `events` table from the current prices/earnings tables."""
    import duckdb

    con = duckdb.connect(config.DB)
    try:
        con.execute(SQL_BUILD_EVENTS)
        n = con.execute('SELECT count(*) FROM "events"').fetchone()[0]
        print(f"events table rebuilt: {n:,} rows")
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# inspection
# --------------------------------------------------------------------------- #
def status() -> dict[str, Any]:
    uni = config.load_universe()
    e = config.dir_stats(config.EARN)
    p = config.dir_stats(config.PRIC)
    events = None
    if os.path.exists(config.EVENTS):  # legacy artifact, kept for reference
        try:
            df = pd.read_parquet(config.EVENTS, columns=["symbol", "report_date"])
            events = {"rows": len(df), "symbols": df["symbol"].nunique()}
        except Exception as exc:  # noqa: BLE001
            events = {"error": str(exc)}

    have_earn = set(f[:-4] for f in os.listdir(config.EARN)) if os.path.isdir(config.EARN) else set()
    have_px = set(f[:-4] for f in os.listdir(config.PRIC)) if os.path.isdir(config.PRIC) else set()
    report: dict[str, Any] = {
        "universe": len(uni),
        "earnings": e,
        "prices": p,
        "events_parquet_legacy": events,
        "missing_earn": sorted(set(uni) - have_earn)[:5],
        "missing_px": sorted(set(uni) - have_px)[:5],
    }
    try:
        report["duckdb"] = config.db_status()
    except Exception as exc:  # noqa: BLE001
        report["duckdb"] = f"error: {exc}"
    report["last_manifest"] = config.load_manifest()
    return report


def write_manifest() -> None:
    os.makedirs(config.DATA, exist_ok=True)
    m = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe": len(config.load_universe()),
        "sources": {"earnings": config.dir_stats(config.EARN), "prices": config.dir_stats(config.PRIC)},
        "notes": "Downloaders are idempotent; run `python -m apps.research events` to (re)build.",
    }
    with open(config.MANIFEST, "w") as fh:
        json.dump(m, fh, indent=2, ensure_ascii=False)
    print(f"manifest -> {config.MANIFEST}")


def verify() -> None:
    bad: list[str] = []

    def check_csv(path: str) -> None:
        try:
            df = pd.read_csv(path, index_col=0)
            if len(df) < 4:
                bad.append(f"{path}: too few rows ({len(df)})")
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{path}: {type(exc).__name__}: {exc}")

    for d in (config.EARN, config.PRIC):
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.endswith(".csv"):
                    check_csv(os.path.join(d, f))
    if os.path.exists(config.EVENTS):  # legacy artifact
        try:
            df = pd.read_parquet(config.EVENTS)
            assert df["report_date"].notna().all()
        except Exception as exc:  # noqa: BLE001
            bad.append(f"events.parquet(legacy): {type(exc).__name__}: {exc}")
    if os.path.exists(config.DB):
        try:
            s = config.db_status()
            assert s and "prices" in s and "events" in s
        except Exception as exc:  # noqa: BLE001
            bad.append(f"market.duckdb: {type(exc).__name__}: {exc}")

    n_earn = len([1 for f in os.listdir(config.EARN) if f.endswith(".csv")]) \
        if os.path.isdir(config.EARN) else 0
    n_px = len([1 for f in os.listdir(config.PRIC) if f.endswith(".csv")]) \
        if os.path.isdir(config.PRIC) else 0
    print(f"verified earnings={n_earn} prices={n_px} files, duckdb ok={os.path.exists(config.DB)}")
    if bad:
        print("ISSUES:")
        for b in bad[:30]:
            print(" -", b)
    else:
        print("all files OK")


# --------------------------------------------------------------------------- #
# query / backup
# --------------------------------------------------------------------------- #
def query(sql: str) -> pd.DataFrame:
    con = config.open_db(read_only=True)
    try:
        return con.execute(sql).fetchdf()
    finally:
        con.close()


def snapshot(keep: int = 5) -> str:
    os.makedirs(config.SNAP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(config.SNAP_DIR, f"quantforge-data-{stamp}.tar.gz")
    with tarfile.open(dest, "w:gz") as tar:
        tar.add(config.DATA, arcname="data")
    snaps = sorted(
        f for f in os.listdir(config.SNAP_DIR)
        if f.startswith("quantforge-data-") and f.endswith(".tar.gz")
    )
    for old in snaps[:-keep] if keep > 0 else []:
        os.remove(os.path.join(config.SNAP_DIR, old))
    print(f"snapshot -> {dest} ({round(os.path.getsize(dest)/1e6, 1)} MB)")
    print(f"kept {min(len(snaps), keep)} of {len(snaps)} snapshots in {config.SNAP_DIR}")
    return dest


def restore(snap: str) -> None:
    if not os.path.exists(snap):
        raise SystemExit(f"snapshot not found: {snap}")
    with tarfile.open(snap, "r:gz") as tar:
        tar.extractall(path=config.ROOT, filter="data")
    print(f"restored {snap} -> {config.DATA}")


# --------------------------------------------------------------------------- #
# Multi-asset layer: crypto (OHLCV/funding/OI) + option chain snapshots.
# --------------------------------------------------------------------------- #
def import_crypto() -> dict[str, int]:
    """Load data/crypto/*.csv into crypto_ohlcv / crypto_funding / crypto_oi.

    Returns per-table row counts.
    """
    import duckdb

    crypto_csv = os.path.join(config.DATA, "crypto")
    out: dict[str, int] = {}
    if not os.path.isdir(crypto_csv):
        return out
    specs = {
        "crypto_ohlcv": "ohlcv_",
        "crypto_funding": "funding_",
        "crypto_oi": "oi_",
    }
    con = duckdb.connect(config.DB)
    try:
        for table, prefix in specs.items():
            files = sorted(f for f in os.listdir(crypto_csv) if f.startswith(prefix) and f.endswith(".csv"))
            if not files:
                continue
            frames = []
            for f in files:
                df = pd.read_csv(os.path.join(crypto_csv, f))
                symbol = f[len(prefix) : -4]
                df["symbol"] = symbol
                frames.append(df)
            raw = pd.concat(frames, ignore_index=True)
            con.register("raw", raw)
            con.execute(f"DROP TABLE IF EXISTS {table}")
            if table == "crypto_ohlcv":
                con.execute((
                    f"CREATE TABLE {table} AS SELECT symbol, "
                    "CAST(ts AS TIMESTAMP) AS ts, open, high, low, close, volume FROM raw"
                ))
            else:
                key = "funding_rate" if table == "crypto_funding" else "open_interest"
                con.execute((
                    f"CREATE TABLE {table} AS SELECT symbol, CAST(ts AS TIMESTAMP) AS ts, "
                    f'"{key}" AS value FROM raw'
                ))
            out[table] = int(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    finally:
        con.close()
    return out


def import_option_chains() -> int:
    """Load data/options/chains_*.csv into `option_chains`."""
    import duckdb

    opt_dir = os.path.join(config.DATA, "options")
    if not os.path.isdir(opt_dir):
        return 0
    frames = []
    for f in sorted(os.listdir(opt_dir)):
        if f.startswith("chains_") and f.endswith(".csv"):
            frames.append(pd.read_csv(os.path.join(opt_dir, f)))
    if not frames:
        return 0
    raw = pd.concat(frames, ignore_index=True)
    con = duckdb.connect(config.DB)
    try:
        con.register("raw", raw)
        con.execute("DROP TABLE IF EXISTS option_chains")
        con.execute("""
            CREATE TABLE option_chains AS
            SELECT CAST(snapshot_at AS TIMESTAMP) AS snapshot_at, ticker, symbol,
                   CAST(expiration AS DATE) AS expiration, strike, "right",
                   bid, ask, last, iv, delta, gamma, theta, vega,
                   open_interest, volume
            FROM raw
        """)
        n = int(con.execute("SELECT count(*) FROM option_chains").fetchone()[0])
    finally:
        con.close()
    return n
