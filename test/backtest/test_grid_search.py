"""
Tests for GridSearchOptimizer.

US-7: 网格搜索优化
- GridSearchOptimizer 执行参数网格搜索
- 支持定义参数范围和步长
- 并行执行多参数组合回测
- 返回排序后的优化结果
"""

from datetime import datetime
from typing import Dict, Optional

import numpy as np
import pandas as pd
import pytest

from quantforge.backtest.optimization.grid_search import (
    GridSearchOptimizer,
    ParameterGrid,
    OptimizationResult,
)
from quantforge.backtest.engine.vectorized import VectorizedBacktest, Signal
from quantforge.backtest.engine.cost_model import CostConfig
from quantforge.backtest.result import BacktestConfig, BacktestResult
from quantforge.constants import KlineInterval


class TestParameterGrid:
    """Test ParameterGrid class."""

    def test_grid_from_ranges(self):
        """Create grid from parameter ranges."""
        grid = ParameterGrid(
            fast_period=range(5, 15, 5),  # [5, 10]
            slow_period=range(20, 40, 10),  # [20, 30]
        )

        combinations = list(grid)

        assert len(combinations) == 4  # 2 x 2
        assert {"fast_period": 5, "slow_period": 20} in combinations
        assert {"fast_period": 10, "slow_period": 30} in combinations

    def test_grid_from_lists(self):
        """Create grid from explicit lists."""
        grid = ParameterGrid(
            threshold=[0.01, 0.02, 0.03],
            multiplier=[1.0, 1.5],
        )

        combinations = list(grid)

        assert len(combinations) == 6  # 3 x 2

    def test_grid_single_parameter(self):
        """Grid with single parameter."""
        grid = ParameterGrid(period=[10, 20, 30])

        combinations = list(grid)

        assert len(combinations) == 3

    def test_grid_length(self):
        """Grid reports correct length."""
        grid = ParameterGrid(
            a=[1, 2],
            b=[3, 4, 5],
        )

        assert len(grid) == 6


class TestOptimizationResult:
    """Test OptimizationResult class."""

    def test_result_creation(self):
        """OptimizationResult stores parameters and metrics."""
        result = OptimizationResult(
            params={"period": 20, "threshold": 0.01},
            metrics={"sharpe_ratio": 1.5, "total_return_pct": 10.0},
        )

        assert result.params["period"] == 20
        assert result.metrics["sharpe_ratio"] == 1.5

    def test_result_comparison(self):
        """Results can be compared by target metric."""
        result1 = OptimizationResult(
            params={"period": 20},
            metrics={"sharpe_ratio": 1.5},
        )
        result2 = OptimizationResult(
            params={"period": 30},
            metrics={"sharpe_ratio": 2.0},
        )

        # Higher sharpe is better
        assert result2.metrics["sharpe_ratio"] > result1.metrics["sharpe_ratio"]


class TestGridSearchOptimizerBasic:
    """Test basic GridSearchOptimizer functionality."""

    @pytest.fixture
    def sample_data(self):
        """Create sample OHLCV data."""
        dates = pd.date_range("2024-01-01", periods=200, freq="15min")
        np.random.seed(42)
        close = 50000 + np.cumsum(np.random.randn(200) * 100)
        return pd.DataFrame({
            "open": close - np.random.uniform(0, 50, 200),
            "high": close + np.random.uniform(0, 100, 200),
            "low": close - np.random.uniform(0, 100, 200),
            "close": close,
            "volume": np.random.uniform(100, 1000, 200),
        }, index=dates)

    @pytest.fixture
    def backtest_config(self):
        """Create backtest config."""
        return BacktestConfig(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 3),
            initial_capital=10000.0,
        )

    def test_optimizer_initialization(self, sample_data, backtest_config):
        """GridSearchOptimizer initializes correctly."""

        def signal_generator(data: pd.DataFrame, params: Dict) -> np.ndarray:
            """Simple signal generator."""
            signals = np.zeros(len(data))
            signals[10] = Signal.BUY.value
            signals[50] = Signal.CLOSE.value
            return signals

        optimizer = GridSearchOptimizer(
            data=sample_data,
            config=backtest_config,
            signal_generator=signal_generator,
        )

        assert optimizer.data is sample_data
        assert optimizer.config == backtest_config

    def test_optimize_returns_results(self, sample_data, backtest_config):
        """optimize() returns list of OptimizationResult."""

        def signal_generator(data: pd.DataFrame, params: Dict) -> np.ndarray:
            """Signal generator using parameters."""
            signals = np.zeros(len(data))
            buy_bar = params.get("buy_bar", 10)
            sell_bar = params.get("sell_bar", 50)
            signals[buy_bar] = Signal.BUY.value
            signals[sell_bar] = Signal.CLOSE.value
            return signals

        optimizer = GridSearchOptimizer(
            data=sample_data,
            config=backtest_config,
            signal_generator=signal_generator,
        )

        grid = ParameterGrid(
            buy_bar=[10, 20],
            sell_bar=[50, 60],
        )

        results = optimizer.optimize(grid)

        assert isinstance(results, list)
        assert len(results) == 4  # 2 x 2 combinations
        assert all(isinstance(r, OptimizationResult) for r in results)

    def test_results_sorted_by_metric(self, sample_data, backtest_config):
        """Results are sorted by target metric."""

        def signal_generator(data: pd.DataFrame, params: Dict) -> np.ndarray:
            """Signal generator."""
            signals = np.zeros(len(data))
            buy_bar = params.get("buy_bar", 10)
            signals[buy_bar] = Signal.BUY.value
            signals[buy_bar + 40] = Signal.CLOSE.value
            return signals

        optimizer = GridSearchOptimizer(
            data=sample_data,
            config=backtest_config,
            signal_generator=signal_generator,
        )

        grid = ParameterGrid(buy_bar=[10, 20, 30])

        results = optimizer.optimize(grid, target_metric="sharpe_ratio")

        # Results should be sorted descending by sharpe_ratio
        sharpe_values = [r.metrics["sharpe_ratio"] for r in results]
        assert sharpe_values == sorted(sharpe_values, reverse=True)


