"""Minimal search: fewer combos, save results incrementally."""
import sys, os, asyncio, json, gc
os.environ['PYTHONUNBUFFERED'] = '1'
from pathlib import Path
import importlib.util

_SD = Path(__file__).resolve().parent
_PR = _SD.parents[2]
if str(_PR) not in sys.path: sys.path.insert(0, str(_PR))

def _imp(n, fp, r=None):
    s = importlib.util.spec_from_file_location(n, fp)
    m = importlib.util.module_from_spec(s)
    sys.modules[n] = m
    if r: sys.modules[r] = m
    s.loader.exec_module(m)
    return m

_core = _imp("_c", _SD/"core.py", "strategy.live.hurst_kalman.core")
_imp("_cf", _SD/"configs.py", "strategy.live.hurst_kalman.configs")

from collections import deque
from datetime import datetime, timedelta
import numpy as np
from nexustrader.backtest import BacktestConfig, CostConfig, PerformanceAnalyzer, Signal, VectorizedBacktest
from nexustrader.backtest.data.ccxt_provider import CCXTDataProvider
from nexustrader.constants import KlineInterval

KF = _core.KalmanFilter1D; calc_h = _core.calculate_hurst

def gen(data, hw, zw, ze, mr, mh, cd, omr, zc):
    n = len(data); sig = np.zeros(n); pr = data["close"].values
    kf = KF(R=0.2, Q=5e-5); kps = []; ph = deque(maxlen=hw+50)
    pos = 0; eb = 0; cu = 0
    for i in range(n):
        p = pr[i]; ph.append(p); kp = kf.update(p); kps.append(kp)
        if i < hw + zw: continue
        h = calc_h(np.array(ph), hw)
        rp = np.array(list(ph)[-zw:]); rk = np.array(kps[-zw:])
        s = np.std(rp - rk); z = (p - kp) / s if s > 1e-10 else 0.0
        im = h < mr
        if omr and not im:
            if pos != 0 and i - eb >= mh: sig[i] = Signal.CLOSE.value; pos = 0; cu = i + cd
            continue
        if i < cu: continue
        si = 0
        if z < -ze: si = Signal.BUY.value
        elif z > ze: si = Signal.SELL.value
        elif abs(z) < zc and pos != 0: si = Signal.CLOSE.value
        if si == Signal.BUY.value:
            if pos == -1 and i - eb >= mh: sig[i] = Signal.CLOSE.value; pos = 0; cu = i + cd
            elif pos == 0: sig[i] = Signal.BUY.value; pos = 1; eb = i
        elif si == Signal.SELL.value:
            if pos == 1 and i - eb >= mh: sig[i] = Signal.CLOSE.value; pos = 0; cu = i + cd
            elif pos == 0: sig[i] = Signal.SELL.value; pos = -1; eb = i
        elif si == Signal.CLOSE.value:
            if pos != 0 and i - eb >= mh: sig[i] = Signal.CLOSE.value; pos = 0; cu = i + cd
    return sig

def run_bt(data, p):
    sig = gen(data, p['hw'], p['zw'], p['ze'], p['mr'], p['mh'], p['cd'], p['omr'], p['zc'])
    bc = BacktestConfig(symbol="BTC/USDT:USDT", interval=KlineInterval.MINUTE_15,
        start_date=data.index[0].to_pydatetime(), end_date=data.index[-1].to_pydatetime(), initial_capital=10000.0)
    cc = CostConfig(maker_fee=0.0002, taker_fee=0.0005, slippage_pct=0.0005, use_funding_rate=False)
    bt = VectorizedBacktest(config=bc, cost_config=cc)
    r = bt.run(data=data, signals=sig)
    a = PerformanceAnalyzer(equity_curve=r.equity_curve, trades=r.trades, initial_capital=10000.0)
    m = a.calculate_metrics()
    d = max((data.index[-1] - data.index[0]).days, 1)
    m["tpd"] = round(m["total_trades"] / d, 2)
    return m

async def main():
    print("=== Mini Search ===", flush=True)
    end = datetime.now(); start = end - timedelta(days=60)
    print("Fetching 2m data...", flush=True)
    async with CCXTDataProvider(exchange="bitget") as pv:
        data = await pv.fetch_klines(symbol="BTC/USDT:USDT", interval=KlineInterval.MINUTE_15, start=start, end=end)
    days = (data.index[-1] - data.index[0]).days
    print(f"Got {len(data)} bars, {days} days", flush=True)

    results = []
    combos = []
    for hw in [30, 48]:
        for zw in [20, 30]:
            for ze in [1.2, 1.5, 1.8, 2.0]:
                for mr in [0.45, 0.50, 0.55]:
                    for mh in [2, 4]:
                        for cd in [1, 2]:
                            for zc in [0.3, 0.5]:
                                for omr in [True, False]:
                                    combos.append(dict(hw=hw,zw=zw,ze=ze,mr=mr,mh=mh,cd=cd,omr=omr,zc=zc))

    print(f"Testing {len(combos)} combos...", flush=True)
    for i, p in enumerate(combos):
        try:
            m = run_bt(data, p)
            results.append({"p": p, "ret": m["total_return_pct"], "sr": m.get("sharpe_ratio",0),
                "dd": m["max_drawdown_pct"], "wr": m["win_rate_pct"], "tr": m["total_trades"],
                "tpd": m["tpd"], "pf": m.get("profit_factor",0)})
        except: pass
        if (i+1) % 50 == 0:
            print(f"  {i+1}/{len(combos)}", flush=True)
            gc.collect()

    # Save all results
    with open("/tmp/hk_results.json", "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nTotal: {len(results)} results", flush=True)

    # Show best
    t = [r for r in results if 0.5 <= r["tpd"] <= 4.0 and r["ret"] > 0]
    s = [r for r in results if 0.8 <= r["tpd"] <= 3.5 and r["ret"] > 0 and r["wr"] > 48]
    b = [r for r in results if 1.0 <= r["tpd"] <= 3.0]

    for label, sub in [("STRICT", s), ("TARGET", t), ("1-3 TPD ALL", b)]:
        if not sub: print(f"\n{label}: 0 results", flush=True); continue
        sub.sort(key=lambda x: x["ret"], reverse=True)
        print(f"\n--- {label}: Top 15 by Return ---", flush=True)
        print(f"{'#':>3} {'Ret':>8} {'SR':>7} {'DD':>7} {'WR':>6} {'Tr':>5} {'TPD':>5} {'PF':>6}  Params", flush=True)
        for i, r in enumerate(sub[:15]):
            p = r["p"]
            ps = f"hw={p['hw']} zw={p['zw']} ze={p['ze']} mr={p['mr']} mh={p['mh']} cd={p['cd']} omr={p['omr']} zc={p['zc']}"
            print(f"{i+1:>3} {r['ret']:>+7.2f}% {r['sr']:>6.2f} {r['dd']:>6.2f}% {r['wr']:>5.1f}% {r['tr']:>5} {r['tpd']:>5.2f} {r['pf']:>5.2f}  {ps}", flush=True)

    print("\nDone!", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
