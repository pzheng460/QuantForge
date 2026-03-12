"""Compare Pine engine vs TradingView: EMA Cross 5/13, BTC/USDT 15min, 2026-01-01 to 2026-03-12."""

import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timezone

from quantforge.pine.parser.parser import parse as pine_parse
from quantforge.pine.interpreter.context import ExecutionContext, BarData
from quantforge.pine.interpreter.runtime import PineRuntime


def fetch_btc_15m(start="2026-01-01T00:00:00Z", end="2026-03-12T23:59:59Z"):
    exchange = ccxt.bitget({"enableRateLimit": True})
    since = exchange.parse8601(start)
    end_ts = exchange.parse8601(end)
    all_ohlcv = []
    while since < end_ts:
        ohlcv = exchange.fetch_ohlcv("BTC/USDT:USDT", "15m", since=since, limit=200)
        if not ohlcv:
            break
        # Deduplicate
        new_bars = [bar for bar in ohlcv if bar[0] > (all_ohlcv[-1][0] if all_ohlcv else 0)]
        if not new_bars and ohlcv:
            new_bars = ohlcv  # First batch
        all_ohlcv.extend(new_bars)
        last_ts = ohlcv[-1][0]
        if last_ts <= since:
            break  # No progress
        since = last_ts + 1
        if len(all_ohlcv) % 2000 < 200:
            print(f"  Fetched {len(all_ohlcv)} bars so far... (last: {pd.to_datetime(last_ts, unit='ms')})")

    df = pd.DataFrame(all_ohlcv, columns=["time", "open", "high", "low", "close", "volume"])
    start_ms = exchange.parse8601(start)
    end_ms = exchange.parse8601(end)
    df = df[(df["time"] >= start_ms) & (df["time"] <= end_ms)]
    df = df.drop_duplicates(subset=["time"]).reset_index(drop=True)
    
    df["datetime"] = pd.to_datetime(df["time"], unit="ms")
    print(f"Total: {len(df)} bars from {df['datetime'].iloc[0]} to {df['datetime'].iloc[-1]}")
    return df


def df_to_bars(df):
    bars = []
    for _, row in df.iterrows():
        bars.append(BarData(
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            time=int(row["time"]),
        ))
    return bars


def run_strategy(df):
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
    bars = df_to_bars(df)
    ctx = ExecutionContext(bars=bars)
    runtime = PineRuntime(ctx)
    result = runtime.run(ast)
    return result


def main():
    print("=" * 70)
    print("Pine Engine vs TradingView Comparison")
    print("EMA Cross 5/13 | BTC/USDT 15min | Jan 1 - Mar 12, 2026")
    print("=" * 70)

    df = fetch_btc_15m()
    result = run_strategy(df)

    # TradingView reference values
    tv_pnl = -6810.96
    tv_trades = 270
    tv_winrate = 27.78
    tv_winners = 75
    tv_losers = 195
    tv_maxdd = 18173.06
    tv_pf = 0.885

    trades = result.trades
    winners = sum(1 for t in trades if t.pnl > 0)
    losers = sum(1 for t in trades if t.pnl <= 0)
    winrate = (winners / len(trades) * 100) if trades else 0
    net_pnl = sum(t.pnl for t in trades)
    
    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = abs(sum(t.pnl for t in trades if t.pnl < 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    print(f"\n{'Metric':<25} {'Pine Engine':>15} {'TradingView':>15} {'Diff':>12}")
    print("-" * 70)
    print(f"{'Total P&L':<25} {net_pnl:>15.2f} {tv_pnl:>15.2f} {net_pnl - tv_pnl:>12.2f}")
    print(f"{'Total Trades':<25} {len(trades):>15} {tv_trades:>15} {len(trades) - tv_trades:>12}")
    print(f"{'Win Rate %':<25} {winrate:>15.2f} {tv_winrate:>15.2f} {winrate - tv_winrate:>12.2f}")
    print(f"{'Winners':<25} {winners:>15} {tv_winners:>15} {winners - tv_winners:>12}")
    print(f"{'Losers':<25} {losers:>15} {tv_losers:>15} {losers - tv_losers:>12}")
    print(f"{'Profit Factor':<25} {pf:>15.3f} {tv_pf:>15.3f} {pf - tv_pf:>12.3f}")
    
    print(f"\n{'='*70}")
    print(f"First 5 trades:")
    for i, t in enumerate(trades[:5]):
        print(f"  #{i+1}: bar {t.entry_bar}→{t.exit_bar} | entry={t.entry_price:.1f} exit={t.exit_price:.1f} | pnl={t.pnl:.2f}")
    
    if len(trades) > 10:
        print(f"\nLast 5 trades:")
        for i, t in enumerate(trades[-5:]):
            idx = len(trades) - 5 + i
            print(f"  #{idx+1}: bar {t.entry_bar}→{t.exit_bar} | entry={t.entry_price:.1f} exit={t.exit_price:.1f} | pnl={t.pnl:.2f}")

    # Accuracy assessment
    print(f"\n{'='*70}")
    trade_diff_pct = abs(len(trades) - tv_trades) / tv_trades * 100
    pnl_diff_pct = abs(net_pnl - tv_pnl) / abs(tv_pnl) * 100 if tv_pnl != 0 else 0
    print(f"Trade count accuracy: {100 - trade_diff_pct:.1f}%")
    print(f"P&L accuracy: {100 - min(pnl_diff_pct, 100):.1f}%")


if __name__ == "__main__":
    main()
