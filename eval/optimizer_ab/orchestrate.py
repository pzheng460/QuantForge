"""Orchestrator: run the (method × strategy × regime × seed) matrix.

Each cell launches runner.py + holdout_eval.py and appends one row to
results/matrix.csv. Re-running skips rows already present (resume).

Usage:
    uv run python -m eval.optimizer_ab.orchestrate \\
        --tier dev --methods baseline --regimes trend_2024h1 --seeds 1
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import time
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = Path(__file__).resolve().parent

FIELDS = [
    "trial_id", "method", "strategy_name", "regime", "seed",
    "returncode", "cost_usd", "duration_s",
    "n_backtests", "lazy_warning",
    "is_sharpe", "is_pf", "is_mdd", "is_win_rate", "is_n_trades",
    "oos_sharpe", "oos_pf", "oos_mdd", "oos_win_rate", "oos_n_trades",
    "overfit_index", "is_to_oos_pf_ratio",
    "trial_json", "stream_log",
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(EVAL_ROOT / "test_set.yaml"))
    p.add_argument("--tier", choices=["dev", "test", "holdout"], required=True)
    p.add_argument("--methods", required=True)
    p.add_argument("--regimes", default="")
    p.add_argument("--seeds", default="")
    p.add_argument("--results-csv", default=str(EVAL_ROOT / "results" / "matrix.csv"))
    p.add_argument("--trials-dir", default=str(EVAL_ROOT / "results" / "trials"))
    p.add_argument("--no-holdout", action="store_true")
    a = p.parse_args()

    cfg = yaml.safe_load(Path(a.config).read_text())
    strategies = cfg["strategies"][a.tier]
    methods = [m.strip() for m in a.methods.split(",") if m.strip()]
    regimes = [r.strip() for r in a.regimes.split(",") if r.strip()] or list(cfg["regimes"])
    seeds = [int(s) for s in a.seeds.split(",") if s.strip()] or list(cfg["trial"].get("seeds") or [1])

    csv_path = Path(a.results_csv)
    trials_dir = Path(a.trials_dir)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    trials_dir.mkdir(parents=True, exist_ok=True)

    done = set()
    if csv_path.exists():
        with open(csv_path) as f:
            done = {r.get("trial_id", "") for r in csv.DictReader(f)}

    cells = [(m, s, r, sd) for m in methods for s in strategies for r in regimes for sd in seeds]
    print(f"[orchestrate] tier={a.tier}  cells={len(cells)}  already_done={len(done)}")

    for method, strat, regime, seed in cells:
        cell_id = f"{method}__{Path(strat).stem}__{regime}__s{seed}"
        # Resume key includes trailing "__" so seed=1 isn't mistaken for a
        # prefix of seed=10's trial_id.
        if any(t.startswith(cell_id + "__") for t in done):
            print(f"  [skip] {cell_id}")
            continue
        trial_json = trials_dir / f"{cell_id}.json"
        print(f"  [run]  {cell_id}")
        t0 = time.time()
        rc = subprocess.run(
            ["uv", "run", "python", "-m", "eval.optimizer_ab.runner",
             "--config", a.config, "--method", method, "--strategy", strat,
             "--regime", regime, "--seed", str(seed), "--out", str(trial_json)],
            cwd=str(Path(__file__).resolve().parents[2]),
        ).returncode
        if not a.no_holdout and trial_json.exists():
            subprocess.run(
                ["uv", "run", "python", "-m", "eval.optimizer_ab.holdout_eval",
                 "--config", a.config, "--trial", str(trial_json)],
                cwd=str(Path(__file__).resolve().parents[2]),
            )
        duration = time.time() - t0
        row = _row(method, strat, regime, seed, cell_id, trial_json, duration, rc)
        _append(csv_path, row)
        done.add(row.get("trial_id", cell_id))

    print(f"[orchestrate] CSV → {csv_path}")
    return 0


def _row(method, strat, regime, seed, cell_id, trial_json, duration, rc):
    if not trial_json.exists():
        return {"trial_id": cell_id, "method": method,
                "strategy_name": Path(strat).stem, "regime": regime,
                "seed": seed, "returncode": rc,
                "duration_s": round(duration, 1)}
    rec = json.loads(trial_json.read_text())
    ho = rec.get("holdout") or {}
    is_m = ho.get("is") or {}
    oos_m = ho.get("oos") or {}
    overfit = None
    if is_m.get("sharpe") is not None and oos_m.get("sharpe") is not None:
        overfit = is_m["sharpe"] - oos_m["sharpe"]
    return {
        "trial_id": rec.get("trial_id", cell_id),
        "method": method,
        "strategy_name": rec.get("strategy_name", Path(strat).stem),
        "regime": regime,
        "seed": seed,
        "returncode": rec.get("returncode", rc),
        "cost_usd": rec.get("cost_usd", 0.0),
        "duration_s": round(duration, 1),
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
        "trial_json": str(trial_json),
        "stream_log": rec.get("stream_log", ""),
    }


def _append(csv_path, row):
    new = not csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow({k: ("" if v is None else v) for k, v in row.items()})


if __name__ == "__main__":
    raise SystemExit(main())
