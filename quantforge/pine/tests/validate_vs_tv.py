"""
Validate Pine Script engine against TradingView.

Usage:
    python -m quantforge.pine.tests.validate_vs_tv

Fetches real BTC/USDT 15min data from Binance, runs an EMA Cross strategy
through the Pine interpreter, and prints trades + indicator values for
manual comparison with TradingView.
"""

import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timezone

from quantforge.pine.parser.parser import parse as pine_parse
from quantforge.pine.interpreter.context import ExecutionContext
from quantforge.pine.interpreter.runtime import PineRuntime


def fetch_btc_15m(days=7):
    """Fetch BTC/USDT 15min klines from Binance."""
    exchange = ccxt.bitget({"enableRateLimit": True})
    since = exchange.parse8601(
        (datetime.now(timezone.utc) - pd.Timedelta(days=days)).isoformat()
    )
    all_ohlcv = []
    while True:
        ohlcv = exchange.fetch_ohlcv("BTC/USDT:USDT", "15m", since=since, limit=1000)
        if not ohlcv:
            break
        all_ohlcv.extend(ohlcv)
        since = ohlcv[-1][0] + 1
        if len(ohlcv) < 1000:
            break

    df = pd.DataFrame(all_ohlcv, columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    print(f"Fetched {len(df)} bars from {df['time'].iloc[0]} to {df['time'].iloc[-1]}")
    return df


def run_ema_cross(df):
    """Run EMA Cross strategy through Pine interpreter."""
    pine_src = """//@version=5
strategy("EMA Cross 5/13", overlay=true)
fast_len = input.int(5, title="Fast Length")
slow_len = input.int(13, title="Slow Length")
fast = ta.ema(close, fast_len)
slow = ta.ema(close, slow_len)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("Long")
"""

    ast = pine_parse(pine_src)
    df_ctx = df.copy()
    df_ctx["time"] = df_ctx["time"].astype(np.int64) // 10**6  # to unix ms
    ctx = ExecutionContext.from_dataframe(df_ctx)
    runtime = PineRuntime(ctx)
    result = runtime.run(ast)
    return result, runtime


def validate_ema_values(df, length=5):
    """Compute EMA manually using TradingView's formula for comparison."""
    closes = df["close"].values
    alpha = 2.0 / (length + 1)

    # TV seeds EMA with SMA of first `length` bars
    ema = np.full(len(closes), np.nan)
    if len(closes) >= length:
        ema[length - 1] = np.mean(closes[:length])
        for i in range(length, len(closes)):
            ema[i] = alpha * closes[i] + (1 - alpha) * ema[i - 1]

    return ema


def main():
    print("=" * 60)
    print("Pine Script Engine vs TradingView Validation")
    print("Strategy: EMA Cross 5/13 on BTC/USDT 15min")
    print("=" * 60)

    # Fetch data
    df = fetch_btc_15m(days=7)

    # Run through Pine interpreter
    result, runtime = run_ema_cross(df)

    # Print result summary
    print(f"\n{'='*60}")
    print(f"Initial Capital: ${result.initial_capital:,.2f}")
    print(f"Net Profit:      ${result.net_profit:,.2f}")
    print(f"Total Trades:    {result.total_trades}")
    print(f"Win Rate:        {result.win_rate:.1%}")
    print(f"Winning Trades:  {result.winning_trades}")
    print(f"Losing Trades:   {result.losing_trades}")

    # Print trades
    print(f"\n{'='*60}")
    print(f"TRADES ({result.total_trades} total):")
    print(f"{'='*60}")
    if hasattr(result, "trades") and result.trades:
        for i, trade in enumerate(result.trades[:20]):  # Show first 20
            print(f"  #{i+1}: {trade}")
    else:
        print("  No trades recorded")

    # Validate EMA values
    print(f"\n{'='*60}")
    print("EMA VALIDATION (last 10 bars):")
    print(f"{'='*60}")
    manual_ema5 = validate_ema_values(df, 5)
    manual_ema13 = validate_ema_values(df, 13)

    print(f"{'Bar':>6} | {'Time':>20} | {'Close':>10} | {'EMA5(manual)':>12} | {'EMA13(manual)':>13}")
    print("-" * 75)
    for i in range(-10, 0):
        idx = len(df) + i
        print(
            f"{idx:>6} | {str(df['time'].iloc[idx]):>20} | "
            f"{df['close'].iloc[idx]:>10.2f} | "
            f"{manual_ema5[idx]:>12.2f} | "
            f"{manual_ema13[idx]:>13.2f}"
        )

    print(f"\n✅ Copy the EMA5/EMA13 values above and compare with TradingView's")
    print(f"   EMA(5) and EMA(13) indicator on BTC/USDT 15min chart.")
    print(f"   They should match to 2+ decimal places.")


if __name__ == "__main__":
    main()
