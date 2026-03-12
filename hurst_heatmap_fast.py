"""Fast Hurst-Kalman heatmap - focus on conservative (few trades) params."""
import asyncio, itertools, sys
from datetime import datetime
import numpy as np
from quantforge.constants import KlineInterval
from strategy.backtest.utils import fetch_data
from strategy.backtest.runner import BacktestRunner
from strategy.strategies.hurst_kalman.core import HurstKalmanConfig
from strategy.strategies.hurst_kalman.registration import HurstKalmanFilterConfig

# Focus on conservative params (high zscore, large window)
PARAMS = list(itertools.product(
    [2.0, 2.5, 3.0, 3.5, 4.0],  # zscore_entry
    [50, 70, 100],                # hurst_window
    [0.45, 0.50, 0.55],          # mr_threshold
    [0.1, 0.2, 0.5],             # kalman_R
    [0.05, 0.07, 0.10],          # stop_loss
))

async def main():
    data = await fetch_data(
        symbol="BTC/USDT:USDT",
        start_date=datetime(2022, 1, 1),
        end_date=datetime(2026, 3, 5),
        interval=KlineInterval.HOUR_1,
        exchange="bitget",
        validate=False,
    )
    print(f"Data: {len(data)} bars | Combos: {len(PARAMS)}", flush=True)

    runner = BacktestRunner(strategy_name="hurst_kalman", exchange="bitget", leverage=5)
    
    results = []
    for i, (zs, hw, mrt, kr, sl) in enumerate(PARAMS):
        cfg = HurstKalmanConfig(
            hurst_window=hw, zscore_entry=zs, mean_reversion_threshold=mrt,
            kalman_R=kr, stop_loss_pct=sl,
        )
        filt = HurstKalmanFilterConfig(
            min_holding_bars=max(2, hw // 12),
            cooldown_bars=max(1, hw // 24),
            only_mean_reversion=True,
        )
        
        # Suppress print
        import io, contextlib
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            try:
                result = runner.run_single(data=data, config_override=cfg, filter_override=filt)
            except:
                continue
        
        m = result
        results.append({
            "zs": zs, "hw": hw, "mrt": mrt, "kr": kr, "sl": sl,
            "ret": m.get("total_return_pct", 0), "sharpe": m.get("sharpe_ratio", 0),
            "trades": m.get("total_trades", 0), "maxdd": m.get("max_drawdown_pct", 0),
            "wr": m.get("win_rate_pct", 0), "pf": m.get("profit_factor", 0),
        })
        
        if (i+1) % 30 == 0:
            print(f"  [{i+1}/{len(PARAMS)}]", flush=True)
    
    results.sort(key=lambda x: x["sharpe"], reverse=True)
    
    print(f"\n{'='*80}")
    print(f"TOP 15 (by Sharpe) — {len(results)} combos tested")
    print(f"{'='*80}")
    for rank, r in enumerate(results[:15], 1):
        print(f"#{rank} Sharpe={r['sharpe']:.2f} | Ret={r['ret']:+.1f}% | DD={r['maxdd']:.1f}% | Trades={r['trades']} | WR={r['wr']:.0f}% | PF={r['pf']:.2f}")
        print(f"   zs={r['zs']} hw={r['hw']} mrt={r['mrt']} kR={r['kr']} sl={r['sl']}")
    
    pos = sum(1 for r in results if r["ret"] > 0)
    print(f"\nPositive: {pos}/{len(results)} ({pos/len(results)*100:.0f}%)")

asyncio.run(main())
