"""
Fully aligned Pine engine vs TradingView comparison.
Key fix: Fetch warmup data BEFORE the test range so EMA values are pre-seeded.
"""

import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timezone

from quantforge.pine.parser.parser import parse as pine_parse
from quantforge.pine.interpreter.context import ExecutionContext, BarData
from quantforge.pine.interpreter.runtime import PineRuntime


def fetch_btc_15m(start_ts, end_ts, exchange):
    """Fetch all 15m bars between start_ts and end_ts (milliseconds)."""
    since = start_ts
    all_ohlcv = []
    while since < end_ts:
        ohlcv = exchange.fetch_ohlcv("BTC/USDT:USDT", "15m", since=since, limit=200)
        if not ohlcv:
            break
        new_bars = [bar for bar in ohlcv if bar[0] > (all_ohlcv[-1][0] if all_ohlcv else 0)]
        if not new_bars and ohlcv:
            new_bars = ohlcv
        all_ohlcv.extend(new_bars)
        last_ts = ohlcv[-1][0]
        if last_ts <= since:
            break
        since = last_ts + 1
        if len(all_ohlcv) % 2000 < 200:
            print(f"  Fetched {len(all_ohlcv)} bars... (last: {pd.to_datetime(last_ts, unit='ms')})")

    df = pd.DataFrame(all_ohlcv, columns=["time", "open", "high", "low", "close", "volume"])
    df = df[(df["time"] >= start_ts) & (df["time"] <= end_ts)]
    df = df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    return df


def df_to_bars(df):
    return [
        BarData(
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            time=int(row["time"]),
        )
        for _, row in df.iterrows()
    ]


