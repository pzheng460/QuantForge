"""
Quick backtest script for VWAP strategy with 5-minute candles.

Usage:
    uv run python strategy/strategies/vwap/run_backtest.py
"""

import asyncio
from datetime import datetime, timedelta

import pandas as pd

from nexustrader.backtest import (
    BacktestConfig,
    PerformanceAnalyzer,
    VectorizedBacktest,
)
from nexustrader.backtest.data.ccxt_provider import CCXTDataProvider
from nexustrader.backtest.data.funding_rate import FundingRateProvider
from nexustrader.constants import KlineInterval

from strategy.strategies.vwap.core import VWAPConfig
from strategy.strategies._base.signal_generator import TradeFilterConfig
from strategy.strategies.vwap.registration import _make_generator

VWAPTradeFilterConfig = TradeFilterConfig  # backward compat alias


async def fetch_5m_data(days: int = 90) -> tuple:
    """Fetch 5-minute OHLCV data and funding rates from Bitget."""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    symbol = "BTC/USDT:USDT"

    print(f"Fetching 5m data from {start_date.date()} to {end_date.date()}...")
    async with CCXTDataProvider(exchange="bitget") as provider:
        data = await provider.fetch_klines(
            symbol=symbol,
            interval=KlineInterval.MINUTE_5,
            start=start_date,
            end=end_date,
        )
    print(f"Fetched {len(data)} bars ({len(data) * 5 / 60 / 24:.0f} days)")

    print("Fetching funding rates...")
    try:
        async with FundingRateProvider(exchange="bitget") as provider:
            funding = await provider.fetch_funding_rates(
                symbol=symbol,
                start=start_date,
                end=end_date,
            )
            if not funding.empty:
                print(
                    f"Fetched {len(funding)} funding records, "
                    f"avg rate: {funding['funding_rate'].mean() * 100:.4f}% per 8h"
                )
            else:
                funding = pd.DataFrame(columns=["funding_rate"])
    except Exception as e:
        print(f"Warning: Could not fetch funding rates: {e}")
        funding = pd.DataFrame(columns=["funding_rate"])

    return data, funding


def run_backtest(
    data: pd.DataFrame,
    funding: pd.DataFrame,
    config: VWAPConfig,
    filter_config: VWAPTradeFilterConfig,
    label: str = "",
) -> dict:
    """Run a single backtest and return metrics."""
    bt_config = BacktestConfig(
        symbol="BTC/USDT:USDT",
        interval=KlineInterval.MINUTE_5,
        start_date=data.index[0].to_pydatetime(),
        end_date=data.index[-1].to_pydatetime(),
        initial_capital=10000.0,
        exchange="bitget",
    )

    from nexustrader.backtest import CostConfig

    cost_config = CostConfig(
        maker_fee=0.0002,
        taker_fee=0.0005,
        slippage_pct=0.0005,
        use_funding_rate=True,
    )

    gen = _make_generator(config, filter_config)
    signals = gen.generate(data)

    bt = VectorizedBacktest(config=bt_config, cost_config=cost_config)
    result = bt.run(data=data, signals=signals, funding_rates=funding)

    analyzer = PerformanceAnalyzer(
        equity_curve=result.equity_curve,
        trades=result.trades,
        initial_capital=bt_config.initial_capital,
    )
    metrics = analyzer.calculate_metrics()

    # Calculate weekly return estimate
    total_days = (data.index[-1] - data.index[0]).days
    total_return = metrics["total_return_pct"]
    if total_days > 0:
        daily_return = total_return / total_days
        weekly_return = daily_return * 7
    else:
        weekly_return = 0

    return {
        "label": label,
        "total_return_pct": metrics["total_return_pct"],
        "max_drawdown_pct": metrics["max_drawdown_pct"],
        "sharpe_ratio": metrics["sharpe_ratio"],
        "sortino_ratio": metrics["sortino_ratio"],
        "total_trades": metrics["total_trades"],
        "win_rate_pct": metrics["win_rate_pct"],
        "profit_factor": metrics["profit_factor"],
        "total_days": total_days,
        "weekly_return_est": weekly_return,
        "funding_paid": result.metrics.get("total_funding_paid", 0),
        "equity_curve": result.equity_curve,
    }


def print_result(r: dict):
    """Print formatted backtest result."""
    print(f"\n{'=' * 60}")
    print(f"  {r['label']}")
    print(f"{'=' * 60}")
    print(f"  Period: {r['total_days']} days")
    print(f"  Total Return:   {r['total_return_pct']:+.2f}%")
    print(f"  Est. Weekly:    {r['weekly_return_est']:+.2f}%")
    print(f"  Max Drawdown:   {r['max_drawdown_pct']:.2f}%")
    print(f"  Sharpe Ratio:   {r['sharpe_ratio']:.2f}")
    print(f"  Sortino Ratio:  {r['sortino_ratio']:.2f}")
    print(f"  Total Trades:   {r['total_trades']}")
    print(f"  Win Rate:       {r['win_rate_pct']:.1f}%")
    print(f"  Profit Factor:  {r['profit_factor']:.2f}")
    print(f"  Funding Paid:   ${r['funding_paid']:.2f}")
    print(f"{'=' * 60}")


