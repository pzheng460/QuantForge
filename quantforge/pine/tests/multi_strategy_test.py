"""Test multiple Pine strategies against Bitget data. Run all, report results."""

import ccxt
import pandas as pd
from pathlib import Path
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
        new = [b for b in ohlcv if b[0] > (all_ohlcv[-1][0] if all_ohlcv else 0)]
        if not new:
            new = ohlcv
        all_ohlcv.extend(new)
        if ohlcv[-1][0] <= since:
            break
        since = ohlcv[-1][0] + 1
        if len(all_ohlcv) % 3000 < 200:
            print(f"    {len(all_ohlcv)} bars...")
    df = pd.DataFrame(all_ohlcv, columns=["time", "open", "high", "low", "close", "volume"])
    df = df[(df["time"] >= exchange.parse8601(start_str)) & (df["time"] <= exchange.parse8601(end_str))]
    return df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)


def df_to_bars(df):
    return [
        BarData(open=float(r["open"]), high=float(r["high"]), low=float(r["low"]),
                close=float(r["close"]), volume=float(r["volume"]), time=int(r["time"]))
        for _, r in df.iterrows()
    ]


def run_pine_file(pine_path, full_df, warmup_count):
    source = Path(pine_path).read_text()
    ast = pine_parse(source)
    bars = df_to_bars(full_df)
    ctx = ExecutionContext(bars=bars)
    runtime = PineRuntime(ctx)
    result = runtime.run(ast)
    test_trades = [t for t in result.trades if t.entry_bar >= warmup_count]
    return test_trades, result


def main():
    exchange = ccxt.bitget({"enableRateLimit": True})

    print("Fetching warmup data (2025-11-01 to 2025-12-31)...")
    warmup = fetch_all(exchange, "2025-11-01T00:00:00Z", "2025-12-31T23:59:59Z")
    print("Fetching test data (2026-01-01 to 2026-03-12)...")
    test = fetch_all(exchange, "2026-01-01T00:00:00Z", "2026-03-12T23:59:59Z")

    full = pd.concat([warmup, test]).drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    wc = len(warmup)
    print(f"Total: {len(full)} bars (warmup={wc}, test={len(test)})\n")

    strategies = [
        ("EMA Cross 5/13", "quantforge/pine/tests/fixtures/ema_cross_5_13.pine"),
        ("RSI Mean Reversion", "quantforge/pine/tests/fixtures/rsi_mean_revert.pine"),
        ("MACD Cross", "quantforge/pine/tests/fixtures/macd_cross.pine"),
        ("Bollinger Bands", "quantforge/pine/tests/fixtures/bb_strategy.pine"),
    ]

    print(f"{'Strategy':<25} {'Trades':>8} {'Winners':>8} {'WinRate':>8} {'Net PnL':>12} {'PF':>8} {'AvgTrade':>10}")
    print("=" * 85)

    for name, path in strategies:
        try:
            trades, result = run_pine_file(path, full, wc)
            n = len(trades)
            w = sum(1 for t in trades if t.pnl > 0)
            wr = (w / n * 100) if n else 0
            pnl = sum(t.pnl for t in trades)
            gp = sum(t.pnl for t in trades if t.pnl > 0)
            gl = abs(sum(t.pnl for t in trades if t.pnl < 0))
            pf = gp / gl if gl > 0 else float('inf')
            avg = pnl / n if n else 0
            print(f"{name:<25} {n:>8} {w:>8} {wr:>7.1f}% {pnl:>12.2f} {pf:>8.3f} {avg:>10.2f}")
        except Exception as e:
            print(f"{name:<25} {'ERROR':>8} — {e}")

    # Show Pine code for user to paste into TV
    print(f"\n{'='*85}")
    print("Pine scripts to paste into TradingView for comparison:")
    print("=" * 85)
    for name, path in strategies:
        source = Path(path).read_text()
        print(f"\n--- {name} ---")
        print(source)


if __name__ == "__main__":
    main()
