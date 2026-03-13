"""Pine Script CLI — parse, backtest, transpile, and deploy Pine scripts.

Usage:
    python -m quantforge.pine.cli backtest my_strategy.pine --symbol BTC/USDT:USDT --exchange bitget --timeframe 15m
    python -m quantforge.pine.cli transpile my_strategy.pine --output strategy.py
    python -m quantforge.pine.cli transpile my_strategy.pine --strategy-api --output strategy.py
    python -m quantforge.pine.cli deploy my_strategy.pine --exchange bitget --demo --symbol BTCUSDT-PERP
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

    pine_file = Path(args.pine_file)
    if not pine_file.exists():
        print(f"Error: file '{pine_file}' not found")
        sys.exit(1)

    source = pine_file.read_text()
    ast = parse(source)

    if args.strategy_api:
        from quantforge.pine.transpiler.codegen import transpile_strategy_api

        python_code = transpile_strategy_api(ast, pine_source=source)
    else:
        from quantforge.pine.transpiler.codegen import transpile

        python_code = transpile(ast, pine_source=source)

    if args.output:
        output = Path(args.output)
        output.write_text(python_code)
        mode = "Strategy API" if args.strategy_api else "standalone"
        print(f"Transpiled to {output} ({mode})")
    else:
        print(python_code)


def _run_live(args: argparse.Namespace) -> None:
    """Run Pine Script as a live trading engine."""
    import asyncio

    from quantforge.pine.live.engine import PineLiveEngine

    pine_file = Path(args.pine_file)
    if not pine_file.exists():
        print(f"Error: file '{pine_file}' not found")
        sys.exit(1)

    source = pine_file.read_text()

    if not args.demo and not args.confirm_live:
        print("Error: live trading requires --confirm-live flag")
        print("Add --confirm-live to acknowledge real money trading")
        sys.exit(1)

    mode_str = "DEMO" if args.demo else "LIVE"
    print(f"Pine Live Engine — {mode_str} mode")
    print(f"  Strategy: {pine_file.name}")
    print(f"  Symbol:   {args.symbol}")
    print(f"  Exchange: {args.exchange}")
    print(f"  Timeframe: {args.timeframe}")
    print(f"  Warmup:   {args.warmup_bars} bars")
    print()

    engine = PineLiveEngine(
        pine_source=source,
        exchange=args.exchange,
        symbol=args.symbol,
        timeframe=args.timeframe,
        demo=args.demo,
        warmup_bars=args.warmup_bars,
        position_size_usdt=args.position_size,
    )

    try:
        asyncio.run(engine.start())
    except KeyboardInterrupt:
        print("\nStopping...")
        asyncio.run(engine.stop())
        print(f"Processed {engine.bars_processed} bars total")
        if engine.bridge:
            print(f"Signals captured: {len(engine.bridge.signals)}")


def _run_optimize(args: argparse.Namespace) -> None:
    """Run Pine Script parameter optimization."""
    from quantforge.pine.interpreter.context import BarData
    from quantforge.pine.optimize import (
        extract_pine_inputs,
        generate_grid,
        run_optimization,
    )
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

    # Extract inputs
    inputs = extract_pine_inputs(ast)
    if not inputs:
        print("Error: no input.int() / input.float() parameters found in script")
        print("Add input declarations with minval/maxval/step for optimization")
        sys.exit(1)

    print(f"Found {len(inputs)} optimizable parameter(s):")
    for inp in inputs:
        lo = inp.minval if inp.minval is not None else "auto"
        hi = inp.maxval if inp.maxval is not None else "auto"
        st = inp.step if inp.step is not None else "auto"
        print(
            f"  {inp.var_name} ({inp.title}): default={inp.defval},"
            f" range=[{lo}, {hi}], step={st}"
        )

    # Generate grid
    grid = generate_grid(inputs)
    print(f"Grid: {len(grid)} combinations")

    # Fetch data
    print(f"\nFetching {args.timeframe} data for {args.symbol} from {args.exchange}...")
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

    # Run optimization
    print(f"\nOptimizing ({len(grid)} runs)...")
    results = run_optimization(
        ast=ast,
        bars=bars,
        grid=grid,
        metric=args.metric,
    )

    # Print top results
    top_n = min(args.top, len(results))
    print(f"\n{'=' * 80}")
    print(f"  Top {top_n} Results (ranked by {args.metric})")
    print(f"{'=' * 80}")

    # Build header from param names
    param_names = [inp.title for inp in inputs]
    header_parts = [f"{'#':>4}"]
    for name in param_names:
        header_parts.append(f"{name:>10}")
    header_parts.extend(
        [
            f"{'Sharpe':>8}",
            f"{'Return':>9}",
            f"{'Trades':>7}",
            f"{'WinRate':>8}",
            f"{'PF':>7}",
            f"{'MaxDD':>8}",
        ]
    )
    print("  " + "  ".join(header_parts))
    print("  " + "-" * (len("  ".join(header_parts)) + 2))

    for i, r in enumerate(results[:top_n], 1):
        parts = [f"{i:>4}"]
        for name in param_names:
            val = r.params.get(name, 0)
            if val == int(val):
                parts.append(f"{int(val):>10}")
            else:
                parts.append(f"{val:>10.2f}")
        parts.extend(
            [
                f"{r.sharpe:>8.2f}",
                f"{r.return_pct:>8.2%}",
                f"{r.total_trades:>7}",
                f"{r.win_rate:>7.1%}",
                f"{r.profit_factor:>7.2f}",
                f"{r.max_drawdown:>7.2%}",
            ]
        )
        print("  " + "  ".join(parts))

    print(f"{'=' * 80}")

    # Export as JSON if requested
    if args.json_output:
        import json

        json_data = {
            "inputs": [
                {
                    "var_name": inp.var_name,
                    "title": inp.title,
                    "type": inp.input_type,
                    "defval": inp.defval,
                    "minval": inp.minval,
                    "maxval": inp.maxval,
                    "step": inp.step,
                }
                for inp in inputs
            ],
            "results": [
                {
                    "params": r.params,
                    "sharpe": r.sharpe,
                    "return_pct": r.return_pct,
                    "net_profit": r.net_profit,
                    "total_trades": r.total_trades,
                    "win_rate": r.win_rate,
                    "profit_factor": r.profit_factor,
                    "max_drawdown": r.max_drawdown,
                }
                for r in results
            ],
        }
        json_path = Path(args.json_output)
        json_path.write_text(json.dumps(json_data, indent=2))
        print(f"\nFull results exported to {json_path}")


def _run_deploy(args: argparse.Namespace) -> None:
    """Transpile Pine Script to Strategy API and prepare for live trading."""
    from quantforge.pine.parser.parser import parse
    from quantforge.pine.transpiler.codegen import transpile_strategy_api

    pine_file = Path(args.pine_file)
    if not pine_file.exists():
        print(f"Error: file '{pine_file}' not found")
        sys.exit(1)

    source = pine_file.read_text()
    ast = parse(source)

    # Generate Strategy API code
    python_code = transpile_strategy_api(ast, pine_source=source)

    # Save to strategy/pine_strategies/
    output_dir = Path("strategy/pine_strategies")
    output_dir.mkdir(parents=True, exist_ok=True)

    strategy_name = pine_file.stem.replace("-", "_").replace(" ", "_")
    output_file = output_dir / f"{strategy_name}.py"
    output_file.write_text(python_code)
    print(f"Transpiled to {output_file}")

    # Show deployment instructions
    print(f"\nStrategy saved to {output_file}")
    print(f"Exchange: {args.exchange} ({'demo' if args.demo else 'live'})")
    print(f"Symbol: {args.symbol}")
    print("\nTo run live trading:")
    print(f"  1. Review the generated strategy: {output_file}")
    print("  2. Import and deploy:")
    print("     from quantforge.dsl.runner import deploy")
    print(f"     from strategy.pine_strategies.{strategy_name} import *")
    print("     # Then call deploy() with the strategy class")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="quantforge-pine",
        description="QuantForge Pine Script engine — parse, backtest, transpile, and deploy",
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
    tp.add_argument(
        "--strategy-api",
        action="store_true",
        help="Generate quantforge.dsl.Strategy subclass (default: standalone script)",
    )

    # live subcommand
    lv = sub.add_parser("live", help="Run Pine Script as a live trading engine")
    lv.add_argument("pine_file", help="Path to .pine file")
    lv.add_argument(
        "--exchange", default="bitget", help="Exchange id (default: bitget)"
    )
    lv.add_argument(
        "--symbol",
        default="BTC/USDT:USDT",
        help="Trading symbol (default: BTC/USDT:USDT)",
    )
    lv.add_argument("--timeframe", default="15m", help="Kline timeframe (default: 15m)")
    lv.add_argument(
        "--demo", action="store_true", default=True, help="Demo/paper mode (default)"
    )
    lv.add_argument(
        "--no-demo",
        dest="demo",
        action="store_false",
        help="Disable demo mode (real money)",
    )
    lv.add_argument(
        "--confirm-live",
        action="store_true",
        default=False,
        help="Required flag for real money trading",
    )
    lv.add_argument(
        "--warmup-bars", type=int, default=500, help="Warmup bar count (default: 500)"
    )
    lv.add_argument(
        "--position-size",
        type=float,
        default=100.0,
        help="Position size in USDT (default: 100)",
    )

    # optimize subcommand
    op = sub.add_parser(
        "optimize", help="Grid search optimization over input parameters"
    )
    op.add_argument("pine_file", help="Path to .pine file")
    op.add_argument(
        "--symbol",
        default="BTC/USDT:USDT",
        help="Trading symbol (default: BTC/USDT:USDT)",
    )
    op.add_argument(
        "--exchange", default="bitget", help="Exchange id (default: bitget)"
    )
    op.add_argument("--timeframe", default="15m", help="Kline timeframe (default: 15m)")
    op.add_argument("--start", default="2026-01-01", help="Start date YYYY-MM-DD")
    op.add_argument("--end", default="2026-03-12", help="End date YYYY-MM-DD")
    op.add_argument(
        "--warmup-days",
        type=int,
        default=60,
        help="Warmup period in days (default: 60)",
    )
    op.add_argument(
        "--metric",
        default="sharpe",
        choices=["sharpe", "return", "profit_factor"],
        help="Metric to rank results by (default: sharpe)",
    )
    op.add_argument(
        "--top",
        type=int,
        default=10,
        help="Number of top results to display (default: 10)",
    )
    op.add_argument(
        "--json",
        dest="json_output",
        help="Export full results as JSON to this file",
    )

    # deploy subcommand
    dp = sub.add_parser(
        "deploy", help="Transpile Pine Script and prepare for live trading"
    )
    dp.add_argument("pine_file", help="Path to .pine file")
    dp.add_argument("--exchange", default="bitget", help="Exchange (default: bitget)")
    dp.add_argument(
        "--demo", action="store_true", default=True, help="Use demo/testnet mode"
    )
    dp.add_argument(
        "--symbol",
        default="BTCUSDT-PERP",
        help="Trading symbol (default: BTCUSDT-PERP)",
    )

    parsed = parser.parse_args()

    if parsed.command == "backtest":
        _run_backtest(parsed)
    elif parsed.command == "transpile":
        _run_transpile(parsed)
    elif parsed.command == "live":
        _run_live(parsed)
    elif parsed.command == "optimize":
        _run_optimize(parsed)
    elif parsed.command == "deploy":
        _run_deploy(parsed)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
