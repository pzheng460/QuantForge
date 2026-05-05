"""Rebuild the matrix.csv from existing trial JSONs in results/trials/.

Use this when holdout_eval was re-run after a fix and the CSV is stale,
without spending money to re-invoke the runner.

Usage:
    python -m eval.optimizer_ab.rebuild_csv \
        --trials-dir eval/optimizer_ab/results/trials \
        --csv eval/optimizer_ab/results/matrix.csv
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

FIELDS = [
    "trial_id", "method", "strategy_name", "regime", "seed",
    "returncode", "cost_usd", "duration_s",
    "n_backtests", "lazy_warning",
    "is_sharpe", "is_pf", "is_mdd", "is_win_rate", "is_n_trades",
    "oos_sharpe", "oos_pf", "oos_mdd", "oos_win_rate", "oos_n_trades",
    "overfit_index", "is_to_oos_pf_ratio",
    "trial_json", "stream_log",
]


def row_from_trial(p: Path) -> dict | None:
    rec = json.loads(p.read_text())
    if not rec.get("optimized_pine"):
        return None
    ho = rec.get("holdout") or {}
    is_m = ho.get("is") or {}
    oos_m = ho.get("oos") or {}
    overfit = None
    if is_m.get("sharpe") is not None and oos_m.get("sharpe") is not None:
        overfit = is_m["sharpe"] - oos_m["sharpe"]
    return {
        "trial_id": rec.get("trial_id"),
        "method": rec.get("method"),
        "strategy_name": rec.get("strategy_name"),
        "regime": rec.get("regime"),
        "seed": rec.get("seed"),
        "returncode": rec.get("returncode"),
        "cost_usd": rec.get("cost_usd"),
        "duration_s": "",
        "n_backtests": rec.get("n_backtests"),
        "lazy_warning": rec.get("lazy_warning"),
        "is_sharpe": is_m.get("sharpe"),
        "is_pf": is_m.get("profit_factor"),
        "is_mdd": is_m.get("max_drawdown"),
        "is_win_rate": is_m.get("win_rate"),
        "is_n_trades": is_m.get("n_trades"),
        "oos_sharpe": oos_m.get("sharpe"),
        "oos_pf": oos_m.get("profit_factor"),
        "oos_mdd": oos_m.get("max_drawdown"),
        "oos_win_rate": oos_m.get("win_rate"),
        "oos_n_trades": oos_m.get("n_trades"),
        "overfit_index": overfit,
        "is_to_oos_pf_ratio": ho.get("is_to_oos_pf_ratio"),
        "trial_json": str(p),
        "stream_log": rec.get("stream_log", ""),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--trials-dir", default="eval/optimizer_ab/results/trials")
    p.add_argument("--csv", default="eval/optimizer_ab/results/matrix.csv")
    a = p.parse_args()

    trials = sorted(Path(a.trials_dir).glob("*.json"))
    rows = [r for r in (row_from_trial(p) for p in trials) if r]
    Path(a.csv).parent.mkdir(parents=True, exist_ok=True)
    with open(a.csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    print(f"[rebuild] wrote {len(rows)} rows → {a.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
