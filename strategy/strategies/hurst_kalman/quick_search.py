"""
Quick parameter search for Hurst-Kalman: target 1-3 trades/day with positive returns.
"""
import sys
import os
import asyncio
os.environ['PYTHONUNBUFFERED'] = '1'

from pathlib import Path
import importlib.util

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

def _imp(name, fp, reg=None):
    spec = importlib.util.spec_from_file_location(name, fp)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    if reg: sys.modules[reg] = mod
    spec.loader.exec_module(mod)
    return mod

_core = _imp("_hk_core", _SCRIPT_DIR/"core.py", "strategy.strategies.hurst_kalman.core")
_configs = _imp("_hk_configs", _SCRIPT_DIR/"configs.py", "strategy.strategies.hurst_kalman.configs")

from collections import deque
from datetime import datetime, timedelta
import numpy as np
from nexustrader.backtest import BacktestConfig, CostConfig, PerformanceAnalyzer, Signal, VectorizedBacktest
from nexustrader.backtest.data.ccxt_provider import CCXTDataProvider
from nexustrader.constants import KlineInterval

HurstKalmanConfig = _core.HurstKalmanConfig
KalmanFilter1D = _core.KalmanFilter1D
calculate_hurst = _core.calculate_hurst

def generate_signals(data, hw, zw, ze, mr, kr, kq, mh, cd, omr, zc):
    n = len(data)
    signals = np.zeros(n)
    prices = data["close"].values
    kalman = KalmanFilter1D(R=kr, Q=kq)
    kalman_prices = []
    price_history = deque(maxlen=hw + 50)
    position = 0
    entry_bar = 0
    cooldown_until = 0

    for i in range(n):
        price = prices[i]
        price_history.append(price)
        kp = kalman.update(price)
        kalman_prices.append(kp)
        if i < hw + zw: continue

        hurst = calculate_hurst(np.array(price_history), hw)
        rp = np.array(list(price_history)[-zw:])
        rk = np.array(kalman_prices[-zw:])
        std = np.std(rp - rk)
        zscore = (price - kp) / std if std > 1e-10 else 0.0
        is_mr = hurst < mr

        if omr and not is_mr:
            if position != 0 and i - entry_bar >= mh:
                signals[i] = Signal.CLOSE.value
                position = 0
                cooldown_until = i + cd
            continue
        if i < cooldown_until: continue

        sig = 0
        if zscore < -ze: sig = Signal.BUY.value
        elif zscore > ze: sig = Signal.SELL.value
        elif abs(zscore) < zc and position != 0: sig = Signal.CLOSE.value

        if sig == Signal.BUY.value:
            if position == -1 and i - entry_bar >= mh:
                signals[i] = Signal.CLOSE.value; position = 0; cooldown_until = i + cd
            elif position == 0:
                signals[i] = Signal.BUY.value; position = 1; entry_bar = i
        elif sig == Signal.SELL.value:
            if position == 1 and i - entry_bar >= mh:
                signals[i] = Signal.CLOSE.value; position = 0; cooldown_until = i + cd
            elif position == 0:
                signals[i] = Signal.SELL.value; position = -1; entry_bar = i
        elif sig == Signal.CLOSE.value:
            if position != 0 and i - entry_bar >= mh:
                signals[i] = Signal.CLOSE.value; position = 0; cooldown_until = i + cd
    return signals

def run_bt(data, params):
    signals = generate_signals(data, **params)
    bt_config = BacktestConfig(symbol="BTC/USDT:USDT", interval=KlineInterval.MINUTE_15,
        start_date=data.index[0].to_pydatetime(), end_date=data.index[-1].to_pydatetime(), initial_capital=10000.0)
    cost = CostConfig(maker_fee=0.0002, taker_fee=0.0005, slippage_pct=0.0005, use_funding_rate=False)
    bt = VectorizedBacktest(config=bt_config, cost_config=cost)
    result = bt.run(data=data, signals=signals)
    analyzer = PerformanceAnalyzer(equity_curve=result.equity_curve, trades=result.trades, initial_capital=10000.0)
    m = analyzer.calculate_metrics()
    days = max((data.index[-1] - data.index[0]).days, 1)
    m["trades_per_day"] = round(m["total_trades"] / days, 2)
    return m

