#!/usr/bin/env python3
"""Portfolio allocation check: bb_squeeze + rsi_momentum combination.

Runs both strategies (default params, taker fees) on the same BTC 1h bars,
takes their per-bar equity curves, and combines them under different capital
allocations (no rebalancing). Reports for each split:
  - total return, max drawdown, Calmar-like, per-window consistency
The goal is to find the allocation that best smooths the curve (smallest
drawdown + most positive sub-windows) rather than max return alone.

Usage:
  python scripts/portfolio_alloc_check.py [--timeframe 1h]
      [--start 2026-02-21] [--end 2026-08-21] [--fee 0.0005] [--windows 10]
"""

import argparse
import json
import warnings
from datetime import datetime, timedelta, timezone

warnings.filterwarnings("ignore")

STRATS = ["bb_squeeze", "rsi_momentum"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--exchange", default="bitget")
    ap.add_argument("--symbol", default="BTC/USDT:USDT")
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--start", default="2026-02-21")
    ap.add_argument("--end", default="2026-08-21")
    ap.add_argument("--warmup", type=int, default=500)
    ap.add_argument("--pos", type=float, default=100_000)
    ap.add_argument("--fee", type=float, default=0.0005)
    ap.add_argument("--windows", type=int, default=10)
    args = ap.parse_args(argv)

    import quantforge.strategies  # noqa: F401
    from quantforge.strategy import get_strategy
    from quantforge.adapters.ccxt import fetch_klines
    from quantforge.backtest import BacktestConfig, run_backtest
    from quantforge.domain.timeframes import timeframe_to_seconds

    start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    warm_start = start_dt - timedelta(seconds=timeframe_to_seconds(args.timeframe) * args.warmup)
    rows = fetch_klines(args.symbol, args.exchange, args.timeframe,
                        int(warm_start.timestamp() * 1000), int(end_dt.timestamp() * 1000))
    start_ms = int(start_dt.timestamp() * 1000)
    warmup_count = 0
    for bar in rows:
        if bar[0] >= start_ms:
            break
        warmup_count += 1
    period = rows[warmup_count:]

    # Run both strategies, keep equity curves + trade pnl streams.
    curves = {}
    pnl_streams = {}
    for name in STRATS:
        cls = get_strategy(name)
        r = run_backtest(
            cls, rows, strategy_config=None,
            config=BacktestConfig(initial_capital=args.pos,
                                  commission_pct=args.fee,
                                  allocation_pct=getattr(cls, "allocation_pct", 1)),
            warmup_bars=warmup_count,
        )
        # curves are per-bar; both start at the same bar index.
        curves[name] = r.equity_curve
        # build a per-bar pnl series (0 except on trade-exit bars) — exit_bar
        # is a bar index into the FULL array (warmup included). Align to period.
        pnl = [0.0] * len(period)
        for t in r.trades:
            idx = t.exit_bar - warmup_count
            if 0 <= idx < len(period):
                pnl[idx] += t.pnl
        pnl_streams[name] = pnl
        print(f"fetched {name}: {len(r.trades)} trades, final equity "
              f"{r.equity_curve[-1]:,.0f} ({(r.equity_curve[-1]/args.pos-1)*100:.2f}%)")

    # Equalize lengths.
    n = min(len(curves["bb_squeeze"]), len(curves["rsi_momentum"]), len(period))
    for name in STRATS:
        curves[name] = curves[name][:n]
        pnl_streams[name] = pnl_streams[name][:n]
    period = period[:n]

    seg = n // args.windows

    def curve_metrics(curve, label):
        peak = curve[0]
        mdd = 0.0
        for eq in curve:
            peak = max(peak, eq)
            mdd = max(mdd, (peak - eq) / peak * 100 if peak else 0)
        ret = (curve[-1] / curve[0] - 1) * 100
        # window consistency on the equity curve
        pos_windows = 0
        for i in range(args.windows):
            lo = i * seg
            hi = min(n, (i + 1) * seg)
            if hi <= lo:
                continue
            wret = (curve[hi - 1] / curve[lo] - 1) * 100 if curve[lo] else 0
            if wret > 0:
                pos_windows += 1
        calmar = ret / mdd if mdd > 0 else float("inf")
        return ret, mdd, calmar, pos_windows

    print(f"\n{'split(bb/rsi)':>14} {'ret%':>8} {'maxDD%':>8} {'Calmar':>7} "
          f"{'posWin':>6} | detail")
    print("-" * 78)

    results = []
    weights = [1.0, 0.8, 0.6, 0.4, 0.2, 0.0]
    for wb in weights:
        wr = 1.0 - wb
        # Combined equity: proportional allocation of the two full-100k curves
        # is a no-rebalance approximation (linear in capital).
        comb = [wb * curves["bb_squeeze"][i] + wr * curves["rsi_momentum"][i]
                for i in range(n)]
        ret, mdd, calmar, pw = curve_metrics(comb, "combined")
        results.append((wb, ret, mdd, calmar, pw))
        print(f"{f'{wb:.0%}/{wr:.0%}':>14} {ret:>8.2f} {mdd:>8.2f} "
              f"{calmar:>7.2f} {f'{pw}/{args.windows}':>6} ")

    # Standalone references.
    for name in STRATS:
        ret, mdd, calmar, pw = curve_metrics(curves[name], name)
        print(f"{f'{name} 100%':>14} {ret:>8.2f} {mdd:>8.2f} {calmar:>7.2f} "
              f"{f'{pw}/{args.windows}':>6} (ref)")

    # Objective: maximize positive windows first, then Calmar, then return.
    def score(r):
        wb, ret, mdd, calmar, pw = r
        return (pw, calmar, ret)

    best = max(results, key=score)
    print("\n" + "=" * 78)
    print(f"BEST for 'stable': bb_squeeze {best[0]:.0%} / rsi_momentum {1-best[0]:.0%}")
    print(f"  => ret {best[1]:.2f}%, maxDD {best[2]:.2f}%, Calmar {best[3]:.2f}, "
          f"positive windows {best[4]}/{args.windows}")

    # Save the per-bar pnl streams for cross-correlation analysis
    out = {"period": f"{args.start}→{args.end}", "timeframe": args.timeframe,
           "fee": args.fee, "windows": args.windows, "n_bars": n,
           "corr_pnl": None, "curves_note": "linear no-rebalance combination"}
    # Cross-correlation between trade pnl streams (diversification value)
    a = pnl_streams["bb_squeeze"]
    b = pnl_streams["rsi_momentum"]
    if any(a) and any(b):
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / len(a)
        va = sum((x - ma) ** 2 for x in a) / len(a)
        vb = sum((y - mb) ** 2 for y in b) / len(b)
        corr = cov / (va ** 0.5 * vb ** 0.5) if va > 0 and vb > 0 else 0
        out["corr_pnl"] = round(corr, 3)
        print(f"PNL cross-correlation (bb_squeeze vs rsi_momentum): {corr:.3f} "
              f"(< 0.3 = good diversification)")
    with open("reports/btc_portfolio_alloc.json", "w") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
