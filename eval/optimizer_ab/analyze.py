"""Analyze A/B results from results/matrix.csv.

Per-method summary stats + paired Wilcoxon (treatment − baseline) on a
chosen metric, with a bootstrap CI for the mean improvement.

Usage:
    uv run python -m eval.optimizer_ab.analyze \
        --csv eval/optimizer_ab/results/matrix.csv \
        --baseline baseline --treatment reflexion \
        --metric oos_sharpe
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path

# True = higher better, False = lower better.
HIGHER_IS_BETTER = {
    "oos_sharpe": True, "oos_pf": True, "oos_win_rate": True,
    "is_sharpe": True, "is_pf": True, "is_win_rate": True,
    "overfit_index": False, "oos_mdd": False, "is_mdd": False,
    "cost_usd": False, "duration_s": False,
}


def to_float(s):
    if s is None or s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def read_rows(csv_path):
    rows = []
    with open(csv_path) as f:
        for r in csv.DictReader(f):
            for k in list(r.keys()):
                if k not in ("trial_id", "method", "strategy_name", "regime",
                             "trial_json", "stream_log"):
                    r[k] = to_float(r[k])
            rows.append(r)
    return rows


def paired_diffs(rows, baseline, treatment, metric):
    by_key = defaultdict(dict)
    for r in rows:
        key = (r["strategy_name"], r["regime"], r.get("seed"))
        by_key[key][r["method"]] = r
    out = []
    for key, mdict in by_key.items():
        if baseline in mdict and treatment in mdict:
            b = mdict[baseline].get(metric)
            t = mdict[treatment].get(metric)
            if b is not None and t is not None:
                out.append((b, t, key))
    return out


def wilcoxon(diffs):
    diffs = [d for d in diffs if d != 0]
    n = len(diffs)
    if n < 5:
        return None, n, None
    pairs = sorted(((abs(d), 1 if d > 0 else -1) for d in diffs), key=lambda x: x[0])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (i + j + 2) / 2.0
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    w_pos = sum(rk for rk, (_, sgn) in zip(ranks, pairs) if sgn > 0)
    w_neg = sum(rk for rk, (_, sgn) in zip(ranks, pairs) if sgn < 0)
    w = min(w_pos, w_neg)
    mean = n * (n + 1) / 4.0
    sd = math.sqrt(n * (n + 1) * (2 * n + 1) / 24.0)
    if sd == 0:
        return w, n, None
    z = (w - mean) / sd
    p = 2.0 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2.0))))
    return w, n, p


def bootstrap_ci(values, n_iters=2000, alpha=0.05):
    if not values:
        return (0.0, 0.0)
    means = []
    for _ in range(n_iters):
        sample = [random.choice(values) for _ in range(len(values))]
        means.append(sum(sample) / len(sample))
    means.sort()
    lo = means[int((alpha / 2) * n_iters)]
    hi = means[int((1 - alpha / 2) * n_iters)]
    return (lo, hi)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--baseline", default="baseline")
    p.add_argument("--treatment", required=True)
    p.add_argument("--metric", default="oos_sharpe")
    a = p.parse_args()

    rows = []
    import csv as _csv
    with open(a.csv) as f:
        for r in _csv.DictReader(f):
            for k in list(r.keys()):
                if k not in ("trial_id", "method", "strategy_name", "regime", "trial_json", "stream_log"):
                    r[k] = to_float(r[k])
            rows.append(r)
    if not rows:
        print("(empty CSV)")
        return 1

    methods = sorted({r["method"] for r in rows})
    by_method = defaultdict(list)
    for r in rows:
        v = r.get(a.metric)
        if v is not None:
            by_method[r["method"]].append(v)

    print(f"# A/B report — metric: `{a.metric}`")
    print()
    print("| method | n | mean | median | std | min | max |")
    print("|---|---|---|---|---|---|---|")
    for m in methods:
        vs = by_method.get(m, [])
        if not vs:
            print(f"| {m} | 0 | – | – | – | – | – |")
            continue
        mean = sum(vs) / len(vs)
        med = statistics.median(vs)
        std = statistics.pstdev(vs) if len(vs) > 1 else 0.0
        print(f"| {m} | {len(vs)} | {mean:.3f} | {med:.3f} | {std:.3f} | {min(vs):.3f} | {max(vs):.3f} |")

    if a.baseline not in methods or a.treatment not in methods:
        print()
        print(f"baseline `{a.baseline}` or treatment `{a.treatment}` missing — no pairwise test")
        return 0

    by_key = defaultdict(dict)
    for r in rows:
        key = (r["strategy_name"], r["regime"], r.get("seed"))
        by_key[key][r["method"]] = r
    paired = []
    for key, mdict in by_key.items():
        if a.baseline in mdict and a.treatment in mdict:
            b = mdict[a.baseline].get(a.metric)
            t = mdict[a.treatment].get(a.metric)
            if b is not None and t is not None:
                paired.append((b, t, key))
    if not paired:
        print()
        print("no paired (strategy, regime, seed) cells")
        return 0
    diffs = [t - b for b, t, _ in paired]
    higher = HIGHER_IS_BETTER.get(a.metric, True)
    n_better = sum(1 for d in diffs if (d > 0) == higher)
    mean_d = sum(diffs) / len(diffs)
    median_d = statistics.median(diffs)
    w, n, p = wilcoxon(diffs)
    lo, hi = bootstrap_ci(diffs, 2000)

    print()
    print(f"## Paired: {a.treatment} − {a.baseline} on `{a.metric}`")
    print(f"- Pairs: {len(diffs)}  (treatment better: {n_better}/{len(diffs)})")
    print(f"- Mean Δ:    {mean_d:+.4f}")
    print(f"- Median Δ:  {median_d:+.4f}")
    print(f"- 95% bootstrap CI on mean Δ: [{lo:+.4f}, {hi:+.4f}]")
    if p is not None:
        print(f"- Wilcoxon two-sided p = {p:.4f}  ({'significant' if p < 0.05 else 'n.s.'} α=0.05)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