async def main():
    print("=== Quick Hurst-Kalman Search ===", flush=True)
    end = datetime.now(); start = end - timedelta(days=90)
    print("Fetching 3m data...", flush=True)
    async with CCXTDataProvider(exchange="bitget") as p:
        data = await p.fetch_klines(symbol="BTC/USDT:USDT", interval=KlineInterval.MINUTE_15, start=start, end=end)
    days = (data.index[-1] - data.index[0]).days
    print(f"Got {len(data)} bars, {days} days", flush=True)

    results = []
    # Focused grid: relax everything for more trades
    combos = []
    for hw in [30, 48, 60]:
        for zw in [20, 30]:
            for ze in [1.2, 1.5, 1.8, 2.0]:
                for mr in [0.45, 0.50, 0.55]:
                    for mh in [2, 3, 4]:
                        for cd in [1, 2]:
                            for zc in [0.3, 0.5]:
                                for omr in [True, False]:
                                    combos.append(dict(hw=hw,zw=zw,ze=ze,mr=mr,kr=0.2,kq=5e-5,mh=mh,cd=cd,omr=omr,zc=zc))

    print(f"Testing {len(combos)} combos...", flush=True)
    for i, p in enumerate(combos):
        try:
            m = run_bt(data, p)
            results.append({"p": p, **{k: m[k] for k in ["total_return_pct","sharpe_ratio","max_drawdown_pct","win_rate_pct","total_trades","trades_per_day","profit_factor"]}})
        except: pass
        if (i+1) % 100 == 0:
            print(f"  {i+1}/{len(combos)}", flush=True)

    print(f"\n{'='*120}", flush=True)
    print(f"Total results: {len(results)}", flush=True)

    # Filter: 1-3 trades/day, positive return
    target = [r for r in results if 0.5 <= r["trades_per_day"] <= 4.0 and r["total_return_pct"] > 0]
    strict = [r for r in results if 0.8 <= r["trades_per_day"] <= 3.5 and r["total_return_pct"] > 0 and r["win_rate_pct"] > 48 and r["max_drawdown_pct"] < 15]

    for label, subset in [("STRICT", strict), ("TARGET", target)]:
        if not subset: 
            print(f"\n{label}: 0 results", flush=True)
            continue
        subset.sort(key=lambda x: x.get("sharpe_ratio",0), reverse=True)
        print(f"\n--- {label}: Top 20 by Sharpe ---", flush=True)
        print(f"{'#':>3} {'Ret':>8} {'Sharpe':>8} {'MaxDD':>8} {'WR':>7} {'Trades':>7} {'T/Day':>7} {'PF':>7}  Params", flush=True)
        print("-"*130, flush=True)
        for i, r in enumerate(subset[:20]):
            p = r["p"]
            ps = f"hw={p['hw']} zw={p['zw']} ze={p['ze']} mr={p['mr']} mh={p['mh']} cd={p['cd']} omr={p['omr']} zc={p['zc']}"
            print(f"{i+1:>3} {r['total_return_pct']:>+7.2f}% {r['sharpe_ratio']:>7.2f} {r['max_drawdown_pct']:>7.2f}% {r['win_rate_pct']:>6.1f}% {r['total_trades']:>7} {r['trades_per_day']:>6.2f} {r['profit_factor']:>6.2f}  {ps}", flush=True)

    # Also show best by trades/day in 1-3 range regardless of return
    bucket = [r for r in results if 1.0 <= r["trades_per_day"] <= 3.0]
    if bucket:
        bucket.sort(key=lambda x: x["total_return_pct"], reverse=True)
        print("\n--- 1-3 trades/day: Top 20 by Return ---", flush=True)
        print(f"{'#':>3} {'Ret':>8} {'Sharpe':>8} {'MaxDD':>8} {'WR':>7} {'Trades':>7} {'T/Day':>7} {'PF':>7}  Params", flush=True)
        print("-"*130, flush=True)
        for i, r in enumerate(bucket[:20]):
            p = r["p"]
            ps = f"hw={p['hw']} zw={p['zw']} ze={p['ze']} mr={p['mr']} mh={p['mh']} cd={p['cd']} omr={p['omr']} zc={p['zc']}"
            print(f"{i+1:>3} {r['total_return_pct']:>+7.2f}% {r['sharpe_ratio']:>7.2f} {r['max_drawdown_pct']:>7.2f}% {r['win_rate_pct']:>6.1f}% {r['total_trades']:>7} {r['trades_per_day']:>6.2f} {r['profit_factor']:>6.2f}  {ps}", flush=True)

    print("\nDone!", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
