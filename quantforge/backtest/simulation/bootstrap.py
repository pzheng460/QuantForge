"""Block bootstrap resampling for OHLCV data."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


class BlockBootstrap:
    """Generate synthetic OHLCV paths via block bootstrap on log-returns.

    Splits historical log-returns into non-overlapping blocks, resamples
    blocks with replacement, and reconstructs prices from the initial
    price + cumulative returns.  This preserves short-range serial
    dependence (volatility clustering, fat tails) while producing
    genuinely new price trajectories.

    Parameters
    ----------
    data : pd.DataFrame
        OHLCV DataFrame with DatetimeIndex.
    block_size : int
        Number of bars per block (default 24 ≈ one day of hourly bars).
    seed : int, optional
        Random seed for reproducibility.
    """

    def __init__(
        self,
        data: pd.DataFrame,
        block_size: int = 24,
        seed: Optional[int] = None,
    ) -> None:
        if block_size < 1:
            raise ValueError("block_size must be >= 1")
        required = {"open", "high", "low", "close", "volume"}
        missing = required - set(data.columns)
        if missing:
            raise ValueError(f"Missing columns: {missing}")
        if len(data) < block_size:
            raise ValueError("data length must be >= block_size")

        self._data = data
        self._block_size = block_size
        self._rng = np.random.default_rng(seed)

        # Pre-compute log-returns for close prices
        close = data["close"].values.astype(np.float64)
        self._initial_close = close[0]
        self._log_returns_close = np.diff(np.log(close))

        # Pre-compute log-ratios for OHLV relative to close
        self._log_ratio_open = np.log(data["open"].values[1:] / close[:-1])
        self._log_ratio_high = np.log(data["high"].values[1:] / close[1:])
        self._log_ratio_low = np.log(data["low"].values[1:] / close[1:])
        self._log_volume = np.log1p(data["volume"].values[1:].astype(np.float64))

        # Number of complete blocks
        n_returns = len(self._log_returns_close)
        self._n_blocks = n_returns // block_size

    def generate(self, n_paths: int = 100) -> list[pd.DataFrame]:
        """Generate *n_paths* synthetic OHLCV DataFrames.

        Each path has the same number of bars as the original data.
        """
        n_bars_target = len(self._data)
        n_returns_target = n_bars_target - 1
        bs = self._block_size

        paths: list[pd.DataFrame] = []
        for _ in range(n_paths):
            # How many blocks we need to cover n_returns_target returns
            n_blocks_needed = int(np.ceil(n_returns_target / bs))
            chosen = self._rng.integers(0, self._n_blocks, size=n_blocks_needed)

            # Concatenate chosen blocks of log-returns
            ret_blocks = []
            open_blocks = []
            high_blocks = []
            low_blocks = []
            vol_blocks = []
            for idx in chosen:
                s = idx * bs
                e = s + bs
                ret_blocks.append(self._log_returns_close[s:e])
                open_blocks.append(self._log_ratio_open[s:e])
                high_blocks.append(self._log_ratio_high[s:e])
                low_blocks.append(self._log_ratio_low[s:e])
                vol_blocks.append(self._log_volume[s:e])

            log_ret = np.concatenate(ret_blocks)[:n_returns_target]
            log_open = np.concatenate(open_blocks)[:n_returns_target]
            log_high = np.concatenate(high_blocks)[:n_returns_target]
            log_low = np.concatenate(low_blocks)[:n_returns_target]
            log_vol = np.concatenate(vol_blocks)[:n_returns_target]

            # Reconstruct close prices
            cum_ret = np.concatenate([[0.0], np.cumsum(log_ret)])
            close = self._initial_close * np.exp(cum_ret)

            # Reconstruct OHLV from ratios
            open_ = np.empty(n_bars_target)
            open_[0] = close[0]
            open_[1:] = close[:-1] * np.exp(log_open)

            high = close.copy()
            high[1:] = close[1:] * np.exp(log_high)
            high[0] = max(open_[0], close[0])

            low = close.copy()
            low[1:] = close[1:] * np.exp(log_low)
            low[0] = min(open_[0], close[0])

            # Ensure OHLC consistency
            high = np.maximum(high, np.maximum(open_, close))
            low = np.minimum(low, np.minimum(open_, close))

            volume = np.empty(n_bars_target)
            volume[0] = self._data["volume"].iloc[0]
            volume[1:] = np.expm1(log_vol)
            volume = np.maximum(volume, 0.0)

            # Build synthetic DatetimeIndex with same frequency
            freq = self._data.index.to_series().diff().median()
            idx = pd.date_range(
                start=self._data.index[0],
                periods=n_bars_target,
                freq=freq,
            )

            paths.append(
                pd.DataFrame(
                    {
                        "open": open_,
                        "high": high,
                        "low": low,
                        "close": close,
                        "volume": volume,
                    },
                    index=idx,
                )
            )

        return paths
