"""
Custom parameter search for Hurst-Kalman targeting 1-3 trades/day with positive returns.
"""
import sys
import os

# Force unbuffered output
os.environ['PYTHONUNBUFFERED'] = '1'

from pathlib import Path
import importlib.util

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

def _import_local_module(module_name, file_path, register_as=None):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    if register_as:
        sys.modules[register_as] = module
    spec.loader.exec_module(module)
    return module

_core = _import_local_module("_hk_core", _SCRIPT_DIR / "core.py", "strategy.bitget.hurst_kalman.core")
_configs = _import_local_module("_hk_configs", _SCRIPT_DIR / "configs.py", "strategy.bitget.hurst_kalman.configs")

HurstKalmanConfig = _core.HurstKalmanConfig
KalmanFilter1D = _core.KalmanFilter1D
calculate_hurst = _core.calculate_hurst
TradeFilterConfig = _configs.TradeFilterConfig

import asyncio
import itertools
import random
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, Optional

import numpy as np
import pandas as pd

from nexustrader.backtest import (
    BacktestConfig, CostConfig, PerformanceAnalyzer, Signal,
    VectorizedBacktest,
)
from nexustrader.backtest.data.ccxt_provider import CCXTDataProvider
from nexustrader.backtest.data.funding_rate import FundingRateProvider
from nexustrader.constants import KlineInterval

def log(msg):
    print(msg, flush=True)


class FlexibleSignalGenerator:
    def __init__(self, config, filter_config):
        self.config = config
        self.filter = filter_config

    def generate(self, data, params=None):
        hurst_window = params.get("hurst_window", self.config.hurst_window) if params else self.config.hurst_window
        zscore_window = params.get("zscore_window", self.config.zscore_window) if params else self.config.zscore_window
        zscore_entry = params.get("zscore_entry", self.config.zscore_entry) if params else self.config.zscore_entry
        mean_reversion_threshold = params.get("mean_reversion_threshold", self.config.mean_reversion_threshold) if params else self.config.mean_reversion_threshold
        kalman_R = params.get("kalman_R", self.config.kalman_R) if params else self.config.kalman_R
        kalman_Q = params.get("kalman_Q", self.config.kalman_Q) if params else self.config.kalman_Q
        min_holding_bars = params.get("min_holding_bars", self.filter.min_holding_bars) if params else self.filter.min_holding_bars
        cooldown_bars = params.get("cooldown_bars", self.filter.cooldown_bars) if params else self.filter.cooldown_bars
        only_mean_reversion = params.get("only_mean_reversion", self.filter.only_mean_reversion) if params else self.filter.only_mean_reversion
        signal_confirmation = params.get("signal_confirmation", self.filter.signal_confirmation) if params else self.filter.signal_confirmation
        zscore_close = params.get("zscore_close", 0.5)

        n = len(data)
        signals = np.zeros(n)
        prices = data["close"].values
        kalman = KalmanFilter1D(R=kalman_R, Q=kalman_Q)
        kalman_prices = []
        price_history = deque(maxlen=hurst_window + 50)
        position = 0
        entry_bar = 0
        cooldown_until = 0
        signal_count = {Signal.BUY.value: 0, Signal.SELL.value: 0}

        for i in range(n):
            price = prices[i]
            price_history.append(price)
            kalman_price = kalman.update(price)
            kalman_prices.append(kalman_price)
            if i < hurst_window + zscore_window:
                continue
            hurst = calculate_hurst(np.array(price_history), hurst_window)
            recent_prices = np.array(list(price_history)[-zscore_window:])
            recent_kalman = np.array(kalman_prices[-zscore_window:])
            deviations = recent_prices - recent_kalman
            std = np.std(deviations)
            zscore = (price - kalman_price) / std if std > 1e-10 else 0.0
            is_mean_reverting = hurst < mean_reversion_threshold

            if only_mean_reversion and not is_mean_reverting:
                if position != 0 and i - entry_bar >= min_holding_bars:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    cooldown_until = i + cooldown_bars
                continue
            if i < cooldown_until:
                continue

            raw_signal = Signal.HOLD.value
            if is_mean_reverting or not only_mean_reversion:
                if zscore < -zscore_entry:
                    raw_signal = Signal.BUY.value
                elif zscore > zscore_entry:
                    raw_signal = Signal.SELL.value
                elif abs(zscore) < zscore_close and position != 0:
                    raw_signal = Signal.CLOSE.value

            if raw_signal == Signal.BUY.value:
                signal_count[Signal.BUY.value] += 1
                signal_count[Signal.SELL.value] = 0
            elif raw_signal == Signal.SELL.value:
                signal_count[Signal.SELL.value] += 1
                signal_count[Signal.BUY.value] = 0
            else:
                signal_count[Signal.BUY.value] = 0
                signal_count[Signal.SELL.value] = 0

            confirmed_signal = Signal.HOLD.value
            if signal_count[Signal.BUY.value] >= signal_confirmation:
                confirmed_signal = Signal.BUY.value
            elif signal_count[Signal.SELL.value] >= signal_confirmation:
                confirmed_signal = Signal.SELL.value
            elif raw_signal == Signal.CLOSE.value:
                confirmed_signal = Signal.CLOSE.value

            if confirmed_signal == Signal.BUY.value:
                if position == -1 and i - entry_bar >= min_holding_bars:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    cooldown_until = i + cooldown_bars
                elif position == 0:
                    signals[i] = Signal.BUY.value
                    position = 1
                    entry_bar = i
            elif confirmed_signal == Signal.SELL.value:
                if position == 1 and i - entry_bar >= min_holding_bars:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    cooldown_until = i + cooldown_bars
                elif position == 0:
                    signals[i] = Signal.SELL.value
                    position = -1
                    entry_bar = i
            elif confirmed_signal == Signal.CLOSE.value:
                if position != 0 and i - entry_bar >= min_holding_bars:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    cooldown_until = i + cooldown_bars

        return signals


