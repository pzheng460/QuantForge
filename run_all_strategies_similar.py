"""Run all strategies on the most shape-similar period (2022-02 → 2022-08)."""

import asyncio
import subprocess
import sys

STRATEGIES = [
    "momentum",
    "ema_crossover",
    "bollinger_band",
    "dual_regime",
    "sma_trend",
    "fear_reversal",
    "ma_convergence",
    "regime_ema",
    "hurst_kalman",
    "vwap",
]

# Most similar 6-month period
START = "2022-02-06"
END = "2022-08-10"

# Also test the 3-month match
PERIODS = [
    ("6mo match (2022-02→08)", "2022-02-06", "2022-08-10"),
    ("3mo match (2022-03→06)", "2022-03-05", "2022-06-07"),
]


async def main():
    for period_label, start, end in PERIODS:
        print(f"\n{'#'*70}")
        print(f"# {period_label}")
        print(f"# BTC price period: {start} → {end}")
        print(f"{'#'*70}")

        for strat in STRATEGIES:
            print(f"\n--- {strat} ---")
            try:
                result = subprocess.run(
                    [
                        sys.executable, "-m", "strategy.backtest",
                        "-S", strat,
                        "--start", start,
                        "--end", end,
                        "-L", "5",
                        "--no-validate",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                # Extract key metrics from output
                output = result.stdout + result.stderr
                for line in output.split("\n"):
                    line = line.strip()
                    if any(k in line for k in [
                        "Total Return:", "Max Drawdown:", "Sharpe Ratio:",
                        "Total Trades:", "Win Rate:", "Profit Factor:",
                        "No data", "ERROR", "Error", "Traceback",
                    ]):
                        print(f"  {line}")
                if result.returncode != 0 and "Traceback" in output:
                    # Print last error line
                    lines = [l for l in output.split("\n") if l.strip()]
                    if lines:
                        print(f"  ERROR: {lines[-1].strip()}")
            except subprocess.TimeoutExpired:
                print(f"  TIMEOUT")
            except Exception as e:
                print(f"  ERROR: {e}")


asyncio.run(main())
