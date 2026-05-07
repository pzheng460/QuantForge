"""Generate paper-quality plots from a matrix.csv (or any A/B result file).

Usage:
    uv run python -m eval.optimizer_ab.plot_results \\
        --csv eval/optimizer_ab/data/baseline_n43.csv \\
        --out-dir eval/optimizer_ab/data/plots
"""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt


def to_float(s):
    if s in (None, ""):
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def read_rows(csv_path):
    rows = []
    with open(csv_path) as f:
        for r in csv.DictReader(f):
            for k in list(r.keys()):
                if k not in ("trial_id", "method", "strategy_name", "regime",
                             "trial_json", "stream_log", "lazy_warning"):
                    r[k] = to_float(r[k])
            rows.append(r)
    return rows


def plot_is_vs_oos(rows, out_path):
    """Scatter: IS Sharpe (x) vs OOS Sharpe (y), points coloured by regime."""
    fig, ax = plt.subplots(figsize=(7, 6))
    by_regime = defaultdict(list)
    for r in rows:
        if r.get("is_sharpe") is not None and r.get("oos_sharpe") is not None:
            by_regime[r["regime"]].append((r["is_sharpe"], r["oos_sharpe"]))
    colours = {"trend_2024h1": "#1f77b4", "range_2024h2": "#ff7f0e", "vol_2025h1": "#2ca02c"}
    for regime, pts in by_regime.items():
        xs, ys = zip(*pts)
        ax.scatter(xs, ys, s=60, alpha=0.7, label=regime, c=colours.get(regime, "gray"))
    lo = min(min(r["is_sharpe"], r["oos_sharpe"]) for r in rows
             if r.get("is_sharpe") is not None) - 1
    hi = max(max(r["is_sharpe"], r["oos_sharpe"]) for r in rows
             if r.get("is_sharpe") is not None) + 1
    ax.plot([lo, hi], [lo, hi], "k--", lw=0.8, alpha=0.5, label="IS = OOS")
    ax.axhline(0, color="grey", lw=0.5)
    ax.axvline(0, color="grey", lw=0.5)
    ax.set_xlabel("In-sample Sharpe")
    ax.set_ylabel("Out-of-sample Sharpe")
    ax.set_title("IS vs OOS Sharpe per trial (baseline TiMi loop, n={})".format(len(rows)))
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def plot_cost_vs_oos(rows, out_path):
    fig, ax = plt.subplots(figsize=(7, 5))
    pts = [(r["cost_usd"], r["oos_sharpe"]) for r in rows
           if r.get("cost_usd") is not None and r.get("oos_sharpe") is not None]
    if not pts:
        return
    xs, ys = zip(*pts)
    ax.scatter(xs, ys, s=60, alpha=0.6, c="#1f77b4")
    ax.axhline(0, color="grey", lw=0.5)
    ax.set_xscale("log")
    ax.set_xlabel("Trial cost (USD, log scale)")
    ax.set_ylabel("Out-of-sample Sharpe")
    ax.set_title("Effort vs OOS quality — agent cost is not predictive")
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def plot_oos_distribution_per_cell(rows, out_path):
    fig, ax = plt.subplots(figsize=(10, 6))
    cells = defaultdict(list)
    for r in rows:
        if r.get("oos_sharpe") is None:
            continue
        cells[(r["strategy_name"], r["regime"])].append(r["oos_sharpe"])
    labels, data = [], []
    for key in sorted(cells):
        labels.append(f"{key[0]}\n{key[1]}")
        data.append(cells[key])
    ax.boxplot(data, tick_labels=labels, showmeans=True, meanline=True,
               meanprops={"color": "red"}, medianprops={"color": "black"})
    ax.axhline(0, color="grey", lw=0.5)
    ax.set_ylabel("Out-of-sample Sharpe")
    ax.set_title("OOS Sharpe distribution per (strategy × regime), 3 seeds each")
    ax.tick_params(axis="x", labelsize=7, rotation=70)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def plot_oos_histogram(rows, out_path):
    fig, ax = plt.subplots(figsize=(7, 4.5))
    oos = [r["oos_sharpe"] for r in rows if r.get("oos_sharpe") is not None]
    ax.hist(oos, bins=15, color="#1f77b4", alpha=0.75, edgecolor="black")
    ax.axvline(0, color="red", lw=1.5, linestyle="--", label="OOS = 0")
    ax.axvline(statistics.median(oos), color="green", lw=1.5,
               linestyle="-", label=f"median = {statistics.median(oos):+.2f}")
    ax.set_xlabel("Out-of-sample Sharpe")
    ax.set_ylabel("Trial count")
    n_neg = sum(1 for x in oos if x < 0)
    n_pos = sum(1 for x in oos if x > 0)
    ax.set_title(f"OOS Sharpe distribution — n={len(oos)} ({n_neg} neg, {n_pos} pos)")
    ax.legend()
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--out-dir", required=True)
    a = p.parse_args()

    rows = read_rows(Path(a.csv))
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    plot_is_vs_oos(rows, out / "is_vs_oos.png")
    plot_cost_vs_oos(rows, out / "cost_vs_oos.png")
    plot_oos_distribution_per_cell(rows, out / "oos_per_cell.png")
    plot_oos_histogram(rows, out / "oos_histogram.png")
    print(f"[plots] wrote 4 PNGs → {out}")


if __name__ == "__main__":
    raise SystemExit(main())
