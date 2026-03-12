"""Manual Hurst-Kalman heatmap on 1h data for speed."""
import asyncio
import itertools
from datetime import datetime
import numpy as np
from quantforge.constants import KlineInterval
from strategy.backtest.utils import fetch_data
from strategy.backtest.runner import BacktestRunner
from strategy.strategies.hurst_kalman.core import HurstKalmanConfig
from strategy.strategies.hurst_kalman.registration import HurstKalmanFilterConfig

ZSCORE_ENTRIES = [1.0, 1.5, 2.0, 2.5, 3.0]
HURST_WINDOWS = [30, 50, 70, 100]
MR_THRESHOLDS = [0.45, 0.50, 0.55]
KALMAN_RS = [0.1, 0.2, 0.5]
STOP_LOSSES = [0.03, 0.05, 0.07]

async def main():
    data = await fetch_data(
        symbol="BTC/USDT:USDT",
        start_date=datetime(2022, 1, 1),
        end_date=datetime(2026, 3, 5),
        interval=KlineInterval.HOUR_1,
        exchange="bitget",
        validate=False,
    )
    print(f"Data: {len(data)} bars, {data.index[0]} → {data.index[-1]}")

    runner = BacktestRunner(strategy_name="hurst_kalman", exchange="bitget", leverage=5)
    
    results = []
    combos = list(itertools.product(ZSCORE_ENTRIES, HURST_WINDOWS, MR_THRESHOLDS, KALMAN_RS, STOP_LOSSES))
    print(f"Total combinations: {len(combos)}")
    
    for i, (zs, hw, mrt, kr, sl) in enumerate(combos):
        if (i+1) % 20 == 0:
            print(f"  [{i+1}/{len(combos)}]...")
        
        cfg = HurstKalmanConfig(
            hurst_window=hw,
            zscore_entry=zs,
            mean_reversion_threshold=mrt,
            kalman_R=kr,
            stop_loss_pct=sl,
        )
        filt = HurstKalmanFilterConfig(
            min_holding_bars=max(2, hw // 12),
            cooldown_bars=max(1, hw // 24),
            only_mean_reversion=True,
        )
        
        try:
            result = runner.run_single(data=data, config_override=cfg, filter_override=filt)
            metrics = result.get("metrics", result)
            total_ret = metrics.get("total_return_pct", 0)
            sharpe = metrics.get("sharpe_ratio", 0)
            trades = metrics.get("total_trades", 0)
            maxdd = metrics.get("max_drawdown_pct", 0)
            winrate = metrics.get("win_rate_pct", 0)
            pf = metrics.get("profit_factor", 0)
            
            results.append({
                "zscore_entry": zs, "hurst_window": hw,
                "mr_threshold": mrt, "kalman_R": kr, "stop_loss": sl,
                "return": total_ret, "sharpe": sharpe, "trades": trades,
                "maxdd": maxdd, "winrate": winrate, "pf": pf,
            })
        except Exception as e:
            pass
    
    # Sort by Sharpe
    results.sort(key=lambda x: x["sharpe"], reverse=True)
    
    print(f"\n{'='*80}")
    print(f"TOP 15 PARAMETER COMBINATIONS (by Sharpe)")
    print(f"{'='*80}")
    for rank, r in enumerate(results[:15], 1):
        print(f"\n#{rank} Sharpe={r['sharpe']:.2f} | Return={r['return']:+.1f}% | MaxDD={r['maxdd']:.1f}% | Trades={r['trades']} | WR={r['winrate']:.0f}%")
        print(f"   zscore={r['zscore_entry']} hw={r['hurst_window']} mr_thresh={r['mr_threshold']} kalman_R={r['kalman_R']} stop={r['stop_loss']}")
    
    print(f"\n{'='*80}")
    print(f"BOTTOM 5 (worst)")
    print(f"{'='*80}")
    for r in results[-5:]:
        print(f"  Sharpe={r['sharpe']:.2f} | Return={r['return']:+.1f}% | Trades={r['trades']}")
        print(f"   zscore={r['zscore_entry']} hw={r['hurst_window']} mr_thresh={r['mr_threshold']}")
    
    # Stats
    positive = [r for r in results if r["return"] > 0]
    print(f"\nPositive return: {len(positive)}/{len(results)} ({len(positive)/len(results)*100:.0f}%)")
    sharpes = [r["sharpe"] for r in results]
    print(f"Sharpe range: {min(sharpes):.2f} → {max(sharpes):.2f}")

asyncio.run(main())
