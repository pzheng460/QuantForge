#!/usr/bin/env python3
"""Run a single strategy backtest WITH realistic trading costs.

The web backtest/optimize API defaults commission_pct=0 (no fees), so those
results are ideal. This script runs the same strategy locally with taker
fees applied, to give a realistic post-cost figure for a recommended strategy.

Usage:
  python scripts/backtest_with_fees.py STRATEGY [--timeframe 1h]
      [--start 2026-02-21] [--end 2026-08-21] [--warmup 500] [--pos 100000]
      [--fee 0.0005] [--json '{"param":val}']
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
    ap.add_argument("--fee", type=float, default=0.0005)  # 0.05% per side
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    import quantforge.strategies  # noqa: F401
    from quantforge.strategy import get_strategy
    from quantforge.adapters.ccxt import fetch_klines
    from quantforge.backtest import BacktestConfig, run_backtest
    from quantforge.domain.timeframes import timeframe_to_seconds
    from apps.dashboard.backend.jobs.data import _resolve_date_range

    start_str, end_str = _resolve_date_range(None, args.start, args.end)
    start_dt = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    warm_start = start_dt - timedelta(seconds=timeframe_to_seconds(args.timeframe) * args.warmup)

    rows = fetch_klines(
        args.symbol, args.exchange, args.timeframe,
        int(warm_start.timestamp() * 1000), int(end_dt.timestamp() * 1000),
    )
    start_ms = int(start_dt.timestamp() * 1000)
    warmup_count = 0
    for bar in rows:
        if bar[0] >= start_ms:
            break
        warmup_count += 1

    cls = get_strategy(args.strategy)
    cfg = json.loads(args.json) if args.json else None
    result = run_backtest(
        cls, rows,
        strategy_config=cfg,
        config=BacktestConfig(
            initial_capital=args.pos,
            commission_pct=args.fee,
            allocation_pct=getattr(cls, "allocation_pct", 1),
        ),
        warmup_bars=warmup_count,
    )
    # Compute total return & trade stats
    curve = result.equity_curve
    print(f"strategy          : {args.strategy}")
    print(f"config            : {json.dumps(cfg or 'default')}")
    print(f"timeframe         : {args.timeframe}  period: {start_str} → {end_str}")
    print(f"fee (per side)    : {args.fee*100:.2f}%")
    print(f"initial capital   : {args.pos:,.0f}")
    print(f"final equity      : {curve[-1]:,.0f}" if curve else "final equity      : n/a")
    if curve and result.initial_capital:
        print(f"total return      : {(curve[-1]/result.initial_capital - 1)*100:.2f}%")
    print(f"trades            : {len(result.trades)}")
    if result.trades:
        wins = sum(1 for t in result.trades if t.pnl > 0)
        gross = sum(t.pnl for t in result.trades)
        fees = sum(t.fee for t in result.trades)
        print(f"win rate          : {wins/len(result.trades)*100:.1f}%")
        print(f"gross pnl         : {gross:,.0f}")
        print(f"total fees paid   : {fees:,.0f}")
    # save raw result as json for later use
    out = {
        "strategy": args.strategy, "config": cfg or "default",
        "timeframe": args.timeframe, "start": start_str, "end": end_str,
        "fee": args.fee, "final_equity": curve[-1] if curve else None,
        "initial_capital": result.initial_capital,
        "trades": len(result.trades),
        "trades_detail": [
            {"side": t.direction, "entry_price": t.entry_price,
             "exit_price": t.exit_price, "pnl": t.pnl, "fee": t.fee}
            for t in result.trades
        ],
    }
    with open(f"reports/btc_backtest_{args.strategy}.json", "w") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    print(f"details saved to  : reports/btc_backtest_{args.strategy}.json")


if __name__ == "__main__":
    main()
