"""
Walk-forward analysis for strategy validation.

Implements rolling window optimization and out-of-sample validation
to test strategy robustness.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from quantforge.backtest.engine.cost_model import CostConfig
from quantforge.backtest.engine.vectorized import VectorizedBacktest
from quantforge.backtest.optimization.grid_search import GridSearchOptimizer, ParameterGrid
from quantforge.backtest.result import BacktestConfig


class WindowType(Enum):
    """Walk-forward window type."""

    ROLLING = "rolling"
    ANCHORED = "anchored"


@dataclass
class WalkForwardResult:
    """Result from a single walk-forward window."""

    window_index: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    best_params: Dict
    train_metrics: Dict[str, float]
    test_metrics: Dict[str, float]


class WalkForwardAnalyzer:
    """
    Walk-forward analysis for strategy validation.

    Performs rolling optimization and out-of-sample testing.
    """

    def __init__(
        self,
        data: pd.DataFrame,
        config: BacktestConfig,
        signal_generator: Callable[[pd.DataFrame, Dict], np.ndarray],
        train_periods: int,
        test_periods: int,
        window_type: WindowType = WindowType.ROLLING,
        cost_config: Optional[CostConfig] = None,
        position_size_pct: float = 1.0,
        funding_rates: Optional[pd.DataFrame] = None,
    ):
        """
        Initialize walk-forward analyzer.

        Args:
            data: OHLCV DataFrame
            config: Backtest configuration
            signal_generator: Function that generates signals from data and params
            train_periods: Number of periods for training window
            test_periods: Number of periods for test window
            window_type: Rolling or anchored window
            cost_config: Trading cost configuration
            position_size_pct: Fraction of capital to use per trade
            funding_rates: Optional funding rate DataFrame for cost modeling.
                Sliced per window by timestamp alignment.
        """
        self.data = data
        self.config = config
        self.signal_generator = signal_generator
        self.train_periods = train_periods
        self.test_periods = test_periods
        self.window_type = window_type
        self.cost_config = cost_config or CostConfig()
        self.position_size_pct = position_size_pct
        self.funding_rates = funding_rates

    def run(
        self,
        grid: ParameterGrid,
        target_metric: str = "sharpe_ratio",
    ) -> List[WalkForwardResult]:
        """
        Run walk-forward analysis.

        Args:
            grid: Parameter grid to search in each window
            target_metric: Metric to optimize

        Returns:
            List of WalkForwardResult for each window
        """
        results = []
        n = len(self.data)
        window_size = self.train_periods + self.test_periods

        if n < window_size:
            # Not enough data for even one window
            return results

        window_index = 0
        start_idx = 0

        while True:
            # Calculate window boundaries
            if self.window_type == WindowType.ANCHORED:
                train_start_idx = 0
                # In anchored mode, train window grows to include more data
                train_end_idx = self.train_periods + (window_index * self.test_periods)
            else:
                train_start_idx = start_idx
                train_end_idx = train_start_idx + self.train_periods

            test_start_idx = train_end_idx
            test_end_idx = test_start_idx + self.test_periods

            # Check if we have enough data
            if test_end_idx > n:
                break

            # Get window data
            train_data = self.data.iloc[train_start_idx:train_end_idx]
            test_data = self.data.iloc[test_start_idx:test_end_idx]

            # Optimize on training data
            train_config = BacktestConfig(
                symbol=self.config.symbol,
                interval=self.config.interval,
                start_date=train_data.index[0],
                end_date=train_data.index[-1],
                initial_capital=self.config.initial_capital,
                leverage=self.config.leverage,
            )

            # Slice funding rates for this window if available
            train_funding = None
            test_funding = None
            if self.funding_rates is not None and not self.funding_rates.empty:
                ts = self.funding_rates.index
                train_funding = self.funding_rates[
                    (ts >= train_data.index[0]) & (ts <= train_data.index[-1])
                ]
                test_funding = self.funding_rates[
                    (ts >= test_data.index[0]) & (ts <= test_data.index[-1])
                ]

            optimizer = GridSearchOptimizer(
                data=train_data,
                config=train_config,
                signal_generator=self.signal_generator,
                cost_config=self.cost_config,
                position_size_pct=self.position_size_pct,
                funding_rates=train_funding,
            )

            opt_results = optimizer.optimize(grid, target_metric=target_metric)
            best_params = optimizer.get_best_params(opt_results)
            train_metrics = opt_results[0].metrics if opt_results else {}

            # Test on out-of-sample data
            test_config = BacktestConfig(
                symbol=self.config.symbol,
                interval=self.config.interval,
                start_date=test_data.index[0],
                end_date=test_data.index[-1],
                initial_capital=self.config.initial_capital,
                leverage=self.config.leverage,
            )

            test_signals = self.signal_generator(test_data, best_params)
            backtest = VectorizedBacktest(
                config=test_config,
                cost_config=self.cost_config,
                position_size_pct=self.position_size_pct,
            )
            test_result = backtest.run(data=test_data, signals=test_signals, funding_rates=test_funding)

            # Create result
            wf_result = WalkForwardResult(
                window_index=window_index,
                train_start=train_data.index[0].to_pydatetime(),
                train_end=train_data.index[-1].to_pydatetime(),
                test_start=test_data.index[0].to_pydatetime(),
                test_end=test_data.index[-1].to_pydatetime(),
                best_params=best_params,
                train_metrics=train_metrics,
                test_metrics=test_result.metrics,
            )
            results.append(wf_result)

            # Move to next window
            window_index += 1
            start_idx += self.test_periods  # Step by test period

        return results

    def get_summary(self, results: List[WalkForwardResult]) -> Dict:
        """
        Get summary statistics from walk-forward results.

        Args:
            results: List of walk-forward results

        Returns:
            Dictionary with summary statistics
        """
        if not results:
            return {"windows_count": 0}

        train_returns = [r.train_metrics.get("total_return_pct", 0) for r in results]
        test_returns = [r.test_metrics.get("total_return_pct", 0) for r in results]
        train_sharpes = [r.train_metrics.get("sharpe_ratio", 0) for r in results]
        test_sharpes = [r.test_metrics.get("sharpe_ratio", 0) for r in results]

        return {
            "windows_count": len(results),
            "avg_train_return": np.mean(train_returns),
            "avg_test_return": np.mean(test_returns),
            "std_train_return": np.std(train_returns),
            "std_test_return": np.std(test_returns),
            "avg_train_sharpe": np.mean(train_sharpes),
            "avg_test_sharpe": np.mean(test_sharpes),
            "total_test_return": sum(test_returns),
            "positive_test_windows": sum(1 for r in test_returns if r > 0),
            "robustness_ratio": np.mean(test_sharpes) / np.mean(train_sharpes) if np.mean(train_sharpes) != 0 else 0,
        }

    def results_to_dataframe(self, results: List[WalkForwardResult]) -> pd.DataFrame:
        """
        Convert walk-forward results to DataFrame.

        Args:
            results: List of walk-forward results

        Returns:
            DataFrame with window details
        """
        rows = []
        for result in results:
            row = {
                "window_index": result.window_index,
                "train_start": result.train_start,
                "train_end": result.train_end,
                "test_start": result.test_start,
                "test_end": result.test_end,
                **{f"param_{k}": v for k, v in result.best_params.items()},
                **{f"train_{k}": v for k, v in result.train_metrics.items()},
                **{f"test_{k}": v for k, v in result.test_metrics.items()},
            }
            rows.append(row)

        return pd.DataFrame(rows)
