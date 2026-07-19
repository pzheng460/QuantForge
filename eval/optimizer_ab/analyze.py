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

# True = higher better, False = lower better.
HIGHER_IS_BETTER = {
    "oos_sharpe": True,
    "oos_pf": True,
    "oos_win_rate": True,
    "is_sharpe": True,
    "is_pf": True,
    "is_win_rate": True,
    "overfit_index": False,
    "oos_mdd": False,
    "is_mdd": False,
    "cost_usd": False,
    "duration_s": False,
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
                if k not in (
                    "trial_id",
                    "method",
                    "strategy_name",
                    "regime",
                    "agent_provider",
                    "model",
                    "trial_json",
                    "stream_log",
                ):
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


def provider_paired(rows, provider_a, provider_b, metric):
    by_key = defaultdict(dict)
    for r in rows:
        key = (r["method"], r["strategy_name"], r["regime"], r.get("seed"))
        by_key[key][r.get("agent_provider")] = r
    out = []
    for key, providers in by_key.items():
        if provider_a in providers and provider_b in providers:
            a = providers[provider_a].get(metric)
            b = providers[provider_b].get(metric)
            if a is not None and b is not None:
                out.append((a, b, key))
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
    p.add_argument(
        "--treatment", default="", help="Treatment method for method A/B comparison."
    )
    p.add_argument("--metric", default="oos_sharpe")
    p.add_argument(
        "--include-lazy",
        action="store_true",
        help="Include trials where the agent emitted FINAL_OUTPUT "
        "without running any real backtest (default: exclude).",
    )
    p.add_argument(
        "--include-no-op",
        action="store_true",
        help="Include trials where no candidate optimization was attempted.",
    )
    p.add_argument(
        "--compare-providers",
        default="",
        help="Compare two providers as provider_a,provider_b, e.g. claude,codex.",
    )
    a = p.parse_args()

    rows = []
    import csv as _csv

    with open(a.csv) as f:
        for r in _csv.DictReader(f):
            for k in list(r.keys()):
                if k not in (
                    "trial_id",
                    "method",
                    "strategy_name",
                    "regime",
                    "agent_provider",
                    "model",
                    "trial_json",
                    "stream_log",
                    "lazy_warning",
                    "no_op",
                    "optimization_attempted",
                ):
                    r[k] = to_float(r[k])
            rows.append(r)
    if not rows:
        print("(empty CSV)")
        return 1

    if not a.include_lazy:
        before = len(rows)
        rows = [r for r in rows if str(r.get("lazy_warning", "")).lower() != "true"]
        dropped = before - len(rows)
        if dropped:
            print(
                f"(excluded {dropped} lazy trials — re-run with --include-lazy to include)"
            )
    if not a.include_no_op:
        before = len(rows)
        rows = [r for r in rows if str(r.get("no_op", "")).lower() != "true"]
        dropped = before - len(rows)
        if dropped:
            print(
                f"(excluded {dropped} no-op trials — re-run with --include-no-op to include)"
            )

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
        print(
            f"| {m} | {len(vs)} | {mean:.3f} | {med:.3f} | {std:.3f} | {min(vs):.3f} | {max(vs):.3f} |"
        )

    if a.treatment:
        if a.baseline not in methods or a.treatment not in methods:
            if not a.compare_providers:
                print()
                print(
                    f"baseline `{a.baseline}` or treatment `{a.treatment}` missing — no pairwise test"
                )
                return 0
        else:
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
                if not a.compare_providers:
                    print()
                    print("no paired (strategy, regime, seed) cells")
                    return 0
            else:
                diffs = [t - b for b, t, _ in paired]
                higher = HIGHER_IS_BETTER.get(a.metric, True)
                n_better = sum(1 for d in diffs if (d > 0) == higher)
                mean_d = sum(diffs) / len(diffs)
                median_d = statistics.median(diffs)
                w, n, p = wilcoxon(diffs)
                lo, hi = bootstrap_ci(diffs, 2000)

                print()
                print(f"## Paired: {a.treatment} − {a.baseline} on `{a.metric}`")
                print(
                    f"- Pairs: {len(diffs)}  (treatment better: {n_better}/{len(diffs)})"
                )
                print(f"- Mean Δ:    {mean_d:+.4f}")
                print(f"- Median Δ:  {median_d:+.4f}")
                print(f"- 95% bootstrap CI on mean Δ: [{lo:+.4f}, {hi:+.4f}]")
                if p is not None:
                    print(
                        f"- Wilcoxon two-sided p = {p:.4f}  ({'significant' if p < 0.05 else 'n.s.'} α=0.05)"
                    )
    elif not a.compare_providers:
        print()
        print("--treatment is required unless --compare-providers is set")
        return 2
    if a.compare_providers:
        parts = [x.strip() for x in a.compare_providers.split(",") if x.strip()]
        if len(parts) != 2:
            print()
            print(
                "--compare-providers expects exactly two providers, e.g. claude,codex"
            )
            return 2
        provider_pairs = provider_paired(rows, parts[0], parts[1], a.metric)
        print()
        print(f"## Provider paired: {parts[1]} − {parts[0]} on `{a.metric}`")
        if not provider_pairs:
            print("no paired (method, strategy, regime, seed) provider cells")
            return 0
        diffs = [b - a0 for a0, b, _ in provider_pairs]
        higher = HIGHER_IS_BETTER.get(a.metric, True)
        n_better = sum(1 for d in diffs if (d > 0) == higher)
        lo, hi = bootstrap_ci(diffs, 2000)
        print(f"- Pairs: {len(diffs)}  ({parts[1]} better: {n_better}/{len(diffs)})")
        print(f"- Mean Δ:    {sum(diffs) / len(diffs):+.4f}")
        print(f"- Median Δ:  {statistics.median(diffs):+.4f}")
        print(f"- 95% bootstrap CI on mean Δ: [{lo:+.4f}, {hi:+.4f}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
