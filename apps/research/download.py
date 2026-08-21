"""Yahoo downloaders for the research app — idempotent and resume-friendly.

Files that already exist on disk are never re-fetched; re-running a download
is a fast cache hit. `force=True` re-downloads.
"""
from __future__ import annotations

import os
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yfinance as yf

from . import config

warnings.filterwarnings("ignore")


def fetch_earnings(
    symbols: list[str],
    *,
    workers: int = 6,
    force: bool = False,
    progress_every: int = 100,
) -> tuple[dict[str, int], list[tuple[str, str]]]:
    """Download per-ticker `earnings_dates` (surprise history) to CSVs.

    Returns (counts, errors) where counts keys are ok/cached/empty/err/...
    """
    os.makedirs(config.EARN, exist_ok=True)
    counts: dict[str, int] = {}
    errors: list[tuple[str, str]] = []

    def fetch_one(sym: str) -> tuple[str, str]:
        path = os.path.join(config.EARN, f"{sym}.csv")
        if not force and os.path.exists(path) and os.path.getsize(path) > 50:
            return sym, "cached"
        last_err = None
        for attempt in range(3):
            try:
                t = yf.Ticker(sym)
                ed = t.earnings_dates
                if ed is None or len(ed) == 0:
                    return sym, "empty"
                ed.to_csv(path)
                return sym, "ok"
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}:{e}"
                time.sleep(2 + attempt * 4)
        return sym, f"err:{last_err}"

    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_one, s): s for s in symbols}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                status = fut.result()
            except Exception as e:  # noqa: BLE001
                status = (sym, f"crash:{e}")
            key = status[1].split(":")[0]
            counts[key] = counts.get(key, 0) + 1
            if status[1].startswith(("err", "crash")):
                errors.append(status)
            done += 1
            if done % progress_every == 0:
                print(f"  {done}/{len(symbols)} elapsed={time.time()-t0:.0f}s {counts}", flush=True)
    return counts, errors


def fetch_prices(
    symbols: list[str],
    *,
    chunk_size: int = 40,
    workers: int = 3,
    years: str = "6y",
    force: bool = False,
) -> tuple[int, list[str]]:
    """Download daily OHLCV for the universe (batched yf.download). Returns (ok, fails)."""
    os.makedirs(config.PRIC, exist_ok=True)

    def download_chunk(chunk: list[str]) -> tuple[list[str], list[str]]:
        ok, fail = [], []
        try:
            data = yf.download(
                chunk, period=years, interval="1d", group_by="ticker",
                auto_adjust=False, threads=False, progress=False,
            )
        except Exception as e:  # noqa: BLE001
            return [], [f"{s}:{type(e).__name__}:{e}" for s in chunk]
        for sym in chunk:
            path = os.path.join(config.PRIC, f"{sym}.csv")
            if not force and os.path.exists(path):
                ok.append(sym)  # already cached
                continue
            try:
                df = data[sym].dropna(how="all") if isinstance(data.columns, pd.MultiIndex) \
                    else data.dropna(how="all")
                if df.empty:
                    fail.append(f"{sym}:empty")
                    continue
                df[[c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]] \
                    .to_csv(path)
                ok.append(sym)
            except Exception as e:  # noqa: BLE001
                fail.append(f"{sym}:{type(e).__name__}:{e}")
        return ok, fail

    todo = [s for s in symbols if force or not os.path.exists(os.path.join(config.PRIC, f"{s}.csv"))]
    cached_n = len(symbols) - len(todo)
    print(f"prices: {len(todo)} to download, {cached_n} cached ({len(symbols)} total)", flush=True)
    chunks = [todo[i : i + chunk_size] for i in range(0, len(todo), chunk_size)]
    fails: list[str] = []
    t0, n_ok = time.time(), 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(download_chunk, c): i for i, c in enumerate(chunks)}
        for fut in as_completed(futs):
            ok, fail = fut.result()
            n_ok += len(ok)
            fails.extend(fail)
            if fail:
                print("  fails:", fail[:5], flush=True)
            print(f"  {n_ok} ok / {len(fails)} fail / {time.time()-t0:.0f}s", flush=True)
    return n_ok, fails


# --------------------------------------------------------------------------- #
# Crypto downloaders (ccxt/bitget public endpoints — no credentials needed).
# Idempotent like the Yahoo path: existing files are never re-fetched.
# --------------------------------------------------------------------------- #
def _ccxt_public():
    import ccxt

    return ccxt.bitget({"enableRateLimit": True, "timeout": 15000})


def _save_crypto_df(path: str, df: pd.DataFrame) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)


