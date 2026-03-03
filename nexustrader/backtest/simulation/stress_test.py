"""Stress testing via importance sampling and tail scenario generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class StressTestResult:
    """Container for stress test outputs.

    Attributes
    ----------
    paths : list[pd.DataFrame]
        Synthetic OHLCV paths biased toward the target scenario.
    weights : np.ndarray
        Importance weights for unbiased estimation under the original
        distribution.  ``weights[i]`` = p_original(path_i) / p_biased(path_i).
    tail_probability : float
        Estimated probability of the scenario under the original distribution.
    scenario_description : str
        Human-readable description of the scenario.
    """

    paths: list[pd.DataFrame]
    weights: np.ndarray
    tail_probability: float
    scenario_description: str


class StressTestGenerator:
    """Generate stress-test scenarios via importance sampling.

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
        self._log_returns = np.diff(np.log(close))
        self._mu = float(np.mean(self._log_returns))
        self._sigma = float(np.std(self._log_returns, ddof=1))
        self._n_bars = len(data)

        self._start = data.index[0]
        self._freq = data.index.to_series().diff().median()

        # Volume stats for reconstruction
        vol_log = np.log1p(data["volume"].values.astype(np.float64))
        self._vol_mean = float(np.mean(vol_log))
        self._vol_std = float(np.std(vol_log, ddof=1))

    def _reconstruct_ohlcv(self, log_returns: np.ndarray) -> pd.DataFrame:
        """Build OHLCV DataFrame from log-returns."""
        n = len(log_returns) + 1
        cum = np.concatenate([[0.0], np.cumsum(log_returns)])
        close = self._initial_close * np.exp(cum)

        open_ = np.empty(n)
        open_[0] = close[0]
        open_[1:] = close[:-1]

        # Intrabar spread from absolute returns
        spread = np.abs(np.concatenate([[0.0], log_returns])) * close * 0.5
        high = np.maximum(open_, close) + spread
        low = np.minimum(open_, close) - spread
        low = np.maximum(low, 1e-8)

        log_vol = self._rng.normal(self._vol_mean, max(self._vol_std, 1e-6), size=n)
        volume = np.expm1(np.clip(log_vol, 0, 30))
        volume = np.maximum(volume, 0.0)

        idx = pd.date_range(start=self._start, periods=n, freq=self._freq)
        return pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=idx,
        )

    def generate_crash_scenarios(
        self,
        n_paths: int = 100,
        crash_pct: float = -0.10,
        crash_window: int = 24,
    ) -> StressTestResult:
        """Generate paths biased toward large drawdowns.

        The return distribution is shifted downward so that a cumulative
        drop of *crash_pct* over *crash_window* bars is likely.  Importance
        weights correct for the bias.

        Parameters
        ----------
        n_paths : int
            Number of scenarios.
        crash_pct : float
            Target cumulative return (e.g. -0.10 for a 10 % crash).
        crash_window : int
            Number of bars over which the crash unfolds.
        """
        # Target per-bar mean to achieve crash_pct over crash_window
        target_per_bar = np.log(1 + crash_pct) / crash_window
        shift = target_per_bar - self._mu

        n_ret = self._n_bars - 1
        paths: list[pd.DataFrame] = []
        log_weights = np.zeros(n_paths)

        for i in range(n_paths):
            returns = self._rng.normal(self._mu + shift, self._sigma, size=n_ret)
            # Importance weight: log(p_orig) - log(p_shifted)
            # = -0.5*((r-mu)/s)^2 + 0.5*((r-mu-shift)/s)^2
            # = shift*(r - mu - shift/2) / s^2
            log_w = np.sum(shift * (returns - self._mu - shift / 2) / self._sigma**2)
            log_weights[i] = -log_w  # orig / shifted ratio
            paths.append(self._reconstruct_ohlcv(returns))

        # Stabilise weights
        weights = np.exp(log_weights - np.max(log_weights))
        weights *= n_paths / np.sum(weights)

        # Tail probability: fraction of paths whose cumulative return <= crash_pct
        cum_rets = np.array(
            [(p["close"].iloc[-1] / p["close"].iloc[0]) - 1 for p in paths]
        )
        hit = cum_rets <= crash_pct
        tail_prob = float(np.mean(weights[hit])) / n_paths if np.any(hit) else 0.0
        tail_prob = np.clip(tail_prob, 0.0, 1.0)

        return StressTestResult(
            paths=paths,
            weights=weights,
            tail_probability=tail_prob,
            scenario_description=(
                f"Crash scenario: {crash_pct * 100:.1f}% drop over {crash_window} bars"
            ),
        )

    def generate_spike_scenarios(
        self,
        n_paths: int = 100,
        spike_pct: float = 0.10,
        spike_window: int = 24,
    ) -> StressTestResult:
        """Generate paths biased toward large upward spikes.

        Same importance-sampling approach as crash scenarios but with an
        upward shift.
        """
        target_per_bar = np.log(1 + spike_pct) / spike_window
        shift = target_per_bar - self._mu

        n_ret = self._n_bars - 1
        paths: list[pd.DataFrame] = []
        log_weights = np.zeros(n_paths)

        for i in range(n_paths):
            returns = self._rng.normal(self._mu + shift, self._sigma, size=n_ret)
            log_w = np.sum(shift * (returns - self._mu - shift / 2) / self._sigma**2)
            log_weights[i] = -log_w
            paths.append(self._reconstruct_ohlcv(returns))

        weights = np.exp(log_weights - np.max(log_weights))
        weights *= n_paths / np.sum(weights)

        cum_rets = np.array(
            [(p["close"].iloc[-1] / p["close"].iloc[0]) - 1 for p in paths]
        )
        hit = cum_rets >= spike_pct
        tail_prob = float(np.mean(weights[hit])) / n_paths if np.any(hit) else 0.0
        tail_prob = np.clip(tail_prob, 0.0, 1.0)

        return StressTestResult(
            paths=paths,
            weights=weights,
            tail_probability=tail_prob,
            scenario_description=(
                f"Spike scenario: +{spike_pct * 100:.1f}% rise over {spike_window} bars"
            ),
        )

    def generate_volatility_scenarios(
        self,
        n_paths: int = 100,
        vol_multiplier: float = 3.0,
    ) -> StressTestResult:
        """Generate paths with amplified volatility.

        Historical returns are scaled by *vol_multiplier* to simulate
        a regime shift to higher volatility.  Importance weights are
        computed as the ratio of the original to the inflated density.
        """
        n_ret = self._n_bars - 1
        sigma_new = self._sigma * vol_multiplier

        paths: list[pd.DataFrame] = []
        log_weights = np.zeros(n_paths)

        for i in range(n_paths):
            returns = self._rng.normal(self._mu, sigma_new, size=n_ret)
            # log(p_orig / p_new) for each return
            # = 0.5*( (r-mu)^2 * (1/s_new^2 - 1/s_orig^2) ) + log(s_new/s_orig)
            diff_inv_var = 1.0 / sigma_new**2 - 1.0 / self._sigma**2
            log_w = 0.5 * np.sum((returns - self._mu) ** 2 * diff_inv_var)
            log_w += n_ret * np.log(sigma_new / self._sigma)
            log_weights[i] = -log_w  # orig / new
            paths.append(self._reconstruct_ohlcv(returns))

        weights = np.exp(log_weights - np.max(log_weights))
        weights *= n_paths / np.sum(weights)

        tail_prob_estimate = float(np.mean(weights)) / n_paths
        tail_prob_estimate = np.clip(tail_prob_estimate, 0.0, 1.0)

        return StressTestResult(
            paths=paths,
            weights=weights,
            tail_probability=tail_prob_estimate,
            scenario_description=(
                f"Volatility scenario: {vol_multiplier:.1f}x normal volatility"
            ),
        )
