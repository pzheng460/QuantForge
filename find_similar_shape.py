"""Find historical periods with similar PRICE SHAPE to recent BTC using DTW."""

import asyncio
from datetime import datetime

import numpy as np
import pandas as pd
from quantforge.constants import KlineInterval
from strategy.backtest.utils import fetch_data


def normalize(prices: np.ndarray) -> np.ndarray:
    """Normalize to start at 0, unit variance."""
    p = prices / prices[0] - 1  # percent change from start
    std = np.std(p)
    if std > 0:
        p = p / std
    return p


def dtw_distance(s1: np.ndarray, s2: np.ndarray) -> float:
    """Fast DTW distance (full matrix, O(n*m))."""
    n, m = len(s1), len(s2)
    dtw = np.full((n + 1, m + 1), np.inf)
    dtw[0, 0] = 0.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = (s1[i - 1] - s2[j - 1]) ** 2
            dtw[i, j] = cost + min(dtw[i - 1, j], dtw[i, j - 1], dtw[i - 1, j - 1])
    return np.sqrt(dtw[n, m]) / (n + m)


def dtw_distance_band(s1: np.ndarray, s2: np.ndarray, band: int = 50) -> float:
    """DTW with Sakoe-Chiba band constraint for speed."""
    n, m = len(s1), len(s2)
    dtw = np.full((n + 1, m + 1), np.inf)
    dtw[0, 0] = 0.0
    for i in range(1, n + 1):
        jmin = max(1, i - band)
        jmax = min(m, i + band)
        for j in range(jmin, jmax + 1):
            cost = (s1[i - 1] - s2[j - 1]) ** 2
            dtw[i, j] = cost + min(dtw[i - 1, j], dtw[i, j - 1], dtw[i - 1, j - 1])
    return np.sqrt(dtw[n, m]) / (n + m)


async def main():
    # Fetch all data
    start = datetime(2022, 1, 1)
    end = datetime(2026, 3, 5)

    data = await fetch_data(
        symbol="BTC/USDT:USDT",
        start_date=start,
        end_date=end,
        interval=KlineInterval.HOUR_1,
        exchange="bitget",
        validate=False,
    )
    print(f"Total: {len(data)} bars, {data.index[0]} → {data.index[-1]}")

    # Downsample to daily for DTW (hourly too expensive)
    daily = data["close"].resample("1D").last().dropna()
    print(f"Daily bars: {len(daily)}")

    # Current regime: recent ~6 months (shorter window = more matches)
    # Let's try multiple windows
    for window_label, recent_start in [
        ("6mo", datetime(2025, 9, 1)),
        ("3mo", datetime(2025, 12, 1)),
        ("1yr", datetime(2025, 3, 1)),
    ]:
        recent = daily[daily.index >= str(recent_start)]
        if len(recent) < 30:
            continue

        window_days = len(recent)
        ref_curve = normalize(recent.values)

        print(f"\n{'='*70}")
        print(f"SHAPE MATCHING — Window: {window_label} ({window_days} days)")
        print(f"Reference: {recent.index[0].date()} → {recent.index[-1].date()}")
        print(f"  ${recent.values[0]:.0f} → ${recent.values[-1]:.0f} ({(recent.values[-1]/recent.values[0]-1)*100:+.1f}%)")
        print(f"{'='*70}")

        # Slide over historical data
        historical = daily[daily.index < str(recent_start)]
        step = max(7, window_days // 10)  # step ~10% of window

        candidates = []
        for i in range(0, len(historical) - window_days, step):
            chunk = historical.iloc[i : i + window_days]
            if len(chunk) < window_days:
                continue
            cand_curve = normalize(chunk.values)

            # Use banded DTW for speed
            dist = dtw_distance_band(ref_curve, cand_curve, band=min(30, window_days // 5))
            candidates.append({
                "start": chunk.index[0],
                "end": chunk.index[-1],
                "start_price": chunk.values[0],
                "end_price": chunk.values[-1],
                "return_pct": (chunk.values[-1] / chunk.values[0] - 1) * 100,
                "dtw_dist": dist,
            })

        candidates.sort(key=lambda x: x["dtw_dist"])

        # Also compute correlation for top candidates
        for c in candidates[:5]:
            chunk = daily[(daily.index >= c["start"]) & (daily.index <= c["end"])]
            cand_norm = chunk.values / chunk.values[0] - 1
            ref_norm = recent.values / recent.values[0] - 1
            # Resample if lengths differ slightly
            min_len = min(len(cand_norm), len(ref_norm))
            corr = np.corrcoef(ref_norm[:min_len], cand_norm[:min_len])[0, 1]
            c["correlation"] = corr

        print(f"\nTop 5 most shape-similar periods:")
        for rank, c in enumerate(candidates[:5], 1):
            print(f"\n  #{rank} DTW={c['dtw_dist']:.4f} | Corr={c.get('correlation', 0):.3f}")
            print(f"     {c['start'].date()} → {c['end'].date()}")
            print(f"     ${c['start_price']:.0f} → ${c['end_price']:.0f} ({c['return_pct']:+.1f}%)")

        # What happened AFTER the most similar period?
        print(f"\n  📈 What happened AFTER the top match?")
        best = candidates[0]
        after_start = best["end"]
        after_data = daily[daily.index > after_start]
        for horizon in [30, 60, 90, 180]:
            if len(after_data) >= horizon:
                future_ret = (after_data.values[horizon - 1] / after_data.values[0] - 1) * 100
                print(f"     +{horizon}d: {future_ret:+.1f}%")


asyncio.run(main())
