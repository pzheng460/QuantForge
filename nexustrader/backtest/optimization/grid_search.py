"""
Grid search optimization for backtest parameters.

Supports exhaustive search over parameter combinations
with parallel execution support.
"""

from dataclasses import dataclass
from itertools import product
from typing import Callable, Dict, Iterator, List, Optional

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
    ):
        """
        Initialize grid search optimizer.

        Args:
            data: OHLCV DataFrame
            config: Backtest configuration
            signal_generator: Function that generates signals from data and params
            cost_config: Trading cost configuration
            position_size_pct: Fraction of capital to use per trade
        """
        self.data = data
        self.config = config
        self.signal_generator = signal_generator
        self.cost_config = cost_config or CostConfig()
        self.position_size_pct = position_size_pct

    def optimize(
        self,
        grid: ParameterGrid,
        target_metric: str = "sharpe_ratio",
        store_equity: bool = False,
    ) -> List[OptimizationResult]:
        """
        Run optimization over parameter grid.

        Args:
            grid: Parameter grid to search
            target_metric: Metric to optimize (for sorting results)
            store_equity: Whether to store equity curves in results

        Returns:
            List of OptimizationResult sorted by target metric
        """
        results = []

        for params in grid:
            result = self._run_single(params, store_equity)
            results.append(result)

        # Sort by target metric (descending)
        results.sort(
            key=lambda r: r.metrics.get(target_metric, float("-inf")),
            reverse=True,
        )

        return results

    def _run_single(
        self,
        params: Dict,
        store_equity: bool = False,
    ) -> OptimizationResult:
        """
        Run backtest for a single parameter combination.

        Args:
            params: Parameter dictionary
            store_equity: Whether to store equity curve

        Returns:
            OptimizationResult
        """
        # Generate signals
        signals = self.signal_generator(self.data, params)

        # Run backtest
        backtest = VectorizedBacktest(
            config=self.config,
            cost_config=self.cost_config,
            position_size_pct=self.position_size_pct,
        )
        result = backtest.run(data=self.data, signals=signals)

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