def _fetch_paginated_ohlcv(
    ex, symbol: str, timeframe: str, days: int, page: int = 200
) -> pd.DataFrame:
    """Fetch up to `days` of OHLCV, starting from the LATEST bars and walking
    backward (each next window = `oldest - len(raw) x bar_ms`, candle-aligned).

    Stops on: an empty page, or an oldest timestamp that no longer moves
    (server ignoring `since` / history exhausted).
    """
    import time as _t

    bar_ms = {"1d": 86400000, "4h": 14400000, "1h": 3600000}.get(timeframe, 86400000)
    chunks: list[pd.DataFrame] = []
    raw = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=page)  # latest window
    if not raw:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    chunks.append(
        pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    )
    oldest = int(raw[0][0])
    while sum(len(c) for c in chunks) < days:
        since_ms = oldest - int(len(raw) * bar_ms)
        raw = ex.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=page)
        if not raw:
            break
        new_oldest = int(raw[0][0])
        if new_oldest >= oldest:
            break  # server ignored `since` — no more history this way
        chunks.append(
            pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
        )
        oldest = new_oldest
        _t.sleep(0.5)
    df = pd.concat(chunks).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df


def _fetch_daily(ex, symbol: str, days: int) -> pd.DataFrame:
    """Daily OHLCV with a 4h-pagination fallback.

    Some bitget perp endpoints cap a single 1d request at the latest ~90 bars
    and ignore `since`; in that case pull 4h bars (which page fine) and resample.
    """
    df = _fetch_paginated_ohlcv(ex, symbol, "1d", days)
    if len(df) >= min(days, 120):
        return df
    df4 = _fetch_paginated_ohlcv(ex, symbol, "4h", days)
    if len(df4) < 60:
        return df
    res = (
        df4.set_index("ts")
        .resample("1D")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )
    return res


def fetch_crypto(
    *,
    spots: list[str] | None = None,
    perps: list[str] | None = None,
    days: int | None = None,
    force: bool = False,
) -> tuple[dict[str, int], list[str]]:
    """Download crypto OHLCV (+ funding rate + OI for perps) to data/crypto/.

    Returns (counts, errors) like the Yahoo fetch helpers.
    """
    spots = spots if spots is not None else config.CRYPTO_SPOTS
    perps = perps if perps is not None else config.CRYPTO_PERPS
    days = days or config.CRYPTO_LOOKBACK_DAYS
    ex = _ccxt_public()
    counts: dict[str, int] = {}
    errors: list[str] = []
    t0 = time.time()

    for sym in spots + perps:
        key = config.crypto_key(sym)
        kind = "perp" if ":" in sym else "spot"
        ohlcv_p = os.path.join(config.CRYPTO, f"ohlcv_{key}.csv")
        if not force and os.path.exists(ohlcv_p) and os.path.getsize(ohlcv_p) > 500:
            counts["cached"] = counts.get("cached", 0) + 1
        else:
            try:
                df = _fetch_daily(ex, sym, days)
                if len(df) < 60:
                    raise ValueError(f"only {len(df)} bars")
                _save_crypto_df(ohlcv_p, df)
                counts["ok"] = counts.get("ok", 0) + 1
                print(f"  {sym}: {len(df)} bars", flush=True)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{sym}:{type(e).__name__}:{e}")
                counts["err"] = counts.get("err", 0) + 1

        if kind == "perp":
            fund_p = os.path.join(config.CRYPTO, f"funding_{key}.csv")
            if not force and os.path.exists(fund_p) and os.path.getsize(fund_p) > 200:
                counts["cached"] = counts.get("cached", 0) + 1
            else:
                try:
                    raw = ex.fetch_funding_rate_history(sym, limit=300) or []
                    fdf = pd.DataFrame(
                        [
                            {"ts": r["timestamp"], "funding_rate": float(r["fundingRate"])}
                            for r in raw if r.get("fundingRate") is not None
                        ]
                    )
                    fdf["ts"] = pd.to_datetime(fdf["ts"], unit="ms")
                    _save_crypto_df(fund_p, fdf)
                    counts["fund_ok"] = counts.get("fund_ok", 0) + 1
                    print(f"  {sym}: {len(fdf)} funding samples", flush=True)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{sym}#funding:{type(e).__name__}:{e}")

            oi_p = os.path.join(config.CRYPTO, f"oi_{key}.csv")
            old_oi: list[pd.DataFrame] = []
            today = pd.Timestamp.now().date()
            if os.path.exists(oi_p):
                prev = pd.read_csv(oi_p)
                prev_dates = set(pd.to_datetime(prev["ts"]).dt.date)
                if today in prev_dates:
                    counts["cached"] = counts.get("cached", 0) + 1
                    continue
                old_oi.append(prev[pd.to_datetime(prev["ts"]).dt.date < today])
            try:
                cur = ex.fetch_open_interest(sym) or {}
                if cur.get("openInterestAmount") is None:
                    raise ValueError("no openInterestAmount")
                odf = pd.DataFrame(
                    [{"ts": pd.Timestamp.now().date(), "open_interest": float(cur["openInterestAmount"])}]
                )
                _save_crypto_df(oi_p, pd.concat(old_oi + [odf], ignore_index=True))
                counts["oi_ok"] = counts.get("oi_ok", 0) + 1
                print(f"  {sym}: OI 快照点 {len(old_oi)+1}（每日一点累计）", flush=True)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{sym}#oi:{type(e).__name__}:{e}")
        time.sleep(0.4)

    print(f"crypto: {counts} elapsed={time.time()-t0:.0f}s", flush=True)
    return counts, errors