def main():
    print("=" * 70)
    print("FULLY ALIGNED Pine Engine vs TradingView Comparison")
    print("EMA Cross 5/13 | BTC/USDT Perp 15min | Jan 1 - Mar 12, 2026")
    print("=" * 70)

    exchange = ccxt.bitget({"enableRateLimit": True})

    # TradingView uses ~5000-20000 bars of history for warmup.
    # We fetch from Nov 1, 2025 (2 months warmup = ~5760 bars at 15min)
    warmup_start = "2025-11-01T00:00:00Z"
    test_start = "2026-01-01T00:00:00Z"
    test_end = "2026-03-12T23:59:59Z"

    warmup_start_ms = exchange.parse8601(warmup_start)
    test_start_ms = exchange.parse8601(test_start)
    test_end_ms = exchange.parse8601(test_end)

    print(f"\n[1/3] Fetching warmup data from Nov 1, 2025...")
    warmup_df = fetch_btc_15m(warmup_start_ms, test_start_ms - 1, exchange)
    print(f"  Warmup: {len(warmup_df)} bars")

    print(f"\n[2/3] Fetching test data Jan 1 - Mar 12, 2026...")
    test_df = fetch_btc_15m(test_start_ms, test_end_ms, exchange)
    print(f"  Test: {len(test_df)} bars")

    # Combine warmup + test
    full_df = pd.concat([warmup_df, test_df], ignore_index=True)
    full_df = full_df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    print(f"  Total (warmup+test): {len(full_df)} bars")

    warmup_bar_count = len(warmup_df)

    # Run strategy on FULL data (warmup + test)
    print(f"\n[3/3] Running Pine strategy on full data...")
    pine_src = """//@version=5
strategy("EMA Cross 5/13", overlay=true, initial_capital=1000000)
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
    bars = df_to_bars(full_df)
    ctx = ExecutionContext(bars=bars)
    runtime = PineRuntime(ctx)
    result = runtime.run(ast)

    # Filter trades to only those in test range (entry_bar >= warmup_bar_count)
    all_trades = result.trades
    test_trades = [t for t in all_trades if t.entry_bar >= warmup_bar_count]
    
    print(f"\n  Total trades (including warmup): {len(all_trades)}")
    print(f"  Trades in test range only: {len(test_trades)}")

    # Metrics for test-range trades only
    winners = sum(1 for t in test_trades if t.pnl > 0)
    losers = sum(1 for t in test_trades if t.pnl <= 0)
    winrate = (winners / len(test_trades) * 100) if test_trades else 0
    net_pnl = sum(t.pnl for t in test_trades)
    gross_profit = sum(t.pnl for t in test_trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in test_trades if t.pnl < 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    # Also check: is there an open trade that spans warmup→test?
    # If the last warmup trade exits in test range, we should count it
    warmup_trades_exiting_in_test = [
        t for t in all_trades
        if t.entry_bar < warmup_bar_count and t.exit_bar >= warmup_bar_count
    ]
    if warmup_trades_exiting_in_test:
        print(f"  Trades spanning warmup→test: {len(warmup_trades_exiting_in_test)}")
        for t in warmup_trades_exiting_in_test:
            print(f"    entry bar {t.entry_bar} (warmup), exit bar {t.exit_bar} (test), pnl={t.pnl:.2f}")

    # TradingView reference
    tv_pnl = -6810.96
    tv_trades = 270
    tv_winrate = 27.78
    tv_winners = 75
    tv_losers = 195
    tv_pf = 0.885

    print(f"\n{'='*70}")
    print(f"{'Metric':<25} {'Pine Engine':>15} {'TradingView':>15} {'Diff':>12} {'Match':>8}")
    print("-" * 70)
    
    trade_match = "✅" if abs(len(test_trades) - tv_trades) <= 1 else "❌"
    pnl_match = "✅" if abs(net_pnl - tv_pnl) / abs(tv_pnl) < 0.02 else "⚠️" if abs(net_pnl - tv_pnl) / abs(tv_pnl) < 0.10 else "❌"
    wr_match = "✅" if abs(winrate - tv_winrate) < 1.0 else "⚠️"
    pf_match = "✅" if abs(pf - tv_pf) < 0.02 else "⚠️"

    print(f"{'Total P&L':<25} {net_pnl:>15.2f} {tv_pnl:>15.2f} {net_pnl - tv_pnl:>12.2f} {pnl_match:>8}")
    print(f"{'Total Trades':<25} {len(test_trades):>15} {tv_trades:>15} {len(test_trades) - tv_trades:>12} {trade_match:>8}")
    print(f"{'Win Rate %':<25} {winrate:>15.2f} {tv_winrate:>15.2f} {winrate - tv_winrate:>12.2f} {wr_match:>8}")
    print(f"{'Winners':<25} {winners:>15} {tv_winners:>15} {winners - tv_winners:>12}")
    print(f"{'Losers':<25} {losers:>15} {tv_losers:>15} {losers - tv_losers:>12}")
    print(f"{'Profit Factor':<25} {pf:>15.3f} {tv_pf:>15.3f} {pf - tv_pf:>12.3f} {pf_match:>8}")

    # First and last trades for manual comparison
    print(f"\n{'='*70}")
    print("First 5 trades (test range):")
    for i, t in enumerate(test_trades[:5]):
        bar_time = pd.to_datetime(full_df.iloc[t.entry_bar]["time"], unit="ms")
        exit_time = pd.to_datetime(full_df.iloc[t.exit_bar]["time"], unit="ms")
        print(f"  #{i+1}: {bar_time} → {exit_time} | entry={t.entry_price:.1f} exit={t.exit_price:.1f} | pnl={t.pnl:+.2f}")

    print(f"\nLast 5 trades (test range):")
    for i, t in enumerate(test_trades[-5:]):
        idx = len(test_trades) - 5 + i
        bar_time = pd.to_datetime(full_df.iloc[t.entry_bar]["time"], unit="ms")
        exit_time = pd.to_datetime(full_df.iloc[t.exit_bar]["time"], unit="ms")
        print(f"  #{idx+1}: {bar_time} → {exit_time} | entry={t.entry_price:.1f} exit={t.exit_price:.1f} | pnl={t.pnl:+.2f}")

    # EMA values at a few key points for spot-checking
    print(f"\n{'='*70}")
    print("EMA spot-check (last 5 bars of test range):")
    alpha5 = 2.0 / (5 + 1)
    alpha13 = 2.0 / (13 + 1)
    closes = full_df["close"].values
    
    # Compute EMA manually on full data
    ema5 = np.full(len(closes), np.nan)
    ema13 = np.full(len(closes), np.nan)
    if len(closes) >= 5:
        ema5[4] = np.mean(closes[:5])
        for i in range(5, len(closes)):
            ema5[i] = alpha5 * closes[i] + (1 - alpha5) * ema5[i - 1]
    if len(closes) >= 13:
        ema13[12] = np.mean(closes[:13])
        for i in range(13, len(closes)):
            ema13[i] = alpha13 * closes[i] + (1 - alpha13) * ema13[i - 1]

    print(f"  {'Time':<22} {'Close':>10} {'EMA5':>12} {'EMA13':>12}")
    for i in range(-5, 0):
        idx = len(full_df) + i
        t = pd.to_datetime(full_df.iloc[idx]["time"], unit="ms")
        print(f"  {str(t):<22} {closes[idx]:>10.2f} {ema5[idx]:>12.2f} {ema13[idx]:>12.2f}")

    overall = "✅ ALIGNED" if trade_match == "✅" and pnl_match in ("✅", "⚠️") else "❌ NEEDS WORK"
    print(f"\n{'='*70}")
    print(f"Overall: {overall}")


if __name__ == "__main__":
    main()
