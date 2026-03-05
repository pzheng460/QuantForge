"""
Grid search optimization for backtest parameters.

Supports exhaustive search over parameter combinations
with parallel execution support.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from itertools import product
from typing import Callable, Dict, Iterator, List, Optional, Tuple

import numpy as np
import pandas as pd

from nexustrader.backtest.engine.cost_model import CostConfig
from nexustrader.backtest.engine.vectorized import VectorizedBacktest
from nexustrader.backtest.result import BacktestConfig


@dataclass
class OptimizationResult:
    """Result from a single parameter optimization run."""

    params: Dict
    metrics: Dict[str, float]
    equity_curve: Optional[pd.Series] = None


class ParameterGrid:
    """
    Parameter grid for optimization.

    Generates all combinations of parameter values.
    """

    def __init__(self, **kwargs):
        """
        Initialize parameter grid.

        Args:
            **kwargs: Parameter names and their values (list or range)
        """
        self.params = {}
        for name, values in kwargs.items():
            # Convert range to list
            if isinstance(values, range):
                self.params[name] = list(values)
            else:
                self.params[name] = list(values)

    def __iter__(self) -> Iterator[Dict]:
        """Iterate over all parameter combinations."""
        if not self.params:
            return

        keys = list(self.params.keys())
        values = [self.params[k] for k in keys]

        for combination in product(*values):
            yield dict(zip(keys, combination))

    def __len__(self) -> int:
        """Get total number of combinations."""
        if not self.params:
            return 0

        total = 1
        for values in self.params.values():
            total *= len(values)
        return total


class GridSearchOptimizer:
    """
    Grid search optimizer for backtesting.

    Performs exhaustive search over parameter combinations.
    """

    def __init__(
        self,
        data: pd.DataFrame,
        config: BacktestConfig,
        signal_generator: Callable[[pd.DataFrame, Dict], np.ndarray],
        cost_config: Optional[CostConfig] = None,
        position_size_pct: float = 1.0,
        n_jobs: int = 1,
        funding_rates: Optional[pd.DataFrame] = None,
    ):
        """
        Initialize grid search optimizer.

        Args:
            data: OHLCV DataFrame
            config: Backtest configuration
            signal_generator: Function that generates signals from data and params
            cost_config: Trading cost configuration
            position_size_pct: Fraction of capital to use per trade
            n_jobs: Number of parallel workers for backtest runs.
                1  = sequential (default).
                -1 = use all CPU cores.
                Signal generation is always sequential because the shared
                signal_fn closure may not be thread-safe.
            funding_rates: Optional funding rate DataFrame for cost modeling.
        """
        self.data = data
        self.config = config
        self.signal_generator = signal_generator
        self.cost_config = cost_config or CostConfig()
        self.position_size_pct = position_size_pct
        self.n_jobs = n_jobs
        self.funding_rates = funding_rates

    def optimize(
        self,
        grid: ParameterGrid,
        target_metric: str = "sharpe_ratio",
        store_equity: bool = False,
    ) -> List[OptimizationResult]:
        """
        Run optimization over parameter grid.

        Signal generation is always sequential (the shared signal_fn closure
        may contain mutable generator state).  VectorizedBacktest runs are
        parallelised with ThreadPoolExecutor when ``n_jobs != 1``.

        Args:
            grid: Parameter grid to search
            target_metric: Metric to optimize (for sorting results)
            store_equity: Whether to store equity curves in results

        Returns:
            List of OptimizationResult sorted by target metric
        """
        params_list = list(grid)

        # Step 1: generate all signals sequentially (preserves thread safety)
        signal_pairs: List[Tuple[Dict, np.ndarray]] = [
            (p, self.signal_generator(self.data, p)) for p in params_list
        ]

        # Step 2: run stateless backtests — parallelise when n_jobs != 1
        if self.n_jobs == 1 or len(signal_pairs) <= 1:
            results = [
                self._run_backtest(p, s, store_equity) for p, s in signal_pairs
            ]
        else:
            workers = self.n_jobs if self.n_jobs > 0 else None
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {
                    ex.submit(self._run_backtest, p, s, store_equity): idx
                    for idx, (p, s) in enumerate(signal_pairs)
                }
                # Preserve submission order so results are deterministic
                ordered: List[OptimizationResult] = [None] * len(signal_pairs)  # type: ignore[list-item]
                for future in as_completed(futures):
                    ordered[futures[future]] = future.result()
            results = ordered

        results.sort(
            key=lambda r: r.metrics.get(target_metric, float("-inf")),
            reverse=True,
        )
        return results

    def _run_backtest(
        self,
        params: Dict,
        signals: np.ndarray,
        store_equity: bool = False,
    ) -> OptimizationResult:
        """Run a single VectorizedBacktest for pre-computed signals.

        This method is thread-safe: it creates a fresh VectorizedBacktest
        instance and only reads from shared immutable data.
        """
        backtest = VectorizedBacktest(
            config=self.config,
            cost_config=self.cost_config,
            position_size_pct=self.position_size_pct,
        )
        result = backtest.run(data=self.data, signals=signals, funding_rates=self.funding_rates)
        return OptimizationResult(
            params=params,
            metrics=result.metrics,
            equity_curve=result.equity_curve if store_equity else None,
        )

    def get_best_params(
        self,
        results: List[OptimizationResult],
    ) -> Dict:
        """
        Get best parameter combination from results.

        Args:
            results: List of optimization results (assumed sorted)

        Returns:
            Best parameter dictionary
        """
        if not results:
            return {}
        return results[0].params

    def results_to_dataframe(
        self,
        results: List[OptimizationResult],
    ) -> pd.DataFrame:
        """
        Convert optimization results to DataFrame.

        Args:
            results: List of optimization results

        Returns:
            DataFrame with parameters and metrics
        """
        rows = []
        for result in results:
            row = {**result.params, **result.metrics}
            rows.append(row)

        return pd.DataFrame(rows)
