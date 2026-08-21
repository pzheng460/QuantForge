#!/usr/bin/env python3
"""QuantForge strategy exploration helper.

Submits backtest/optimize jobs to the local dashboard API and waits for
results. Lightweight wrapper so exploration stays reproducible and scriptable.

Usage:
  python scripts/explore_backtest.py run-backtest <strategy> [--exchange E]
      [--timeframe TF] [--start DATE] [--end DATE] [--json '{"param":val}']
      [--pos USDT] [--warmup N]
  python scripts/explore_backtest.py run-all-backtests [--exchange E]
      [--timeframe TF] [--start DATE] [--end DATE] [--pos USDT]
      (runs every BTC-relevant built-in strategy at default params)
"""

import argparse
import json
import sys
import time

import requests

_API = "https://127.0.0.1:8000/api"

# Strategies relevant to BTC (excludes Schwab options strategy).
BTC_STRATEGIES = [
    "ema_crossover",
    "ema_crossover_v2",
    "ema_crossover_v3",
    "bollinger_band",
    "bollinger_band_v4",
    "bb_squeeze",
    "bb_squeeze_v2",
    "momentum_adx",
    "rsi_momentum",
    "macd_trend",
    "sma_trend",
    "hurst_kalman",
    "dual_regime",
]

POLL_SECS = 3
MAX_POLLS = 400  # up to ~20 min per job


def submit(url: str, payload: dict) -> dict:
    r = requests.post(_API + url, json=payload, timeout=60, verify=False)
    r.raise_for_status()
    return r.json()


def poll_job(kind: str, job_id: str) -> dict:
    for _ in range(MAX_POLLS):
        r = requests.get(
            f"{_API}/{kind}/{job_id}", timeout=30, verify=False
        )
        r.raise_for_status()
        j = r.json()
        status = j.get("status")
        if status in ("completed", "failed", "cancelled"):
            return j
        if status == "failed":
            return j
        time.sleep(POLL_SECS)
    raise TimeoutError(f"job {job_id} did not finish in time")


def summarize(res: dict) -> dict:
    """Pull the key performance numbers out of a backtest result."""
    out = res.get("result") or {}
    keys = [
        "total_return_pct", "bh_return_pct", "annualized_return_pct",
        "max_drawdown_pct", "sharpe_ratio", "sortino_ratio", "calmar_ratio",
        "annualized_volatility_pct", "total_trades", "win_rate_pct",
        "profit_factor", "payoff_ratio", "expectancy",
    ]
    row = {k: out.get(k) for k in keys}
    row["status"] = res.get("status")
    row["error"] = res.get("error")
    return row


def run_backtest(args) -> dict:
    payload = {
        "strategy": args.strategy,
        "exchange": args.exchange,
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "period": None,
        "start_date": args.start,
        "end_date": args.end,
        "warmup_bars": args.warmup,
    }
    if args.pos:
        payload["position_size_usdt"] = args.pos
    if args.json:
        payload["config_override"] = json.loads(args.json)
    j = submit("/backtest/run", payload)
    res = poll_job("backtest", j["job_id"])
    return summarize(res)


def _payload_for(s: str, args) -> dict:
    p = {
        "strategy": s,
        "exchange": args.exchange,
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "period": None,
        "start_date": args.start,
        "end_date": args.end,
        "warmup_bars": args.warmup,
    }
    if getattr(args, "pos", None):
        p["position_size_usdt"] = args.pos
    return p


def run_all(args) -> None:
    print(f"{'strategy':20} {'ret%':>9} {'bh%':>8} {'sharpe':>7} "
          f"{'maxDD%':>8} {'trades':>7} {'win%':>6} {'PF':>6} {'status'}")
    for s in BTC_STRATEGIES:
        try:
            j = submit("/backtest/run", _payload_for(s, args))
            res = poll_job("backtest", j["job_id"])
            row = summarize(res)
        except Exception as e:  # noqa: BLE001
            row = {"status": "failed", "error": str(e)[:120]}
        ret = row.get("total_return_pct")
        sharpe = row.get("sharpe_ratio")
        mdd = row.get("max_drawdown_pct")
        win = row.get("win_rate_pct")
        print(f"{s:20} {str(ret):>9} "
              f"{str(row.get('bh_return_pct','')):>8} "
              f"{str(round(sharpe,2)) if isinstance(sharpe,(int,float)) else '':>7} "
              f"{str(round(mdd,1)) if isinstance(mdd,(int,float)) else '':>8} "
              f"{str(row.get('total_trades','')):>7} "
              f"{str(round(win,1)) if isinstance(win,(int,float)) else '':>6} "
              f"{str(row.get('profit_factor','')):>6} "
              f"{row.get('status')}")
        sys.stdout.flush()


