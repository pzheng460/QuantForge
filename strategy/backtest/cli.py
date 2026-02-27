"""
Unified CLI for the backtest framework.

Single entry point replacing three identical main() functions.

Usage:
    uv run python -m strategy.backtest -S hurst_kalman -X binance -p 1y --full
    uv run python -m strategy.backtest -S ema_crossover -X okx --heatmap
    uv run python -m strategy.backtest -S bollinger_band -X bybit --optimize
"""

import argparse
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from strategy.backtest.exchange_profiles import get_profile, list_exchanges
from strategy.backtest.runner import BacktestRunner

from strategy.backtest.utils import (
    DEFAULT_PERIOD,
    PERIODS,
    fetch_data,
    fetch_funding_rates,
    print_results_table,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unified backtest framework for NexusTrader strategies.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "-S",
        "--strategy",
        type=str,
        required=True,
        help="Strategy name (e.g. hurst_kalman, ema_crossover, bollinger_band)",
    )
    parser.add_argument(
        "-X",
        "--exchange",
        type=str,
        default="bitget",
        help=f"Exchange ({', '.join(list_exchanges())}). Default: bitget",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Trading pair symbol (default: exchange default, e.g. BTC/USDT:USDT)",
    )
    parser.add_argument(
        "-p",
        "--period",
        type=str,
        default=DEFAULT_PERIOD,
        choices=list(PERIODS.keys()),
        help=f"Data period. Default: {DEFAULT_PERIOD}",
    )
    parser.add_argument(
        "-m",
        "--mesa",
        type=int,
        default=None,
        help="Mesa config index (0 = best). Runs single backtest with this config.",
    )
    parser.add_argument(
        "--heatmap",
        action="store_true",
        help="Run heatmap parameter scan",
    )
    parser.add_argument(
        "--heatmap-resolution",
        type=int,
        default=15,
        help="Heatmap grid resolution (default: 15)",
    )
    parser.add_argument(
        "-o",
        "--optimize",
        action="store_true",
        help="Run grid search optimization",
    )
    parser.add_argument(
        "-w",
        "--walk-forward",
        action="store_true",
        help="Run walk-forward validation",
    )
    parser.add_argument(
        "-r",
        "--regime",
        action="store_true",
        help="Run regime analysis (requires single backtest first)",
    )
    parser.add_argument(
        "-f",
        "--full",
        action="store_true",
        help="Run complete three-stage test (optimize + walk-forward + holdout)",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Generate HTML report after backtest",
    )
    parser.add_argument(
        "-s",
        "--show-results",
        action="store_true",
        help="Show saved backtest results",
    )
    parser.add_argument(
        "-e",
        "--export-config",
        action="store_true",
        help="Export optimized config for paper trading (used with --full)",
    )
    parser.add_argument(
        "-L",
        "--leverage",
        type=float,
        default=1.0,
        help="Leverage multiplier (default: 1.0, e.g. 5 for 5x leverage)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Custom output directory for results",
    )

    return parser


async def async_main(args: argparse.Namespace) -> None:
    """Main async entry point."""
    output_dir = Path(args.output_dir) if args.output_dir else None
    runner = BacktestRunner(
        strategy_name=args.strategy,
        exchange=args.exchange,
        symbol=args.symbol,
        output_dir=output_dir,
        leverage=args.leverage,
    )

    # Show results and exit
    if args.show_results:
        results = runner.load_results()
        print_results_table(results)
        return

    # Fetch data
    days = PERIODS.get(args.period, 365)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    profile = get_profile(args.exchange)

    symbol = args.symbol or profile.default_symbol
    data = await fetch_data(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        interval=runner.reg.default_interval,
        exchange=profile.ccxt_id,
    )
    funding_rates = await fetch_funding_rates(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        exchange=profile.ccxt_id,
    )

    # Dispatch to the appropriate mode
    if args.heatmap:
        runner.run_heatmap(
            data=data,
            funding_rates=funding_rates,
            period=args.period,
            resolution=args.heatmap_resolution,
        )
        return

    if args.full:
        runner.run_three_stage_test(
            data=data,
            funding_rates=funding_rates,
            period=args.period,
            export_config_flag=args.export_config,
        )
        return

    if args.optimize:
        runner.run_grid_search(data=data, period=args.period)
        return

    if args.walk_forward:
        runner.run_walk_forward(data=data)
        return

    # Default: single backtest with Mesa config
    mesa_index = args.mesa if args.mesa is not None else 0
    result_dict = runner.run_single(
        data=data,
        mesa_index=mesa_index,
        period=args.period,
        funding_rates=funding_rates,
    )

    if args.regime and "result" in result_dict:
        runner.run_regime_analysis(data, result_dict["result"])

    if args.report and "result" in result_dict:
        runner.generate_report(result_dict["result"])

    # Save results
    saved = runner.load_results()
    key = f"mesa{mesa_index}_{args.period}"
    saved[key] = {k: v for k, v in result_dict.items() if k not in ("result", "data")}
    runner.save_results(saved)


def main():
    """Synchronous entry point."""
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(async_main(args))


def run_with_args(
    strategy: str,
    exchange_default: str = "bitget",
    output_dir_default: Optional[str] = None,
):
    """Entry point for backward-compatible shims.

    Allows old backtest.py scripts to delegate to the unified CLI
    while defaulting to their original exchange.
    """
    parser = build_parser()
    parser.set_defaults(strategy=strategy, exchange=exchange_default)
    if output_dir_default:
        parser.set_defaults(output_dir=output_dir_default)
    args = parser.parse_args()
    # Override strategy so -S is not required for shims
    args.strategy = strategy
    asyncio.run(async_main(args))