def run_backtest(data, params, funding_rates=None):
    hk_config = HurstKalmanConfig(
        hurst_window=params.get("hurst_window", 100),
        zscore_window=params.get("zscore_window", 50),
        zscore_entry=params.get("zscore_entry", 2.0),
        mean_reversion_threshold=params.get("mean_reversion_threshold", 0.45),
        kalman_R=params.get("kalman_R", 0.2),
        kalman_Q=params.get("kalman_Q", 1e-5),
        stop_loss_pct=params.get("stop_loss_pct", 0.03),
        position_size_pct=params.get("position_size_pct", 0.10),
    )
    filter_config = TradeFilterConfig(
        min_holding_bars=params.get("min_holding_bars", 4),
        cooldown_bars=params.get("cooldown_bars", 2),
        signal_confirmation=params.get("signal_confirmation", 1),
        only_mean_reversion=params.get("only_mean_reversion", True),
    )
    generator = FlexibleSignalGenerator(hk_config, filter_config)
    signals = generator.generate(data, params)

    bt_config = BacktestConfig(
        symbol="BTC/USDT:USDT",
        interval=KlineInterval.MINUTE_15,
        start_date=data.index[0].to_pydatetime(),
        end_date=data.index[-1].to_pydatetime(),
        initial_capital=10000.0,
    )
    cost_config = CostConfig(
        maker_fee=0.0002,
        taker_fee=0.0005,
        slippage_pct=0.0005,
        use_funding_rate=True,
    )
    bt = VectorizedBacktest(config=bt_config, cost_config=cost_config)
    result = bt.run(data=data, signals=signals, funding_rates=funding_rates)
    analyzer = PerformanceAnalyzer(
        equity_curve=result.equity_curve,
        trades=result.trades,
        initial_capital=bt_config.initial_capital,
    )
    metrics = analyzer.calculate_metrics()
    total_days = (data.index[-1] - data.index[0]).days
    metrics["trades_per_day"] = round(metrics["total_trades"] / max(total_days, 1), 2)
    metrics["total_days"] = total_days
    return metrics


