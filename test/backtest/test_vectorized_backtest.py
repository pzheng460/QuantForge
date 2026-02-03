"""
Tests for VectorizedBacktest engine.

US-3: 向量化回测引擎
- VectorizedBacktest 类接受信号序列和配置
- 支持做多/做空/平仓信号
- 返回权益曲线和交易记录
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from nexustrader.backtest.engine.vectorized import VectorizedBacktest, Signal
from nexustrader.backtest.engine.cost_model import CostConfig
from nexustrader.backtest.result import BacktestConfig, BacktestResult
from nexustrader.constants import KlineInterval


class TestSignalEnum:
    """Test Signal enum."""

    def test_signal_values(self):
        """Signal enum has correct values."""
        assert Signal.HOLD.value == 0
        assert Signal.BUY.value == 1
        assert Signal.SELL.value == -1
        assert Signal.CLOSE.value == 2


class TestVectorizedBacktestBasic:
    """Test basic VectorizedBacktest functionality."""

    @pytest.fixture
    def sample_data(self):
        """Create sample OHLCV data."""
        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        np.random.seed(42)
        close = 50000 + np.cumsum(np.random.randn(100) * 100)
        return pd.DataFrame({
            "open": close - np.random.uniform(0, 50, 100),
            "high": close + np.random.uniform(0, 100, 100),
            "low": close - np.random.uniform(0, 100, 100),
            "close": close,
            "volume": np.random.uniform(100, 1000, 100),
        }, index=dates)

    @pytest.fixture
    def backtest_config(self):
        """Create a backtest config."""
        return BacktestConfig(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 2),
            initial_capital=10000.0,
        )

    @pytest.fixture
    def cost_config(self):
        """Create a cost config."""
        return CostConfig(
            maker_fee=0.0002,
            taker_fee=0.0005,
            slippage_pct=0.0005,
            use_funding_rate=False,
        )

    def test_backtest_initialization(self, backtest_config, cost_config):
        """VectorizedBacktest initializes correctly."""
        bt = VectorizedBacktest(
            config=backtest_config,
            cost_config=cost_config,
        )
        assert bt.config == backtest_config
        assert bt.cost_model.config == cost_config

    def test_run_returns_backtest_result(self, sample_data, backtest_config, cost_config):
        """run() returns BacktestResult."""
        bt = VectorizedBacktest(config=backtest_config, cost_config=cost_config)

        # Simple signal: buy at bar 10, close at bar 50
        signals = np.zeros(len(sample_data))
        signals[10] = Signal.BUY.value
        signals[50] = Signal.CLOSE.value

        result = bt.run(data=sample_data, signals=signals)

        assert isinstance(result, BacktestResult)
        assert len(result.equity_curve) == len(sample_data)

    def test_no_trades_with_hold_signals(self, sample_data, backtest_config, cost_config):
        """No trades when all signals are HOLD."""
        bt = VectorizedBacktest(config=backtest_config, cost_config=cost_config)

        signals = np.zeros(len(sample_data))  # All HOLD

        result = bt.run(data=sample_data, signals=signals)

        assert len(result.trades) == 0
        # Equity should remain constant
        assert result.equity_curve.iloc[-1] == backtest_config.initial_capital


class TestVectorizedBacktestTrading:
    """Test trading logic."""

    @pytest.fixture
    def sample_data(self):
        """Create sample OHLCV data with predictable prices."""
        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        # Simple linear price increase
        close = np.linspace(50000, 51000, 100)
        return pd.DataFrame({
            "open": close - 10,
            "high": close + 10,
            "low": close - 10,
            "close": close,
            "volume": np.full(100, 500.0),
        }, index=dates)

    @pytest.fixture
    def backtest_config(self):
        """Create a backtest config."""
        return BacktestConfig(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 2),
            initial_capital=10000.0,
        )

    @pytest.fixture
    def cost_config(self):
        """Create a cost config with zero fees for easier testing."""
        return CostConfig(
            maker_fee=0.0,
            taker_fee=0.0,
            slippage_pct=0.0,
            use_funding_rate=False,
        )

    def test_long_trade_profit(self, sample_data, backtest_config, cost_config):
        """Long trade generates profit when price increases."""
        bt = VectorizedBacktest(config=backtest_config, cost_config=cost_config)

        # Buy at bar 10 (price ~50100), close at bar 90 (price ~50900)
        signals = np.zeros(len(sample_data))
        signals[10] = Signal.BUY.value
        signals[90] = Signal.CLOSE.value

        result = bt.run(data=sample_data, signals=signals)

        # Should have at least one completed trade
        assert len(result.trades) >= 1
        # Final equity should be higher than initial
        assert result.equity_curve.iloc[-1] > backtest_config.initial_capital

    def test_short_trade_profit_on_price_drop(self, backtest_config, cost_config):
        """Short trade generates profit when price decreases."""
        # Create data with price drop
        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        close = np.linspace(51000, 50000, 100)  # Price drops
        data = pd.DataFrame({
            "open": close + 10,
            "high": close + 10,
            "low": close - 10,
            "close": close,
            "volume": np.full(100, 500.0),
        }, index=dates)

        bt = VectorizedBacktest(config=backtest_config, cost_config=cost_config)

        # Short at bar 10, close at bar 90
        signals = np.zeros(len(data))
        signals[10] = Signal.SELL.value
        signals[90] = Signal.CLOSE.value

        result = bt.run(data=data, signals=signals)

        # Final equity should be higher than initial (profit from short)
        assert result.equity_curve.iloc[-1] > backtest_config.initial_capital

    def test_trade_records_entry_exit(self, sample_data, backtest_config, cost_config):
        """Trade records capture entry and exit."""
        bt = VectorizedBacktest(config=backtest_config, cost_config=cost_config)

        signals = np.zeros(len(sample_data))
        signals[10] = Signal.BUY.value
        signals[50] = Signal.CLOSE.value

        result = bt.run(data=sample_data, signals=signals)

        # Should have 2 trade records (entry + exit)
        assert len(result.trades) == 2

        # First trade is entry
        assert result.trades[0].side == "buy"
        # Second trade is exit
        assert result.trades[1].side == "sell"


class TestVectorizedBacktestCosts:
    """Test cost calculations in backtest."""

    @pytest.fixture
    def sample_data(self):
        """Create sample data."""
        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        close = np.full(100, 50000.0)  # Constant price
        return pd.DataFrame({
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": np.full(100, 500.0),
        }, index=dates)

    @pytest.fixture
    def backtest_config(self):
        """Create a backtest config."""
        return BacktestConfig(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 2),
            initial_capital=10000.0,
        )

    def test_fees_reduce_equity(self, sample_data, backtest_config):
        """Fees reduce final equity."""
        # With fees
        cost_with_fees = CostConfig(
            maker_fee=0.0,
            taker_fee=0.001,  # 0.1% fee
            slippage_pct=0.0,
            use_funding_rate=False,
        )

        # Without fees
        cost_no_fees = CostConfig(
            maker_fee=0.0,
            taker_fee=0.0,
            slippage_pct=0.0,
            use_funding_rate=False,
        )

        signals = np.zeros(len(sample_data))
        signals[10] = Signal.BUY.value
        signals[50] = Signal.CLOSE.value

        bt_with_fees = VectorizedBacktest(config=backtest_config, cost_config=cost_with_fees)
        bt_no_fees = VectorizedBacktest(config=backtest_config, cost_config=cost_no_fees)

        result_with_fees = bt_with_fees.run(data=sample_data, signals=signals)
        result_no_fees = bt_no_fees.run(data=sample_data, signals=signals)

        # With fees should have less equity
        assert result_with_fees.equity_curve.iloc[-1] < result_no_fees.equity_curve.iloc[-1]

    def test_slippage_affects_execution_price(self, sample_data, backtest_config):
        """Slippage affects execution prices."""
        cost_with_slippage = CostConfig(
            maker_fee=0.0,
            taker_fee=0.0,
            slippage_pct=0.01,  # 1% slippage
            use_funding_rate=False,
        )

        cost_no_slippage = CostConfig(
            maker_fee=0.0,
            taker_fee=0.0,
            slippage_pct=0.0,
            use_funding_rate=False,
        )

        signals = np.zeros(len(sample_data))
        signals[10] = Signal.BUY.value
        signals[50] = Signal.CLOSE.value

        bt_with_slippage = VectorizedBacktest(config=backtest_config, cost_config=cost_with_slippage)
        bt_no_slippage = VectorizedBacktest(config=backtest_config, cost_config=cost_no_slippage)

        result_with_slippage = bt_with_slippage.run(data=sample_data, signals=signals)
        result_no_slippage = bt_no_slippage.run(data=sample_data, signals=signals)

        # With slippage should have lower equity (buying higher, selling lower)
        assert result_with_slippage.equity_curve.iloc[-1] < result_no_slippage.equity_curve.iloc[-1]


class TestVectorizedBacktestMetrics:
    """Test backtest result metrics."""

    @pytest.fixture
    def sample_data(self):
        """Create sample data."""
        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        np.random.seed(42)
        close = 50000 + np.cumsum(np.random.randn(100) * 100)
        return pd.DataFrame({
            "open": close - 10,
            "high": close + 10,
            "low": close - 10,
            "close": close,
            "volume": np.full(100, 500.0),
        }, index=dates)

    @pytest.fixture
    def backtest_config(self):
        """Create a backtest config."""
        return BacktestConfig(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 2),
            initial_capital=10000.0,
        )

    @pytest.fixture
    def cost_config(self):
        """Create a cost config."""
        return CostConfig(
            maker_fee=0.0,
            taker_fee=0.0,
            slippage_pct=0.0,
            use_funding_rate=False,
        )

    def test_result_has_metrics(self, sample_data, backtest_config, cost_config):
        """Result includes performance metrics."""
        bt = VectorizedBacktest(config=backtest_config, cost_config=cost_config)

        signals = np.zeros(len(sample_data))
        signals[10] = Signal.BUY.value
        signals[50] = Signal.CLOSE.value

        result = bt.run(data=sample_data, signals=signals)

        # Should have basic metrics
        assert "total_return_pct" in result.metrics
        assert "max_drawdown_pct" in result.metrics
        assert "total_trades" in result.metrics

    def test_equity_curve_length_matches_data(self, sample_data, backtest_config, cost_config):
        """Equity curve has same length as input data."""
        bt = VectorizedBacktest(config=backtest_config, cost_config=cost_config)

        signals = np.zeros(len(sample_data))
        signals[10] = Signal.BUY.value
        signals[50] = Signal.CLOSE.value

        result = bt.run(data=sample_data, signals=signals)

        assert len(result.equity_curve) == len(sample_data)