async def main():
    # Fetch 3 months of 5-minute data
    data, funding = await fetch_5m_data(days=90)

    if len(data) == 0:
        print("No data fetched!")
        return

    filter_config = VWAPTradeFilterConfig(
        min_holding_bars=4,
        cooldown_bars=2,
        signal_confirmation=1,
    )

    # ================================================================
    # Test multiple parameter sets
    # ================================================================
    param_sets = [
        # Default spec params
        VWAPConfig(
            std_window=200,
            rsi_period=14,
            zscore_entry=2.0,
            zscore_exit=0.0,
            zscore_stop=3.5,
            rsi_oversold=30.0,
            rsi_overbought=70.0,
            position_size_pct=0.20,
            stop_loss_pct=0.03,
        ),
        # Tighter entry (Z=1.5), wider RSI filter
        VWAPConfig(
            std_window=200,
            rsi_period=14,
            zscore_entry=1.5,
            zscore_exit=0.0,
            zscore_stop=3.0,
            rsi_oversold=35.0,
            rsi_overbought=65.0,
            position_size_pct=0.20,
            stop_loss_pct=0.03,
        ),
        # Wider entry (Z=2.5), strict RSI
        VWAPConfig(
            std_window=200,
            rsi_period=14,
            zscore_entry=2.5,
            zscore_exit=0.0,
            zscore_stop=3.5,
            rsi_oversold=25.0,
            rsi_overbought=75.0,
            position_size_pct=0.20,
            stop_loss_pct=0.03,
        ),
        # Shorter lookback (100), moderate entry
        VWAPConfig(
            std_window=100,
            rsi_period=14,
            zscore_entry=2.0,
            zscore_exit=0.0,
            zscore_stop=3.5,
            rsi_oversold=30.0,
            rsi_overbought=70.0,
            position_size_pct=0.20,
            stop_loss_pct=0.03,
        ),
        # Shorter lookback + wider RSI + exit at Z=0.3
        VWAPConfig(
            std_window=100,
            rsi_period=10,
            zscore_entry=1.5,
            zscore_exit=0.3,
            zscore_stop=3.0,
            rsi_oversold=35.0,
            rsi_overbought=65.0,
            position_size_pct=0.20,
            stop_loss_pct=0.05,
        ),
        # Longer lookback (300), conservative
        VWAPConfig(
            std_window=300,
            rsi_period=20,
            zscore_entry=2.0,
            zscore_exit=0.0,
            zscore_stop=3.5,
            rsi_oversold=30.0,
            rsi_overbought=70.0,
            position_size_pct=0.20,
            stop_loss_pct=0.03,
        ),
    ]

    labels = [
        "Default (Z=2.0, W=200, RSI 30/70)",
        "Tighter (Z=1.5, W=200, RSI 35/65)",
        "Wider (Z=2.5, W=200, RSI 25/75)",
        "Short Window (Z=2.0, W=100, RSI 30/70)",
        "Aggressive (Z=1.5, W=100, RSI 35/65, exit=0.3)",
        "Conservative (Z=2.0, W=300, RSI 30/70, period=20)",
    ]

    results = []
    for config, label in zip(param_sets, labels):
        r = run_backtest(data, funding, config, filter_config, label)
        results.append(r)
        print_result(r)

    # Summary table
    print("\n" + "=" * 100)
    print("PARAMETER COMPARISON SUMMARY")
    print("=" * 100)
    print(
        f"{'Config':<45} {'Return':>8} {'Weekly':>8} {'Sharpe':>7} "
        f"{'MaxDD':>7} {'Win%':>6} {'Trades':>7} {'PF':>6}"
    )
    print("-" * 100)
    for r in results:
        print(
            f"{r['label']:<45} {r['total_return_pct']:>+7.1f}% "
            f"{r['weekly_return_est']:>+7.2f}% {r['sharpe_ratio']:>6.2f} "
            f"{r['max_drawdown_pct']:>6.1f}% {r['win_rate_pct']:>5.1f}% "
            f"{r['total_trades']:>7} {r['profit_factor']:>5.2f}"
        )
    print("=" * 100)

    # Find best
    best = max(results, key=lambda r: r["sharpe_ratio"])
    print(f"\nBest by Sharpe: {best['label']}")
    print(f"  Return: {best['total_return_pct']:+.2f}%, Weekly est: {best['weekly_return_est']:+.2f}%")
    print(f"  Sharpe: {best['sharpe_ratio']:.2f}, MaxDD: {best['max_drawdown_pct']:.2f}%")

    profitable = [r for r in results if r["total_return_pct"] > 0]
    if profitable:
        print(f"\n{len(profitable)}/{len(results)} parameter sets are profitable")
    else:
        print("\nNo parameter sets achieved positive returns in this period.")
        print("Note: Mean reversion strategies perform poorly in strong trending markets.")
        print("Consider: the market may have been trending recently.")


if __name__ == "__main__":
    asyncio.run(main())