async def main():
    log("=== Hurst-Kalman Custom Parameter Search ===")

    # Fetch data
    end_date = datetime.now()
    start_date = end_date - timedelta(days=180)
    log(f"Fetching 6m data...")
    async with CCXTDataProvider(exchange="bitget") as provider:
        data = await provider.fetch_klines(
            symbol="BTC/USDT:USDT",
            interval=KlineInterval.MINUTE_15,
            start=start_date,
            end=end_date,
        )
    log(f"Fetched {len(data)} bars")

    try:
        async with FundingRateProvider(exchange="bitget") as provider:
            funding = await provider.fetch_funding_rates(
                symbol="BTC/USDT:USDT",
                start=start_date,
                end=end_date,
            )
    except:
        funding = pd.DataFrame(columns=["funding_rate"])

    total_days = (data.index[-1] - data.index[0]).days
    btc_ret = (data['close'].iloc[-1] / data['close'].iloc[0] - 1) * 100
    log(f"Period: {data.index[0].date()} to {data.index[-1].date()} ({total_days} days)")
    log(f"BTC Return: {btc_ret:+.1f}%")

    results = []
    random.seed(42)

    # Phase 1: only_mean_reversion=False (more trades)
    log("\n--- Phase 1: only_mean_reversion=False ---")
    p1 = list(itertools.product(
        [30, 48, 60, 80],        # hurst_window
        [20, 30, 40],            # zscore_window
        [1.2, 1.5, 1.8, 2.0, 2.5],  # zscore_entry
        [0.45, 0.48, 0.50, 0.55],    # mean_reversion_threshold
        [0.1, 0.2],             # kalman_R
        [2, 3, 4],              # min_holding_bars
        [1, 2],                 # cooldown_bars
        [0.3, 0.5],             # zscore_close
    ))
    log(f"Total P1 combos: {len(p1)}")
    if len(p1) > 400:
        p1 = random.sample(p1, 400)
        log(f"Sampled to {len(p1)}")

    for idx, (hw, zw, ze, mr, kr, mh, cd, zc) in enumerate(p1):
        params = dict(hurst_window=hw, zscore_window=zw, zscore_entry=ze,
                      mean_reversion_threshold=mr, kalman_R=kr,
                      min_holding_bars=mh, cooldown_bars=cd,
                      only_mean_reversion=False, zscore_close=zc)
        try:
            m = run_backtest(data, params, funding)
            results.append({"params": params, **{k: m[k] for k in [
                "total_return_pct", "sharpe_ratio", "max_drawdown_pct",
                "win_rate_pct", "total_trades", "trades_per_day", "profit_factor"
            ]}})
        except Exception as e:
            pass
        if (idx+1) % 100 == 0:
            log(f"  P1: {idx+1}/{len(p1)} done")

    log(f"P1 done: {len(results)} results")

    # Phase 2: only_mean_reversion=True with relaxed thresholds
    log("\n--- Phase 2: only_mean_reversion=True ---")
    p2 = list(itertools.product(
        [30, 48, 60, 80],
        [20, 30, 40],
        [1.2, 1.5, 1.8, 2.0],
        [0.48, 0.50, 0.52, 0.55],
        [0.1, 0.2],
        [2, 3, 4],
        [1, 2],
        [0.3, 0.5],
    ))
    log(f"Total P2 combos: {len(p2)}")
    if len(p2) > 300:
        p2 = random.sample(p2, 300)
        log(f"Sampled to {len(p2)}")

    prev = len(results)
    for idx, (hw, zw, ze, mr, kr, mh, cd, zc) in enumerate(p2):
        params = dict(hurst_window=hw, zscore_window=zw, zscore_entry=ze,
                      mean_reversion_threshold=mr, kalman_R=kr,
                      min_holding_bars=mh, cooldown_bars=cd,
                      only_mean_reversion=True, zscore_close=zc)
        try:
            m = run_backtest(data, params, funding)
            results.append({"params": params, **{k: m[k] for k in [
                "total_return_pct", "sharpe_ratio", "max_drawdown_pct",
                "win_rate_pct", "total_trades", "trades_per_day", "profit_factor"
            ]}})
        except:
            pass
        if (idx+1) % 100 == 0:
            log(f"  P2: {idx+1}/{len(p2)} done")

    log(f"P2 done: {len(results)-prev} results (total {len(results)})")

    # Analysis
    log(f"\n{'='*120}")
    log("RESULTS ANALYSIS")
    log(f"{'='*120}")

    positive = [r for r in results if r["total_return_pct"] > 0]
    log(f"Positive return: {len(positive)}/{len(results)} ({len(positive)/len(results)*100:.1f}%)")

    # Target: 1-3 trades/day, positive return
    target = [r for r in results
              if 0.5 <= r["trades_per_day"] <= 4.0
              and r["total_return_pct"] > 0]
    log(f"Target range (0.5-4 t/day, ret>0): {len(target)}")

    # Strict target
    strict = [r for r in results
              if 0.8 <= r["trades_per_day"] <= 3.5
              and r["total_return_pct"] > 0
              and r["win_rate_pct"] > 50
              and r["max_drawdown_pct"] < 10]
    log(f"Strict (0.8-3.5 t/day, ret>0, WR>50%, DD<10%): {len(strict)}")

    # Show all with positive return sorted by Sharpe
    for label, subset in [("TARGET (0.5-4 t/day, ret>0)", target),
                           ("STRICT (0.8-3.5 t/day, ret>0, WR>50%, DD<10%)", strict),
                           ("ALL POSITIVE", positive)]:
        if not subset:
            continue
        subset.sort(key=lambda x: x["sharpe_ratio"], reverse=True)
        log(f"\n--- {label}: Top 30 by Sharpe ---")
        log(f"{'#':<4} {'Ret':>8} {'Sharpe':>8} {'MaxDD':>8} {'WR':>7} {'Trades':>7} {'T/Day':>7} {'PF':>7}  Params")
        log("-" * 140)
        for i, r in enumerate(subset[:30]):
            p = r["params"]
            ps = f"hw={p['hurst_window']} zw={p['zscore_window']} ze={p['zscore_entry']} mr={p['mean_reversion_threshold']} kR={p['kalman_R']} mh={p['min_holding_bars']} cd={p['cooldown_bars']} omr={p['only_mean_reversion']} zc={p['zscore_close']}"
            log(f"{i+1:<4} {r['total_return_pct']:>+7.2f}% {r['sharpe_ratio']:>7.2f} {r['max_drawdown_pct']:>7.2f}% {r['win_rate_pct']:>6.1f}% {r['total_trades']:>7} {r['trades_per_day']:>6.2f} {r['profit_factor']:>6.2f}  {ps}")

    # Bucket analysis
    log(f"\n{'='*80}")
    log("BUCKET ANALYSIS by trades/day")
    log(f"{'='*80}")
    buckets = [(0, 0.3), (0.3, 0.5), (0.5, 1), (1, 2), (2, 3), (3, 5), (5, 100)]
    for lo, hi in buckets:
        b = [r for r in results if lo <= r["trades_per_day"] < hi]
        if b:
            avg_ret = np.mean([r["total_return_pct"] for r in b])
            pos = len([r for r in b if r["total_return_pct"] > 0])
            avg_sharpe = np.mean([r["sharpe_ratio"] for r in b])
            log(f"  {lo:.1f}-{hi:.1f} t/day: {len(b)} configs, avg_ret={avg_ret:+.2f}%, {pos} positive ({pos/len(b)*100:.0f}%), avg_sharpe={avg_sharpe:.2f}")

    log("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
