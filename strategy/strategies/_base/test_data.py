"""Synthetic OHLCV data generators for parity tests.

Centralised here so both registration.py (for ParityTestConfig) and
test/indicators/parity_factory.py can import them without creating a
production → test dependency.
"""

import numpy as np
import pandas as pd


def generate_trending_ohlcv(n: int = 2000, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data with trending/ranging regimes."""
    rng = np.random.RandomState(seed)
    close = np.zeros(n)
    close[0] = 100.0
    for i in range(1, n):
        regime = (i // 200) % 3
        if regime == 0:
            drift = 0.001
        elif regime == 1:
            drift = -0.001
        else:
            drift = 0.0
        close[i] = close[i - 1] * (1 + drift + rng.normal(0, 0.015))

    spread = rng.uniform(0.005, 0.02, n) * close
    high = close + spread * rng.uniform(0.3, 1.0, n)
    low = close - spread * rng.uniform(0.3, 1.0, n)
    volume = rng.uniform(500, 5000, n) * (1 + rng.uniform(0, 2, n))

    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.002, n)),
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def generate_mean_reverting_ohlcv(n: int = 2000, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data with mean-reverting characteristics."""
    rng = np.random.RandomState(seed)
    close = np.zeros(n)
    close[0] = 100.0
    mean_level = 100.0

    for i in range(1, n):
        close[i] = (
            close[i - 1]
            + 0.02 * (mean_level - close[i - 1])
            + rng.normal(0, 1.2)
        )
        if i % 500 == 0:
            mean_level += rng.normal(0, 5)

    spread = rng.uniform(0.005, 0.02, n) * np.abs(close)
    high = close + spread * rng.uniform(0.3, 1.0, n)
    low = close - spread * rng.uniform(0.3, 1.0, n)
    volume = rng.uniform(500, 5000, n) * (1 + rng.uniform(0, 2, n))

    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.002, n)),
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def generate_strong_mean_reverting_ohlcv(n: int = 2000, seed: int = 42) -> pd.DataFrame:
    """Generate strongly mean-reverting OHLCV data for Hurst-Kalman testing."""
    rng = np.random.RandomState(seed)
    close = np.zeros(n)
    close[0] = 100.0
    mean_level = 100.0
    for i in range(1, n):
        close[i] = (
            close[i - 1] + 0.03 * (mean_level - close[i - 1]) + rng.normal(0, 1.0)
        )
        if i % 400 == 0:
            mean_level += rng.normal(0, 3)
    spread = rng.uniform(0.005, 0.02, n) * np.abs(close)
    high = close + spread * rng.uniform(0.3, 1.0, n)
    low = close - spread * rng.uniform(0.3, 1.0, n)
    volume = rng.uniform(500, 5000, n) * (1 + rng.uniform(0, 2, n))
    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.002, n)),
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def generate_dual_regime_ohlcv(n: int = 2000, seed: int = 42) -> pd.DataFrame:
    """Generate OHLCV data with alternating trending and ranging periods."""
    rng = np.random.RandomState(seed)
    close = np.zeros(n)
    close[0] = 100.0

    for i in range(1, n):
        phase = (i // 300) % 4
        if phase == 0:
            drift, vol = 0.002, 0.01
        elif phase == 1:
            drift, vol = 0.0, 0.02
        elif phase == 2:
            drift, vol = -0.002, 0.01
        else:
            drift, vol = 0.0, 0.015
        close[i] = close[i - 1] * (1 + drift + rng.normal(0, vol))

    spread = rng.uniform(0.005, 0.02, n) * close
    high = close + spread * rng.uniform(0.3, 1.0, n)
    low = close - spread * rng.uniform(0.3, 1.0, n)
    volume = rng.uniform(500, 5000, n) * (1 + rng.uniform(0, 2, n))

    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.002, n)),
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def generate_range_bound_ohlcv(n: int = 2000, seed: int = 42) -> pd.DataFrame:
    """Generate range-bound OHLCV data with occasional breakouts."""
    rng = np.random.RandomState(seed)
    close = np.zeros(n)
    close[0] = 100.0
    center = 100.0

    for i in range(1, n):
        mean_revert = 0.03 * (center - close[i - 1])
        close[i] = close[i - 1] + mean_revert + rng.normal(0, 0.8)
        if i % 400 == 0:
            center += rng.choice([-3, 3])

    spread = rng.uniform(0.3, 1.0, n)
    high = close + spread
    low = close - spread
    volume = rng.uniform(500, 5000, n) * (1 + rng.uniform(0, 2, n))

    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.002, n)),
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def generate_vwap_ohlcv(n: int = 2000, seed: int = 42) -> pd.DataFrame:
    """Generate mean-reverting OHLCV data with DatetimeIndex for VWAP testing."""
    rng = np.random.RandomState(seed)
    close = np.zeros(n)
    close[0] = 100.0
    mean_level = 100.0

    for i in range(1, n):
        close[i] = (
            close[i - 1]
            + 0.02 * (mean_level - close[i - 1])
            + rng.normal(0, 1.2)
        )
        if i % 500 == 0:
            mean_level += rng.normal(0, 5)

    spread = rng.uniform(0.005, 0.02, n) * np.abs(close)
    high = close + spread * rng.uniform(0.3, 1.0, n)
    low = close - spread * rng.uniform(0.3, 1.0, n)
    volume = rng.uniform(500, 5000, n) * (1 + rng.uniform(0, 2, n))
    timestamps = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")

    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.002, n)),
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=timestamps,
    )


def generate_funding_ohlcv(n: int = 500, seed: int = 42) -> pd.DataFrame:
    """Generate hourly OHLCV data for funding rate testing."""
    rng = np.random.RandomState(seed)
    close = np.zeros(n)
    close[0] = 50000.0

    for i in range(1, n):
        close[i] = close[i - 1] * (1 + rng.normal(0, 0.005))

    spread = rng.uniform(10, 100, n)
    high = close + spread * rng.uniform(0.3, 1.0, n)
    low = close - spread * rng.uniform(0.3, 1.0, n)
    volume = rng.uniform(100, 1000, n) * (1 + rng.uniform(0, 2, n))
    timestamps = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")

    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.001, n)),
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=timestamps,
    )


def generate_funding_rates(
    timestamps: pd.DatetimeIndex, seed: int = 42
) -> pd.DataFrame:
    """Generate synthetic funding rate data (every 8 hours)."""
    rng = np.random.RandomState(seed)
    fr_times = pd.date_range(timestamps[0], timestamps[-1], freq="8h", tz="UTC")
    rates = rng.uniform(0.0001, 0.002, len(fr_times))
    return pd.DataFrame({"funding_rate": rates}, index=fr_times)
