"""Monte Carlo path generation: GBM and Merton Jump Diffusion."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from nexustrader.backtest.analysis.performance import infer_periods_per_year


def _build_ohlcv_from_close(
    close: np.ndarray,
    initial_close: float,
    rng: np.random.Generator,
    intrabar_vol: float,
    mean_volume: float,
    vol_std: float,
    start: pd.Timestamp,
    freq: pd.Timedelta,
) -> pd.DataFrame:
    """Construct a full OHLCV DataFrame from a close price array.

    Parameters
    ----------
    close : np.ndarray
        Synthetic close prices (length N).
    initial_close : float
        First bar's reference close for open[0].
    rng : np.random.Generator
        RNG for intrabar noise.
    intrabar_vol : float
        Typical intrabar volatility (used to generate high/low spread).
    mean_volume : float
        Mean of log(1 + volume) for volume generation.
    vol_std : float
        Std of log(1 + volume).
    start : pd.Timestamp
        Start timestamp for the index.
    freq : pd.Timedelta
        Bar frequency.
    """
    n = len(close)
    # Open: previous close (first bar uses initial_close)
    open_ = np.empty(n)
    open_[0] = initial_close
    open_[1:] = close[:-1]

    # High / Low via intrabar volatility
    spread = np.abs(rng.normal(0, intrabar_vol, size=n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    low = np.maximum(low, 1e-8)  # prevent negative/zero

    # Volume from lognormal
    log_vol = rng.normal(mean_volume, max(vol_std, 1e-6), size=n)
    volume = np.expm1(np.clip(log_vol, 0, 30))
    volume = np.maximum(volume, 0.0)

    idx = pd.date_range(start=start, periods=n, freq=freq)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


class GBMGenerator:
    """Generate synthetic OHLCV paths via Geometric Brownian Motion.

    Estimates drift (mu) and volatility (sigma) from historical close-to-close
    log-returns and generates new paths using the standard GBM formula:

        S(t+1) = S(t) * exp((mu - sigma²/2)*dt + sigma*sqrt(dt)*Z)

    Parameters
    ----------
    data : pd.DataFrame
        OHLCV DataFrame with DatetimeIndex.
    seed : int, optional
        Random seed for reproducibility.
    """

    def __init__(self, data: pd.DataFrame, seed: Optional[int] = None) -> None:
        required = {"open", "high", "low", "close", "volume"}
        missing = required - set(data.columns)
        if missing:
            raise ValueError(f"Missing columns: {missing}")

        self._data = data
        self._rng = np.random.default_rng(seed)

        close = data["close"].values.astype(np.float64)
        self._initial_close = close[0]
        log_ret = np.diff(np.log(close))

        # Estimate GBM parameters
        ppy = infer_periods_per_year(data.index)
        self._dt = 1.0 / ppy
        self._mu = float(np.mean(log_ret)) / self._dt
        self._sigma = float(np.std(log_ret, ddof=1)) / np.sqrt(self._dt)

        # Intrabar volatility (for OHLCV construction)
        hl_ratio = np.log(data["high"].values / np.maximum(data["low"].values, 1e-8))
        self._intrabar_vol = float(np.mean(hl_ratio))

        # Volume statistics
        vol_log = np.log1p(data["volume"].values.astype(np.float64))
        self._vol_mean = float(np.mean(vol_log))
        self._vol_std = float(np.std(vol_log, ddof=1))

        # Index metadata
        self._start = data.index[0]
        self._freq = data.index.to_series().diff().median()
        self._n_bars = len(data)

    def generate(
        self, n_paths: int = 100, n_bars: Optional[int] = None
    ) -> list[pd.DataFrame]:
        """Generate *n_paths* synthetic OHLCV paths.

        Parameters
        ----------
        n_paths : int
            Number of paths to generate.
        n_bars : int, optional
            Length of each path in bars.  Defaults to the length of the
            original data.
        """
        n = n_bars if n_bars is not None else self._n_bars
        dt = self._dt
        mu = self._mu
        sigma = self._sigma

        paths: list[pd.DataFrame] = []
        for _ in range(n_paths):
            z = self._rng.standard_normal(n - 1)
            log_inc = (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * z
            log_price = np.concatenate([[0.0], np.cumsum(log_inc)])
            close = self._initial_close * np.exp(log_price)

            paths.append(
                _build_ohlcv_from_close(
                    close,
                    self._initial_close,
                    self._rng,
                    self._intrabar_vol,
                    self._vol_mean,
                    self._vol_std,
                    self._start,
                    self._freq,
                )
            )
        return paths


class JumpDiffusionGenerator:
    """Generate synthetic OHLCV paths via Merton Jump Diffusion.

    Extends GBM with compound Poisson jumps to produce fatter tails:

        S(t+1) = S(t) * exp(GBM_increment + J * N(t))

    Jump parameters (lambda, mu_j, sigma_j) are estimated from outlier
    returns (|r| > 2*sigma).

    Parameters
    ----------
    data : pd.DataFrame
        OHLCV DataFrame with DatetimeIndex.
    seed : int, optional
        Random seed for reproducibility.
    """

    def __init__(self, data: pd.DataFrame, seed: Optional[int] = None) -> None:
        required = {"open", "high", "low", "close", "volume"}
        missing = required - set(data.columns)
        if missing:
            raise ValueError(f"Missing columns: {missing}")

        self._data = data
        self._rng = np.random.default_rng(seed)

        close = data["close"].values.astype(np.float64)
        self._initial_close = close[0]
        log_ret = np.diff(np.log(close))

        ppy = infer_periods_per_year(data.index)
        self._dt = 1.0 / ppy

        # Separate normal vs jump returns
        sigma_full = float(np.std(log_ret, ddof=1))
        threshold = 2.0 * sigma_full
        is_jump = np.abs(log_ret) > threshold
        jump_returns = log_ret[is_jump]
        normal_returns = log_ret[~is_jump]

        # GBM parameters from non-jump returns
        self._mu = (
            float(np.mean(normal_returns)) / self._dt
            if len(normal_returns) > 0
            else 0.0
        )
        self._sigma = (
            float(np.std(normal_returns, ddof=1)) / np.sqrt(self._dt)
            if len(normal_returns) > 1
            else sigma_full / np.sqrt(self._dt)
        )

        # Jump parameters
        n_total = len(log_ret)
        n_jumps = len(jump_returns)
        self._lam = n_jumps / max(n_total * self._dt, 1e-8)  # jumps per unit time
        self._mu_j = float(np.mean(jump_returns)) if n_jumps > 0 else 0.0
        self._sigma_j = float(np.std(jump_returns, ddof=1)) if n_jumps > 1 else 0.01

        # Intrabar / volume stats
        hl_ratio = np.log(data["high"].values / np.maximum(data["low"].values, 1e-8))
        self._intrabar_vol = float(np.mean(hl_ratio))
        vol_log = np.log1p(data["volume"].values.astype(np.float64))
        self._vol_mean = float(np.mean(vol_log))
        self._vol_std = float(np.std(vol_log, ddof=1))

        self._start = data.index[0]
        self._freq = data.index.to_series().diff().median()
        self._n_bars = len(data)

    def generate(
        self, n_paths: int = 100, n_bars: Optional[int] = None
    ) -> list[pd.DataFrame]:
        """Generate *n_paths* synthetic OHLCV paths with jump diffusion."""
        n = n_bars if n_bars is not None else self._n_bars
        dt = self._dt
        mu = self._mu
        sigma = self._sigma
        lam = self._lam
        mu_j = self._mu_j
        sigma_j = self._sigma_j

        paths: list[pd.DataFrame] = []
        for _ in range(n_paths):
            # Diffusion component
            z = self._rng.standard_normal(n - 1)
            diffusion = (mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * z

            # Jump component
            n_jumps = self._rng.poisson(lam * dt, size=n - 1)
            jump_sizes = np.zeros(n - 1)
            for i in range(n - 1):
                if n_jumps[i] > 0:
                    jumps = self._rng.normal(mu_j, sigma_j, size=n_jumps[i])
                    jump_sizes[i] = np.sum(jumps)

            log_inc = diffusion + jump_sizes
            log_price = np.concatenate([[0.0], np.cumsum(log_inc)])
            close = self._initial_close * np.exp(log_price)

            paths.append(
                _build_ohlcv_from_close(
                    close,
                    self._initial_close,
                    self._rng,
                    self._intrabar_vol,
                    self._vol_mean,
                    self._vol_std,
                    self._start,
                    self._freq,
                )
            )
        return paths