def run_optimize(args) -> dict:
    """Run a schema-driven grid optimize and wait for the top-level result."""
    payload = {
        "strategy": args.strategy,
        "exchange": args.exchange,
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "period": None,
        "start_date": args.start,
        "end_date": args.end,
        "warmup_bars": args.warmup,
        "mode": args.mode,
        "metric": args.metric,
    }
    if args.pos:
        payload["position_size_usdt"] = args.pos
    j = submit("/optimize/run", payload)
    job_id = j["job_id"]
    # The optimized result lives on the top-level grid_result/wfo_result/full_result.
    for _ in range(MAX_POLLS):
        s = requests.get(f"{_API}/optimize/{job_id}", timeout=30, verify=False).json()
        if s.get("status") in ("completed", "failed", "cancelled"):
            return {**s, "_meta": {"mode": args.mode, "metric": args.metric}}
        time.sleep(POLL_SECS)
    raise TimeoutError(f"optimize job {job_id} did not finish")


def summarize_optimize(res: dict) -> dict:
    """Pull the best grid row / full three-stage result out of an optimize job."""
    out = {"status": res.get("status"), "error": res.get("error"),
           "mode": res.get("_meta", {}).get("mode")}
    grid = res.get("grid_result") or {}
    if grid:
        out.update({
            "kind": "grid",
            "best_params": grid.get("best_params") or {},
            "best_sharpe": grid.get("best_sharpe"),
            "best_return_pct": grid.get("best_return_pct"),
            "best_drawdown_pct": grid.get("best_drawdown_pct"),
            "n_rows": len(grid.get("rows") or []),
            "top_rows": (grid.get("rows") or [])[:5],
        })
    elif res.get("full_result"):
        f = res["full_result"]
        out.update({"kind": "full", "full_result": f})
    elif res.get("wfo_result"):
        out.update({"kind": "wfo", "wfo_result": res["wfo_result"]})
    return out



def parse_args(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    rb = sub.add_parser("run-backtest")
    rb.add_argument("strategy")
    rb.add_argument("--exchange", default="bitget")
    rb.add_argument("--symbol", default="BTC/USDT:USDT")
    rb.add_argument("--timeframe", default="1h")
    rb.add_argument("--start", default="2026-02-21")
    rb.add_argument("--end", default="2026-08-21")
    rb.add_argument("--warmup", type=int, default=500)
    rb.add_argument("--pos", type=float, default=None)
    rb.add_argument("--json", default=None)

    ra = sub.add_parser("run-all-backtests")
    ra.add_argument("--exchange", default="bitget")
    ra.add_argument("--symbol", default="BTC/USDT:USDT")
    ra.add_argument("--timeframe", default="1h")
    ra.add_argument("--start", default="2026-02-21")
    ra.add_argument("--end", default="2026-08-21")
    ra.add_argument("--warmup", type=int, default=500)
    ra.add_argument("--pos", type=float, default=None)

    ro = sub.add_parser("run-optimize")
    ro.add_argument("strategy")
    ro.add_argument("--exchange", default="bitget")
    ro.add_argument("--symbol", default="BTC/USDT:USDT")
    ro.add_argument("--timeframe", default="1h")
    ro.add_argument("--start", default="2026-02-21")
    ro.add_argument("--end", default="2026-08-21")
    ro.add_argument("--warmup", type=int, default=500)
    ro.add_argument("--pos", type=float, default=None)
    ro.add_argument("--mode", default="grid", choices=["grid", "wfo", "full"])
    ro.add_argument("--metric", default="sharpe")

    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.cmd == "run-backtest":
        row = run_backtest(args)
        print(json.dumps(row, indent=2, ensure_ascii=False))
    elif args.cmd == "run-optimize":
        res = run_optimize(args)
        print(json.dumps(summarize_optimize(res), indent=2, ensure_ascii=False))
    else:
        run_all(args)


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"HTTP error: {e}", file=sys.stderr)
        if e.response is not None:
            print(e.response.text[:500], file=sys.stderr)
        sys.exit(2)
