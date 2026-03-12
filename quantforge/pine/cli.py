"""Pine Script CLI — parse, backtest, and transpile Pine scripts.

Usage:
    python -m quantforge.pine.cli backtest my_strategy.pine --symbol BTC/USDT:USDT --exchange bitget --timeframe 15m
    python -m quantforge.pine.cli transpile my_strategy.pine --output strategy.py
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path


def _fetch_ohlcv(
    symbol: str,
    exchange_id: str,
    timeframe: str,
    start: str,
    end: str,
    warmup_days: int,
) -> list:
    """Fetch OHLCV data from exchange via ccxt, including warmup period."""
    import ccxt

    exchange_cls = getattr(ccxt, exchange_id, None)
    if exchange_cls is None:
        print(f"Error: exchange '{exchange_id}' not found in ccxt")
        sys.exit(1)

    exchange = exchange_cls({"enableRateLimit": True})
    exchange.load_markets()

    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    from datetime import timedelta

    warmup_start = start_dt - timedelta(days=warmup_days)
    since_ms = int(warmup_start.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    all_ohlcv = []
    current_since = since_ms
    limit = 1000

    while current_since < end_ms:
        ohlcv = exchange.fetch_ohlcv(
            symbol, timeframe, since=current_since, limit=limit
        )
        if not ohlcv:
            break
        all_ohlcv.extend(ohlcv)
        last_ts = ohlcv[-1][0]
        if last_ts <= current_since:
            break
        current_since = last_ts + 1

    # Filter to end date
    all_ohlcv = [bar for bar in all_ohlcv if bar[0] <= end_ms]
    return all_ohlcv


def _run_backtest(args: argparse.Namespace) -> None:
    """Run Pine Script backtest."""
    from quantforge.pine.interpreter.context import BarData, ExecutionContext
    from quantforge.pine.interpreter.runtime import PineRuntime
    from quantforge.pine.parser.parser import parse

    pine_file = Path(args.pine_file)
    if not pine_file.exists():
        print(f"Error: file '{pine_file}' not found")
        sys.exit(1)

    source = pine_file.read_text()
    print(f"Parsing {pine_file.name}...")

    try:
        ast = parse(source)
    except Exception as e:
        print(f"Parse error: {e}")
        sys.exit(1)

    print(f"Fetching {args.timeframe} data for {args.symbol} from {args.exchange}...")
    ohlcv = _fetch_ohlcv(
        symbol=args.symbol,
        exchange_id=args.exchange,
        timeframe=args.timeframe,
        start=args.start,
        end=args.end,
        warmup_days=args.warmup_days,
    )

    if not ohlcv:
        print("Error: no OHLCV data returned")
        sys.exit(1)

    print(f"Loaded {len(ohlcv)} bars")

    bars = [
        BarData(
            open=bar[1],
            high=bar[2],
            low=bar[3],
            close=bar[4],
            volume=bar[5],
            time=bar[0] // 1000,
        )
        for bar in ohlcv
    ]

    ctx = ExecutionContext(bars=bars)
    runtime = PineRuntime(ctx)
    result = runtime.run(ast)

    # Calculate metrics
    total_pnl = result.net_profit
    trades = result.trades
    total = result.total_trades
    wins = result.winning_trades
    losses = result.losing_trades
    win_rate = result.win_rate

    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl <= 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Max drawdown from equity curve
    max_dd = 0.0
    peak = result.initial_capital
    for eq in result.equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    # Print results
    print("\n" + "=" * 60)
    print(f"  Pine Script Backtest Results — {pine_file.name}")
    print("=" * 60)
    print(f"  Symbol:          {args.symbol}")
    print(f"  Exchange:        {args.exchange}")
    print(f"  Timeframe:       {args.timeframe}")
    print(f"  Period:          {args.start} to {args.end}")
    print(f"  Bars:            {len(bars)}")
    print("-" * 60)
    print(f"  Initial Capital: ${result.initial_capital:,.2f}")
    print(
        f"  Final Equity:    ${result.equity_curve[-1]:,.2f}"
        if result.equity_curve
        else "  Final Equity:    N/A"
    )
    print(f"  Net P&L:         ${total_pnl:,.2f}")
    print(f"  Return:          {total_pnl / result.initial_capital:.2%}")
    print("-" * 60)
    print(f"  Total Trades:    {total}")
    print(f"  Winning:         {wins}")
    print(f"  Losing:          {losses}")
    print(f"  Win Rate:        {win_rate:.1%}")
    print(f"  Profit Factor:   {profit_factor:.2f}")
    print(f"  Max Drawdown:    {max_dd:.2%}")
    print("-" * 60)

    if trades:
        print("\n  Trades:")
        print(f"  {'#':>4}  {'Dir':>5}  {'Entry':>12}  {'Exit':>12}  {'P&L':>12}")
        print(f"  {'—' * 4}  {'—' * 5}  {'—' * 12}  {'—' * 12}  {'—' * 12}")
        for i, t in enumerate(trades[:50], 1):
            d = "LONG" if t.direction.value == "long" else "SHORT"
            print(
                f"  {i:>4}  {d:>5}  {t.entry_price:>12.2f}  {t.exit_price:>12.2f}  {t.pnl:>12.2f}"
            )
        if len(trades) > 50:
            print(f"  ... and {len(trades) - 50} more trades")

    print("=" * 60)


def _run_transpile(args: argparse.Namespace) -> None:
    """Transpile Pine Script to Python."""
    from quantforge.pine.parser.parser import parse
    from quantforge.pine.transpiler.codegen import transpile

    pine_file = Path(args.pine_file)
    if not pine_file.exists():
        print(f"Error: file '{pine_file}' not found")
        sys.exit(1)

    source = pine_file.read_text()
    ast = parse(source)
    python_code = transpile(ast, pine_source=source)

    if args.output:
        output = Path(args.output)
        output.write_text(python_code)
        print(f"Transpiled to {output}")
    else:
        print(python_code)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="quantforge-pine",
        description="QuantForge Pine Script engine — parse, backtest, and transpile",
    )
    sub = parser.add_subparsers(dest="command", help="Command to run")

    # backtest subcommand
    bt = sub.add_parser("backtest", help="Run Pine Script backtest on exchange data")
    bt.add_argument("pine_file", help="Path to .pine file")
    bt.add_argument(
        "--symbol",
        default="BTC/USDT:USDT",
        help="Trading symbol (default: BTC/USDT:USDT)",
    )
    bt.add_argument(
        "--exchange", default="bitget", help="Exchange id (default: bitget)"
    )
    bt.add_argument("--timeframe", default="15m", help="Kline timeframe (default: 15m)")
    bt.add_argument("--start", default="2026-01-01", help="Start date YYYY-MM-DD")
    bt.add_argument("--end", default="2026-03-12", help="End date YYYY-MM-DD")
    bt.add_argument(
        "--warmup-days",
        type=int,
        default=60,
        help="Warmup period in days (default: 60)",
    )

    # transpile subcommand
    tp = sub.add_parser("transpile", help="Transpile Pine Script to Python")
    tp.add_argument("pine_file", help="Path to .pine file")
    tp.add_argument("--output", "-o", help="Output Python file (default: stdout)")

    parsed = parser.parse_args()

    if parsed.command == "backtest":
        _run_backtest(parsed)
    elif parsed.command == "transpile":
        _run_transpile(parsed)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