class TestGridSearchOptimizerAdvanced:
    """Test advanced GridSearchOptimizer features."""

    @pytest.fixture
    def sample_data(self):
        """Create sample OHLCV data."""
        dates = pd.date_range("2024-01-01", periods=200, freq="15min")
        np.random.seed(42)
        close = 50000 + np.cumsum(np.random.randn(200) * 100)
        return pd.DataFrame({
            "open": close - 10,
            "high": close + 10,
            "low": close - 10,
            "close": close,
            "volume": np.full(200, 500.0),
        }, index=dates)

    @pytest.fixture
    def backtest_config(self):
        """Create backtest config."""
        return BacktestConfig(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 3),
            initial_capital=10000.0,
        )

    def test_optimize_with_cost_config(self, sample_data, backtest_config):
        """Optimization with cost configuration."""

        def signal_generator(data: pd.DataFrame, params: Dict) -> np.ndarray:
            signals = np.zeros(len(data))
            signals[10] = Signal.BUY.value
            signals[50] = Signal.CLOSE.value
            return signals

        cost_config = CostConfig(
            maker_fee=0.0001,
            taker_fee=0.0003,
            slippage_pct=0.0001,
        )

        optimizer = GridSearchOptimizer(
            data=sample_data,
            config=backtest_config,
            signal_generator=signal_generator,
            cost_config=cost_config,
        )

        grid = ParameterGrid(param=[1, 2])
        results = optimizer.optimize(grid)

        assert len(results) == 2

    def test_get_best_params(self, sample_data, backtest_config):
        """get_best_params() returns best parameter combination."""

        def signal_generator(data: pd.DataFrame, params: Dict) -> np.ndarray:
            signals = np.zeros(len(data))
            signals[10] = Signal.BUY.value
            signals[50] = Signal.CLOSE.value
            return signals

        optimizer = GridSearchOptimizer(
            data=sample_data,
            config=backtest_config,
            signal_generator=signal_generator,
        )

        grid = ParameterGrid(param=[1, 2, 3])
        results = optimizer.optimize(grid)

        best_params = optimizer.get_best_params(results)

        assert isinstance(best_params, dict)
        assert "param" in best_params

    def test_results_to_dataframe(self, sample_data, backtest_config):
        """Results can be converted to DataFrame."""

        def signal_generator(data: pd.DataFrame, params: Dict) -> np.ndarray:
            signals = np.zeros(len(data))
            signals[10] = Signal.BUY.value
            signals[50] = Signal.CLOSE.value
            return signals

        optimizer = GridSearchOptimizer(
            data=sample_data,
            config=backtest_config,
            signal_generator=signal_generator,
        )

        grid = ParameterGrid(a=[1, 2], b=[3, 4])
        results = optimizer.optimize(grid)

        df = optimizer.results_to_dataframe(results)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 4
        assert "a" in df.columns
        assert "b" in df.columns
        assert "sharpe_ratio" in df.columns or "total_return_pct" in df.columns


class TestGridSearchOptimizerEdgeCases:
    """Test edge cases."""

    @pytest.fixture
    def sample_data(self):
        """Create sample data."""
        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        close = np.linspace(50000, 50100, 100)
        return pd.DataFrame({
            "open": close - 5,
            "high": close + 10,
            "low": close - 10,
            "close": close,
            "volume": np.full(100, 500.0),
        }, index=dates)

    @pytest.fixture
    def backtest_config(self):
        """Create backtest config."""
        return BacktestConfig(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 2),
            initial_capital=10000.0,
        )

    def test_single_parameter_combination(self, sample_data, backtest_config):
        """Handle grid with single combination."""

        def signal_generator(data: pd.DataFrame, params: Dict) -> np.ndarray:
            signals = np.zeros(len(data))
            return signals

        optimizer = GridSearchOptimizer(
            data=sample_data,
            config=backtest_config,
            signal_generator=signal_generator,
        )

        grid = ParameterGrid(param=[1])
        results = optimizer.optimize(grid)

        assert len(results) == 1

    def test_no_trades_scenario(self, sample_data, backtest_config):
        """Handle parameters that produce no trades."""

        def signal_generator(data: pd.DataFrame, params: Dict) -> np.ndarray:
            # No signals - no trades
            return np.zeros(len(data))

        optimizer = GridSearchOptimizer(
            data=sample_data,
            config=backtest_config,
            signal_generator=signal_generator,
        )

        grid = ParameterGrid(param=[1, 2])
        results = optimizer.optimize(grid)

        assert len(results) == 2
        # Should handle zero trades gracefully
        for result in results:
            assert "total_return_pct" in result.metrics
