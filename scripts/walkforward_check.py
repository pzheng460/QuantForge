#!/usr/bin/env python3
"""Manual walk-forward robustness check for a specific strategy+config.

The built-in optimizer's schema-driven grid can't honor cross-parameter
constraints (e.g. ema fast < slow) and its grid-best params overfit (few
trades). This script instead takes ONE explicit config (or defaults) and rolls
a walk-forward over the requested period, reporting each window's out-of-sample
return/sharpe so you can see whether a given parameterization is robustly
positive, independent of in-sample grid search.

Usage:
  python scripts/walkforward_check.py STRATEGY [--timeframe 1h]
      [--start 2026-02-21] [--end 2026-08-21] [--warmup 500]
      [--fee 0.0005] [--json '{"param":val}'] [--windows 6]
"""

import argparse
import json
import warnings
from datetime import datetime, timedelta, timezone

warnings.filterwarnings("ignore")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy")
    ap.add_argument("--exchange", default="bitget")
    ap.add_argument("--symbol", default="BTC/USDT:USDT")
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--start", default="2026-02-21")
    ap.add_argument("--end", default="2026-08-21")
    ap.add_argument("--warmup", type=int, default=500)
    ap.add_argument("--pos", type=float, default=100_000)
    ap.add_argument("--fee", type=float, default=0.0005)
    ap.add_argument("--json", default=None)
    ap.add_argument("--windows", type=int, default=6)
    args = ap.parse_args(argv)

    import quantforge.strategies  # noqa: F401
    from quantforge.strategy import get_strategy
    from quantforge.adapters.ccxt import fetch_klines
    from quantforge.backtest import BacktestConfig, run_backtest
    from quantforge.domain.timeframes import timeframe_to_seconds

    cfg = json.loads(args.json) if args.json else None
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
    period = rows[warmup_count:]  # rows with warmup already included

    cls = get_strategy(args.strategy)
    alloc = getattr(cls, "allocation_pct", 1)

    def run_bt(bars, wu):
        r = run_backtest(cls, bars, strategy_config=cfg,
                         config=BacktestConfig(initial_capital=args.pos,
                                               commission_pct=args.fee,
                                               allocation_pct=alloc),
                         warmup_bars=wu)
        curve = r.equity_curve
        ret = (curve[-1] / r.initial_capital - 1) * 100 if curve and r.initial_capital else 0
        trades = len(r.trades)
        wins = sum(1 for t in r.trades if t.pnl > 0)
        wr = wins / trades * 100 if trades else 0
        return ret, trades, wr

    # Slice the period into contiguous windows and evaluate each out-of-sample.
    n = len(period)
    seg = n // args.windows
    print(f"{args.strategy}  config={json.dumps(cfg or 'default')}  tf={args.timeframe}  fee={args.fee*100:.2f}%  {args.windows} windows over {args.start}→{args.end}")
    print(f"{'window':>6} {'ret%':>9} {'trades':>6} {'win%':>6}")
    print("-" * 40)
    total_ret = 0
    positives = 0
    # walk forward: each window uses warmup from just before it
    for i in range(args.windows):
        lo = i * seg
        hi = min(n, (i + 1) * seg)
        wu = min(args.warmup, max(0, lo))  # warm up on bars before the window start
        ret, trades, wr = run_bt(period[lo - wu:hi], len(period[lo - wu:hi]) - (hi - lo))
        total_ret += ret
        if ret > 0:
            positives += 1
        print(f"{i+1:>6} {ret:>9.2f} {trades:>6} {wr:>6.1f}")
    print("-" * 40)
    print(f"full-period return (last window full): sum of window rets = {total_ret:.2f}")
    print(f"ROBUSTNESS: {positives}/{args.windows} windows positive")


if __name__ == "__main__":
    main()
