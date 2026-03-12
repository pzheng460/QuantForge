"""Find historical periods similar to recent BTC market regime."""

import asyncio
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from quantforge.constants import KlineInterval
from strategy.backtest.utils import fetch_data


def calc_regime_features(df: pd.DataFrame, label: str = "") -> dict:
    """Calculate regime features for a period."""
    close = df["close"].values
    ret = np.diff(np.log(close))
    
    total_return = (close[-1] / close[0] - 1) * 100
    volatility = np.std(ret) * np.sqrt(24 * 365) * 100  # annualized vol %
    max_dd = 0
    peak = close[0]
    for p in close:
        peak = max(peak, p)
        dd = (peak - p) / peak
        max_dd = max(max_dd, dd)
    max_dd *= 100
    
    # Trend strength: linear regression R²
    x = np.arange(len(close))
    corr = np.corrcoef(x, close)[0, 1]
    r2 = corr ** 2
    
    # Choppiness: count direction changes in daily returns
    daily = df["close"].resample("1D").last().dropna()
    daily_ret = daily.pct_change().dropna().values
    direction_changes = np.sum(np.diff(np.sign(daily_ret)) != 0) / len(daily_ret) if len(daily_ret) > 1 else 0
    
    return {
        "label": label,
        "start": df.index[0].strftime("%Y-%m-%d"),
        "end": df.index[-1].strftime("%Y-%m-%d"),
        "total_return_pct": total_return,
        "annualized_vol_pct": volatility,
        "max_drawdown_pct": max_dd,
        "trend_r2": r2,
        "choppiness": direction_changes,
        "start_price": close[0],
        "end_price": close[-1],
    }


async def main():
    # Fetch available data (Bitget has data from ~2022)
    start = datetime(2022, 1, 1)
    end = datetime(2026, 3, 5)
    
    data = await fetch_data(
        symbol="BTC/USDT:USDT",
        start_date=start,
        end_date=end,
        interval=KlineInterval.HOUR_1,
        exchange="bitget",
    )
    print(f"Total data: {len(data)} bars, {data.index[0]} → {data.index[-1]}")
    
    # Current regime: last ~6 months (roughly 2025-09 to 2026-03)
    # Actually let's use "past 1 year" as mentioned: ~$89k → ~$71k
    recent_start = datetime(2025, 3, 1)
    recent_end = data.index[-1].to_pydatetime()
    recent = data[data.index >= str(recent_start)]
    
    if len(recent) < 100:
        # Try shorter recent window
        recent_start = datetime(2025, 6, 1)
        recent = data[data.index >= str(recent_start)]
    
    print(f"\n=== CURRENT REGIME ({recent.index[0].date()} → {recent.index[-1].date()}) ===")
    current = calc_regime_features(recent, "Current")
    for k, v in current.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.2f}")
        else:
            print(f"  {k}: {v}")
    
    # Scan historical periods with sliding window (same length as current)
    window_days = (recent.index[-1] - recent.index[0]).days
    window_bars = len(recent)
    print(f"\nWindow: {window_days} days ({window_bars} bars)")
    
    # Calculate features for all sliding windows (step = 30 days)
    step_bars = 24 * 30  # ~30 days
    periods = []
    
    historical = data[data.index < str(recent_start)]
    
    for i in range(0, len(historical) - window_bars, step_bars):
        chunk = historical.iloc[i:i + window_bars]
        if len(chunk) < window_bars * 0.9:
            continue
        feat = calc_regime_features(chunk, f"Period_{i}")
        periods.append(feat)
    
    # Similarity score: weighted distance
    def similarity(p):
        d_ret = (p["total_return_pct"] - current["total_return_pct"]) / max(abs(current["total_return_pct"]), 1)
        d_vol = (p["annualized_vol_pct"] - current["annualized_vol_pct"]) / current["annualized_vol_pct"]
        d_dd = (p["max_drawdown_pct"] - current["max_drawdown_pct"]) / max(current["max_drawdown_pct"], 1)
        d_r2 = (p["trend_r2"] - current["trend_r2"])
        d_chop = (p["choppiness"] - current["choppiness"])
        
        return (d_ret**2 * 3 + d_vol**2 * 2 + d_dd**2 * 2 + d_r2**2 * 1 + d_chop**2 * 1) ** 0.5
    
    for p in periods:
        p["similarity"] = similarity(p)
    
    periods.sort(key=lambda x: x["similarity"])
    
    print(f"\n{'='*80}")
    print(f"TOP 5 MOST SIMILAR HISTORICAL PERIODS (to current regime)")
    print(f"{'='*80}")
    
    for rank, p in enumerate(periods[:5], 1):
        print(f"\n--- #{rank} Similarity Score: {p['similarity']:.3f} ---")
        print(f"  Period: {p['start']} → {p['end']}")
        print(f"  Price:  ${p['start_price']:.0f} → ${p['end_price']:.0f}")
        print(f"  Return: {p['total_return_pct']:+.1f}%")
        print(f"  Vol:    {p['annualized_vol_pct']:.1f}%")
        print(f"  MaxDD:  {p['max_drawdown_pct']:.1f}%")
        print(f"  Trend:  R²={p['trend_r2']:.3f}")
        print(f"  Chop:   {p['choppiness']:.3f}")
    
    # Also show current for comparison
    print(f"\n--- CURRENT ---")
    print(f"  Period: {current['start']} → {current['end']}")
    print(f"  Price:  ${current['start_price']:.0f} → ${current['end_price']:.0f}")
    print(f"  Return: {current['total_return_pct']:+.1f}%")
    print(f"  Vol:    {current['annualized_vol_pct']:.1f}%")
    print(f"  MaxDD:  {current['max_drawdown_pct']:.1f}%")
    print(f"  Trend:  R²={current['trend_r2']:.3f}")
    print(f"  Chop:   {current['choppiness']:.3f}")


asyncio.run(main())
