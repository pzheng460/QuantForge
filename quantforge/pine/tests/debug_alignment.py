"""
Debug P&L alignment: find where our trades differ from TV.
Investigate: warmup length, borderline trades, systematic price bias.
"""

import ccxt
import pandas as pd
import numpy as np
from quantforge.pine.parser.parser import parse as pine_parse
from quantforge.pine.interpreter.context import ExecutionContext, BarData
from quantforge.pine.interpreter.runtime import PineRuntime


def fetch_all(exchange, start_str, end_str):
    since = exchange.parse8601(start_str)
    end_ts = exchange.parse8601(end_str)
    all_ohlcv = []
    while since < end_ts:
        ohlcv = exchange.fetch_ohlcv("BTC/USDT:USDT", "15m", since=since, limit=200)
        if not ohlcv:
            break
        new_bars = [b for b in ohlcv if b[0] > (all_ohlcv[-1][0] if all_ohlcv else 0)]
        if not new_bars:
            new_bars = ohlcv
        all_ohlcv.extend(new_bars)
        last_ts = ohlcv[-1][0]
        if last_ts <= since:
            break
        since = last_ts + 1
        if len(all_ohlcv) % 5000 < 200:
            print(f"  {len(all_ohlcv)} bars...")
    df = pd.DataFrame(all_ohlcv, columns=["time", "open", "high", "low", "close", "volume"])
    df = df[(df["time"] >= exchange.parse8601(start_str)) & (df["time"] <= exchange.parse8601(end_str))]
    return df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)


def df_to_bars(df):
    return [BarData(open=float(r["open"]), high=float(r["high"]), low=float(r["low"]),
                    close=float(r["close"]), volume=float(r["volume"]), time=int(r["time"]))
            for _, r in df.iterrows()]


def run_pine(full_df):
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
    return runtime.run(ast)


def main():
    exchange = ccxt.bitget({"enableRateLimit": True})

    test_start = "2026-01-01T00:00:00Z"
    test_end = "2026-03-12T23:59:59Z"
    test_start_ms = exchange.parse8601(test_start)

    # ===================================================================
    # Test 1: Does more warmup help?
    # ===================================================================
    print("=" * 70)
    print("TEST 1: Warmup length impact")
    print("=" * 70)

    for warmup_months, warmup_start in [
        ("2mo", "2025-11-01T00:00:00Z"),
        ("6mo", "2025-07-01T00:00:00Z"),
        ("12mo", "2025-01-01T00:00:00Z"),
    ]:
        print(f"\n--- Warmup: {warmup_months} (from {warmup_start[:10]}) ---")
        warmup_df = fetch_all(exchange, warmup_start, "2025-12-31T23:59:59Z")
        test_df = fetch_all(exchange, test_start, test_end)
        full_df = pd.concat([warmup_df, test_df], ignore_index=True)
        full_df = full_df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
        warmup_count = len(warmup_df)
        print(f"  Warmup bars: {warmup_count}, Test bars: {len(test_df)}, Total: {len(full_df)}")

        result = run_pine(full_df)
        test_trades = [t for t in result.trades if t.entry_bar >= warmup_count]
        net_pnl = sum(t.pnl for t in test_trades)
        winners = sum(1 for t in test_trades if t.pnl > 0)
        print(f"  Trades: {len(test_trades)}, Winners: {winners}, P&L: {net_pnl:.2f}")
        print(f"  Diff from TV (-6810.96): {net_pnl - (-6810.96):+.2f}")

    # ===================================================================
    # Test 2: Analyze borderline trades (near-zero PnL)
    # ===================================================================
    print(f"\n{'='*70}")
    print("TEST 2: Borderline trades (|PnL| < 100)")
    print("=" * 70)

    # Use 6mo warmup for analysis
    warmup_df = fetch_all(exchange, "2025-07-01T00:00:00Z", "2025-12-31T23:59:59Z")
    test_df = fetch_all(exchange, test_start, test_end)
    full_df = pd.concat([warmup_df, test_df], ignore_index=True)
    full_df = full_df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    warmup_count = len(warmup_df)

    result = run_pine(full_df)
    test_trades = [t for t in result.trades if t.entry_bar >= warmup_count]

    borderline = [(i, t) for i, t in enumerate(test_trades) if abs(t.pnl) < 100]
    print(f"Borderline trades: {len(borderline)}")
    for idx, t in borderline:
        entry_time = pd.to_datetime(full_df.iloc[t.entry_bar]["time"], unit="ms")
        exit_time = pd.to_datetime(full_df.iloc[t.exit_bar]["time"], unit="ms")
        win = "W" if t.pnl > 0 else "L"
        print(f"  #{idx+1} [{win}] {entry_time} → {exit_time} | entry={t.entry_price:.2f} exit={t.exit_price:.2f} | pnl={t.pnl:+.2f}")

    # ===================================================================
    # Test 3: P&L distribution analysis
    # ===================================================================
    print(f"\n{'='*70}")
    print("TEST 3: P&L distribution")
    print("=" * 70)
    pnls = [t.pnl for t in test_trades]
    winners_pnl = [p for p in pnls if p > 0]
    losers_pnl = [p for p in pnls if p <= 0]
    
    print(f"  Net P&L: {sum(pnls):.2f} (TV: -6810.96, diff: {sum(pnls)-(-6810.96):+.2f})")
    print(f"  Gross Profit: {sum(winners_pnl):.2f}")
    print(f"  Gross Loss: {sum(losers_pnl):.2f}")
    print(f"  Avg Win: {np.mean(winners_pnl):.2f}" if winners_pnl else "  No wins")
    print(f"  Avg Loss: {np.mean(losers_pnl):.2f}" if losers_pnl else "  No losses")
    print(f"  Largest Win: {max(pnls):.2f}")
    print(f"  Largest Loss: {min(pnls):.2f}")
    
    # Systematic bias check: are our entry prices consistently off?
    print(f"\n  Entry price stats (first 20 trades):")
    for i, t in enumerate(test_trades[:20]):
        bar_idx = t.entry_bar
        actual_open = full_df.iloc[bar_idx]["open"]
        # Entry should be at next bar's open, which is entry_bar's open
        print(f"    #{i+1}: entry_price={t.entry_price:.1f}, bar_open={actual_open:.1f}, diff={t.entry_price - actual_open:.1f}")


if __name__ == "__main__":
    main()
