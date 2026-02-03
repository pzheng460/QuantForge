"""
Tests for WalkForwardAnalyzer.

US-8: Walk-Forward 分析
- WalkForwardAnalyzer 执行滚动窗口优化和验证
- 支持定义训练/测试窗口大小
- 支持锚定和滚动窗口模式
- 返回每个窗口的优化结果和样本外表现
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from nexustrader.backtest.optimization.walk_forward import (
    WalkForwardAnalyzer,
    WalkForwardResult,
    WindowType,
)
from nexustrader.backtest.optimization.grid_search import ParameterGrid
from nexustrader.backtest.engine.vectorized import Signal
from nexustrader.backtest.engine.cost_model import CostConfig
from nexustrader.backtest.result import BacktestConfig
from nexustrader.constants import KlineInterval


class TestWindowType:
    """Test WindowType enum."""

    def test_window_types(self):
        """WindowType has expected values."""
        assert WindowType.ROLLING.value == "rolling"
        assert WindowType.ANCHORED.value == "anchored"


class TestWalkForwardResult:
    """Test WalkForwardResult class."""

    def test_result_creation(self):
        """WalkForwardResult stores window data."""
        result = WalkForwardResult(
            window_index=0,
            train_start=datetime(2024, 1, 1),
            train_end=datetime(2024, 1, 15),
            test_start=datetime(2024, 1, 15),
            test_end=datetime(2024, 1, 22),
            best_params={"period": 20},
            train_metrics={"sharpe_ratio": 1.5},
            test_metrics={"sharpe_ratio": 1.2},
        )

        assert result.window_index == 0
        assert result.best_params["period"] == 20
        assert result.train_metrics["sharpe_ratio"] == 1.5
        assert result.test_metrics["sharpe_ratio"] == 1.2


class TestWalkForwardAnalyzerBasic:
    """Test basic WalkForwardAnalyzer functionality."""

    @pytest.fixture
    def sample_data(self):
        """Create sample OHLCV data for walk-forward - minimal for fast tests."""
        dates = pd.date_range("2024-01-01", periods=300, freq="15min")
        np.random.seed(42)
        close = 50000 + np.cumsum(np.random.randn(300) * 50)
        return pd.DataFrame({
            "open": close - np.random.uniform(0, 25, 300),
            "high": close + np.random.uniform(0, 50, 300),
            "low": close - np.random.uniform(0, 50, 300),
            "close": close,
            "volume": np.random.uniform(100, 1000, 300),
        }, index=dates)

    @pytest.fixture
    def backtest_config(self):
        """Create backtest config."""
        return BacktestConfig(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 4),
            initial_capital=10000.0,
        )

    @pytest.fixture
    def signal_generator(self):
        """Create simple signal generator."""
        def generator(data: pd.DataFrame, params: dict) -> np.ndarray:
            signals = np.zeros(len(data))
            period = params.get("period", 20)
            # Simple signal: buy at period, close at 2*period
            if len(data) > period * 2:
                signals[period] = Signal.BUY.value
                signals[period * 2] = Signal.CLOSE.value
            return signals
        return generator

    def test_analyzer_initialization(self, sample_data, backtest_config, signal_generator):
        """WalkForwardAnalyzer initializes correctly."""
        analyzer = WalkForwardAnalyzer(
            data=sample_data,
            config=backtest_config,
            signal_generator=signal_generator,
            train_periods=100,
            test_periods=50,
        )

        assert analyzer.train_periods == 100
        assert analyzer.test_periods == 50

    def test_run_returns_results(self, sample_data, backtest_config, signal_generator):
        """run() returns list of WalkForwardResult."""
        analyzer = WalkForwardAnalyzer(
            data=sample_data,
            config=backtest_config,
            signal_generator=signal_generator,
            train_periods=100,
            test_periods=50,
        )

        grid = ParameterGrid(period=[20])
        results = analyzer.run(grid)

        assert isinstance(results, list)
        assert len(results) > 0
        assert all(isinstance(r, WalkForwardResult) for r in results)

    def test_each_result_has_train_and_test_metrics(self, sample_data, backtest_config, signal_generator):
        """Each result has both train and test metrics."""
        analyzer = WalkForwardAnalyzer(
            data=sample_data,
            config=backtest_config,
            signal_generator=signal_generator,
            train_periods=100,
            test_periods=50,
        )

        grid = ParameterGrid(period=[20])
        results = analyzer.run(grid)

        for result in results:
            assert "sharpe_ratio" in result.train_metrics or "total_return_pct" in result.train_metrics
            assert "sharpe_ratio" in result.test_metrics or "total_return_pct" in result.test_metrics


class TestWalkForwardAnalyzerWindowTypes:
    """Test different window types."""

    @pytest.fixture
    def sample_data(self):
        """Create sample data - minimal for fast tests."""
        dates = pd.date_range("2024-01-01", periods=300, freq="15min")
        np.random.seed(42)
        close = 50000 + np.cumsum(np.random.randn(300) * 50)
        return pd.DataFrame({
            "open": close - 10,
            "high": close + 10,
            "low": close - 10,
            "close": close,
            "volume": np.full(300, 500.0),
        }, index=dates)

    @pytest.fixture
    def backtest_config(self):
        """Create backtest config."""
        return BacktestConfig(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 4),
            initial_capital=10000.0,
        )

    @pytest.fixture
    def signal_generator(self):
        """Create signal generator."""
        def generator(data: pd.DataFrame, params: dict) -> np.ndarray:
            signals = np.zeros(len(data))
            if len(data) > 30:
                signals[5] = Signal.BUY.value
                signals[25] = Signal.CLOSE.value
            return signals
        return generator

    def test_rolling_window(self, sample_data, backtest_config, signal_generator):
        """Rolling window mode creates overlapping windows."""
        analyzer = WalkForwardAnalyzer(
            data=sample_data,
            config=backtest_config,
            signal_generator=signal_generator,
            train_periods=100,
            test_periods=50,
            window_type=WindowType.ROLLING,
        )

        grid = ParameterGrid(param=[1])
        results = analyzer.run(grid)

        # With rolling, train window moves forward
        if len(results) > 1:
            assert results[1].train_start > results[0].train_start

    def test_anchored_window(self, sample_data, backtest_config, signal_generator):
        """Anchored window mode keeps train start fixed."""
        analyzer = WalkForwardAnalyzer(
            data=sample_data,
            config=backtest_config,
            signal_generator=signal_generator,
            train_periods=100,
            test_periods=50,
            window_type=WindowType.ANCHORED,
        )

        grid = ParameterGrid(param=[1])
        results = analyzer.run(grid)

        # With anchored, all train windows start at same time
        if len(results) > 1:
            assert results[1].train_start == results[0].train_start


class TestWalkForwardAnalyzerSummary:
    """Test summary statistics."""

    @pytest.fixture
    def sample_data(self):
        """Create sample data - minimal for fast tests."""
        dates = pd.date_range("2024-01-01", periods=300, freq="15min")
        np.random.seed(42)
        close = 50000 + np.cumsum(np.random.randn(300) * 50)
        return pd.DataFrame({
            "open": close - 10,
            "high": close + 10,
            "low": close - 10,
            "close": close,
            "volume": np.full(300, 500.0),
        }, index=dates)

    @pytest.fixture
    def backtest_config(self):
        """Create backtest config."""
        return BacktestConfig(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 4),
            initial_capital=10000.0,
        )

    @pytest.fixture
    def signal_generator(self):
        """Create signal generator."""
        def generator(data: pd.DataFrame, params: dict) -> np.ndarray:
            signals = np.zeros(len(data))
            if len(data) > 30:
                signals[5] = Signal.BUY.value
                signals[25] = Signal.CLOSE.value
            return signals
        return generator

    def test_get_summary(self, sample_data, backtest_config, signal_generator):
        """get_summary() returns aggregated statistics."""
        analyzer = WalkForwardAnalyzer(
            data=sample_data,
            config=backtest_config,
            signal_generator=signal_generator,
            train_periods=100,
            test_periods=50,
        )

        grid = ParameterGrid(param=[1])
        results = analyzer.run(grid)

        summary = analyzer.get_summary(results)

        assert isinstance(summary, dict)
        assert "avg_train_return" in summary or "windows_count" in summary

    def test_results_to_dataframe(self, sample_data, backtest_config, signal_generator):
        """Results can be converted to DataFrame."""
        analyzer = WalkForwardAnalyzer(
            data=sample_data,
            config=backtest_config,
            signal_generator=signal_generator,
            train_periods=100,
            test_periods=50,
        )

        grid = ParameterGrid(param=[1])
        results = analyzer.run(grid)

        df = analyzer.results_to_dataframe(results)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == len(results)
        assert "window_index" in df.columns


class TestWalkForwardAnalyzerEdgeCases:
    """Test edge cases."""

    @pytest.fixture
    def short_data(self):
        """Create shorter data."""
        dates = pd.date_range("2024-01-01", periods=500, freq="15min")
        close = np.linspace(50000, 50500, 500)
        return pd.DataFrame({
            "open": close - 5,
            "high": close + 10,
            "low": close - 10,
            "close": close,
            "volume": np.full(500, 500.0),
        }, index=dates)

    @pytest.fixture
    def backtest_config(self):
        """Create backtest config."""
        return BacktestConfig(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 5),
            initial_capital=10000.0,
        )

    @pytest.fixture
    def signal_generator(self):
        """Create signal generator."""
        def generator(data: pd.DataFrame, params: dict) -> np.ndarray:
            return np.zeros(len(data))
        return generator

    def test_insufficient_data(self, short_data, backtest_config, signal_generator):
        """Handle data insufficient for multiple windows."""
        analyzer = WalkForwardAnalyzer(
            data=short_data,
            config=backtest_config,
            signal_generator=signal_generator,
            train_periods=300,
            test_periods=150,
        )

        grid = ParameterGrid(param=[1])
        results = analyzer.run(grid)

        # Should still produce at least one result or handle gracefully
        assert isinstance(results, list)
