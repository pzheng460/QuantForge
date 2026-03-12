"""
Detailed tests for Hurst-Kalman strategy backtest correctness.

These tests verify:
1. Signal generation logic
2. Capital tracking accuracy
3. Position sizing correctness
4. Trade execution logic
5. Equity calculation accuracy
"""

import numpy as np
import pandas as pd
import pytest
from datetime import datetime, timedelta

from quantforge.backtest import (
    BacktestConfig,
    CostConfig,
    Signal,
    VectorizedBacktest,
)
from quantforge.constants import KlineInterval


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def simple_data():
    """Create simple OHLCV data for testing."""
    dates = pd.date_range(start="2024-01-01", periods=100, freq="15min")
    prices = [100.0] * 100  # Constant price
    return pd.DataFrame({
        "open": prices,
        "high": prices,
        "low": prices,
        "close": prices,
        "volume": [1000.0] * 100,
    }, index=dates)


@pytest.fixture
def trending_up_data():
    """Create data with upward trend."""
    dates = pd.date_range(start="2024-01-01", periods=100, freq="15min")
    # Price goes from 100 to 200 linearly
    prices = [100.0 + i for i in range(100)]
    return pd.DataFrame({
        "open": prices,
        "high": [p + 1 for p in prices],
        "low": [p - 1 for p in prices],
        "close": prices,
        "volume": [1000.0] * 100,
    }, index=dates)


@pytest.fixture
def trending_down_data():
    """Create data with downward trend."""
    dates = pd.date_range(start="2024-01-01", periods=100, freq="15min")
    # Price goes from 200 to 100 linearly
    prices = [200.0 - i for i in range(100)]
    return pd.DataFrame({
        "open": prices,
        "high": [p + 1 for p in prices],
        "low": [p - 1 for p in prices],
        "close": prices,
        "volume": [1000.0] * 100,
    }, index=dates)


@pytest.fixture
def backtest_config():
    """Create standard backtest configuration."""
    return BacktestConfig(
        symbol="BTC/USDT:USDT",
        interval=KlineInterval.MINUTE_15,
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 1, 2),
        initial_capital=10000.0,
    )


@pytest.fixture
def zero_cost_config():
    """Create cost config with zero fees for easier calculation verification."""
    return CostConfig(
        maker_fee=0.0,
        taker_fee=0.0,
        slippage_pct=0.0,
    )


@pytest.fixture
def standard_cost_config():
    """Create standard cost config for realistic testing."""
    return CostConfig(
        maker_fee=0.0002,
        taker_fee=0.0005,
        slippage_pct=0.0005,
    )


# =============================================================================
# TEST CAPITAL TRACKING
# =============================================================================

class TestCapitalTracking:
    """Tests for verifying capital is correctly tracked during trades."""

    def test_initial_capital_preserved_with_no_trades(
        self, simple_data, backtest_config, zero_cost_config
    ):
        """Capital should remain unchanged when no trades occur."""
        bt = VectorizedBacktest(config=backtest_config, cost_config=zero_cost_config)
        signals = np.zeros(len(simple_data))  # All HOLD signals

        result = bt.run(data=simple_data, signals=signals)

        # Final equity should equal initial capital
        assert result.equity_curve.iloc[-1] == backtest_config.initial_capital
        assert len(result.trades) == 0

    def test_capital_decreases_on_open_long(
        self, simple_data, backtest_config, zero_cost_config
    ):
        """Opening a position should decrease available capital."""
        bt = VectorizedBacktest(
            config=backtest_config,
            cost_config=zero_cost_config,
            position_size_pct=1.0,  # 100% of capital
        )

        # Buy at bar 10
        signals = np.zeros(len(simple_data))
        signals[10] = Signal.BUY.value

        result = bt.run(data=simple_data, signals=signals)

        # Should have one trade (open long)
        assert len(result.trades) == 1

        # Trade should record the position
        trade = result.trades[0]
        assert trade.side == "buy"
        assert trade.amount > 0

        # Capital after should be negative (position value deducted)
        assert trade.capital_after < backtest_config.initial_capital

    def test_capital_restored_on_close(
        self, simple_data, backtest_config, zero_cost_config
    ):
        """Closing a position should restore capital (minus/plus PnL)."""
        bt = VectorizedBacktest(
            config=backtest_config,
            cost_config=zero_cost_config,
            position_size_pct=1.0,
        )

        # Buy at bar 10, close at bar 20 (same price, no profit/loss)
        signals = np.zeros(len(simple_data))
        signals[10] = Signal.BUY.value
        signals[20] = Signal.CLOSE.value

        result = bt.run(data=simple_data, signals=signals)

        # Should have two trades: open and close
        assert len(result.trades) == 2

        # Final equity should equal initial capital (no price change, no fees)
        assert abs(result.equity_curve.iloc[-1] - backtest_config.initial_capital) < 0.01

    def test_equity_increases_on_profitable_long(
        self, trending_up_data, backtest_config, zero_cost_config
    ):
        """Equity should increase when price goes up for a long position."""
        bt = VectorizedBacktest(
            config=backtest_config,
            cost_config=zero_cost_config,
            position_size_pct=1.0,
        )

        # Buy at bar 10 (price=110), close at bar 50 (price=150)
        signals = np.zeros(len(trending_up_data))
        signals[10] = Signal.BUY.value
        signals[50] = Signal.CLOSE.value

        result = bt.run(data=trending_up_data, signals=signals)

        # Entry price = 110, exit price = 150
        # Profit = (150 - 110) / 110 ≈ 36.36%
        expected_profit_pct = (150 - 110) / 110

        # Final equity should be approximately initial * (1 + profit)
        expected_final = backtest_config.initial_capital * (1 + expected_profit_pct)

        assert result.equity_curve.iloc[-1] > backtest_config.initial_capital
        assert abs(result.equity_curve.iloc[-1] - expected_final) < 1.0

    def test_equity_increases_on_profitable_short(
        self, trending_down_data, backtest_config, zero_cost_config
    ):
        """Equity should increase when price goes down for a short position."""
        bt = VectorizedBacktest(
            config=backtest_config,
            cost_config=zero_cost_config,
            position_size_pct=1.0,
        )

        # Sell at bar 10 (price=190), close at bar 50 (price=150)
        signals = np.zeros(len(trending_down_data))
        signals[10] = Signal.SELL.value
        signals[50] = Signal.CLOSE.value

        result = bt.run(data=trending_down_data, signals=signals)

        # Entry price = 190, exit price = 150
        # Profit = (190 - 150) / 190 ≈ 21.05%
        expected_profit_pct = (190 - 150) / 190

        # Final equity should be approximately initial * (1 + profit)
        expected_final = backtest_config.initial_capital * (1 + expected_profit_pct)

        assert result.equity_curve.iloc[-1] > backtest_config.initial_capital
        assert abs(result.equity_curve.iloc[-1] - expected_final) < 1.0


# =============================================================================
# TEST POSITION SIZING
# =============================================================================

class TestPositionSizing:
    """Tests for verifying position sizing is correct."""

    def test_full_position_size(
        self, simple_data, backtest_config, zero_cost_config
    ):
        """100% position size should use all capital."""
        bt = VectorizedBacktest(
            config=backtest_config,
            cost_config=zero_cost_config,
            position_size_pct=1.0,
        )

        signals = np.zeros(len(simple_data))
        signals[10] = Signal.BUY.value

        result = bt.run(data=simple_data, signals=signals)

        trade = result.trades[0]
        # Position value should equal initial capital
        position_value = trade.amount * trade.price
        assert abs(position_value - backtest_config.initial_capital) < 1.0

    def test_half_position_size(
        self, simple_data, backtest_config, zero_cost_config
    ):
        """50% position size should use half the capital."""
        bt = VectorizedBacktest(
            config=backtest_config,
            cost_config=zero_cost_config,
            position_size_pct=0.5,
        )

        signals = np.zeros(len(simple_data))
        signals[10] = Signal.BUY.value

        result = bt.run(data=simple_data, signals=signals)

        trade = result.trades[0]
        position_value = trade.amount * trade.price
        expected_value = backtest_config.initial_capital * 0.5
        assert abs(position_value - expected_value) < 1.0

    def test_position_size_with_fees(
        self, simple_data, backtest_config, standard_cost_config
    ):
        """Position size should account for fees and slippage."""
        bt = VectorizedBacktest(
            config=backtest_config,
            cost_config=standard_cost_config,
            position_size_pct=1.0,
        )

        signals = np.zeros(len(simple_data))
        signals[10] = Signal.BUY.value

        result = bt.run(data=simple_data, signals=signals)

        trade = result.trades[0]
        # Adjusted price should include slippage
        assert trade.price > simple_data["close"].iloc[10]
        # Fee should be positive
        assert trade.fee > 0


# =============================================================================
# TEST TRADE EXECUTION
# =============================================================================

class TestTradeExecution:
    """Tests for verifying trade execution logic."""

    def test_buy_signal_opens_long(
        self, simple_data, backtest_config, zero_cost_config
    ):
        """BUY signal should open a long position."""
        bt = VectorizedBacktest(config=backtest_config, cost_config=zero_cost_config)

        signals = np.zeros(len(simple_data))
        signals[10] = Signal.BUY.value

        result = bt.run(data=simple_data, signals=signals)

        assert len(result.trades) == 1
        assert result.trades[0].side == "buy"
        assert result.trades[0].position_after > 0

    def test_sell_signal_opens_short(
        self, simple_data, backtest_config, zero_cost_config
    ):
        """SELL signal should open a short position."""
        bt = VectorizedBacktest(config=backtest_config, cost_config=zero_cost_config)

        signals = np.zeros(len(simple_data))
        signals[10] = Signal.SELL.value

        result = bt.run(data=simple_data, signals=signals)

        assert len(result.trades) == 1
        assert result.trades[0].side == "sell"
        assert result.trades[0].position_after < 0

    def test_close_signal_closes_long(
        self, simple_data, backtest_config, zero_cost_config
    ):
        """CLOSE signal should close a long position."""
        bt = VectorizedBacktest(config=backtest_config, cost_config=zero_cost_config)

        signals = np.zeros(len(simple_data))
        signals[10] = Signal.BUY.value
        signals[20] = Signal.CLOSE.value

        result = bt.run(data=simple_data, signals=signals)

        assert len(result.trades) == 2
        # Second trade should close the position
        assert result.trades[1].position_after == 0.0

    def test_close_signal_closes_short(
        self, simple_data, backtest_config, zero_cost_config
    ):
        """CLOSE signal should close a short position."""
        bt = VectorizedBacktest(config=backtest_config, cost_config=zero_cost_config)

        signals = np.zeros(len(simple_data))
        signals[10] = Signal.SELL.value
        signals[20] = Signal.CLOSE.value

        result = bt.run(data=simple_data, signals=signals)

        assert len(result.trades) == 2
        # Second trade should close the position
        assert result.trades[1].position_after == 0.0

    def test_buy_closes_short_and_opens_long(
        self, simple_data, backtest_config, zero_cost_config
    ):
        """BUY signal while short should close short and open long."""
        bt = VectorizedBacktest(config=backtest_config, cost_config=zero_cost_config)

        signals = np.zeros(len(simple_data))
        signals[10] = Signal.SELL.value  # Open short
        signals[20] = Signal.BUY.value   # Close short, open long

        result = bt.run(data=simple_data, signals=signals)

        # Should have 3 trades: open short, close short, open long
        assert len(result.trades) == 3
        assert result.trades[0].side == "sell"  # Open short
        assert result.trades[1].side == "buy"   # Close short
        assert result.trades[2].side == "buy"   # Open long

    def test_sell_closes_long_and_opens_short(
        self, simple_data, backtest_config, zero_cost_config
    ):
        """SELL signal while long should close long and open short."""
        bt = VectorizedBacktest(config=backtest_config, cost_config=zero_cost_config)

        signals = np.zeros(len(simple_data))
        signals[10] = Signal.BUY.value   # Open long
        signals[20] = Signal.SELL.value  # Close long, open short

        result = bt.run(data=simple_data, signals=signals)

        # Should have 3 trades: open long, close long, open short
        assert len(result.trades) == 3
        assert result.trades[0].side == "buy"   # Open long
        assert result.trades[1].side == "sell"  # Close long
        assert result.trades[2].side == "sell"  # Open short


# =============================================================================
# TEST EQUITY CALCULATION
# =============================================================================

class TestEquityCalculation:
    """Tests for verifying equity curve calculation."""

    def test_equity_starts_at_initial_capital(
        self, simple_data, backtest_config, zero_cost_config
    ):
        """Equity should start at initial capital."""
        bt = VectorizedBacktest(config=backtest_config, cost_config=zero_cost_config)
        signals = np.zeros(len(simple_data))

        result = bt.run(data=simple_data, signals=signals)

        assert result.equity_curve.iloc[0] == backtest_config.initial_capital

    def test_equity_constant_when_no_position(
        self, simple_data, backtest_config, zero_cost_config
    ):
        """Equity should remain constant when not in a position."""
        bt = VectorizedBacktest(config=backtest_config, cost_config=zero_cost_config)
        signals = np.zeros(len(simple_data))

        result = bt.run(data=simple_data, signals=signals)

        # All equity values should be equal
        assert all(result.equity_curve == backtest_config.initial_capital)

    def test_equity_tracks_unrealized_pnl(
        self, trending_up_data, backtest_config, zero_cost_config
    ):
        """Equity should track unrealized PnL while in a position."""
        bt = VectorizedBacktest(
            config=backtest_config,
            cost_config=zero_cost_config,
            position_size_pct=1.0,
        )

        # Buy at bar 10 and hold
        signals = np.zeros(len(trending_up_data))
        signals[10] = Signal.BUY.value

        result = bt.run(data=trending_up_data, signals=signals)

        # Equity should increase as price increases
        entry_bar = 10
        for i in range(entry_bar + 1, len(trending_up_data)):
            assert result.equity_curve.iloc[i] >= result.equity_curve.iloc[i - 1]

    def test_equity_after_multiple_trades(
        self, trending_up_data, backtest_config, zero_cost_config
    ):
        """Equity should correctly accumulate after multiple trades."""
        bt = VectorizedBacktest(
            config=backtest_config,
            cost_config=zero_cost_config,
            position_size_pct=1.0,
        )

        # Multiple trades
        signals = np.zeros(len(trending_up_data))
        signals[10] = Signal.BUY.value   # Buy at 110
        signals[20] = Signal.CLOSE.value  # Close at 120 (profit)
        signals[30] = Signal.BUY.value   # Buy at 130
        signals[40] = Signal.CLOSE.value  # Close at 140 (profit)

        result = bt.run(data=trending_up_data, signals=signals)

        # Should have made profit on both trades
        assert result.equity_curve.iloc[-1] > backtest_config.initial_capital

        # Calculate expected return
        # Trade 1: (120 - 110) / 110 = 9.09%
        # Trade 2: (140 - 130) / 130 = 7.69%
        # Compound: (1 + 0.0909) * (1 + 0.0769) - 1 ≈ 17.47%
        expected_return = (1 + (120 - 110) / 110) * (1 + (140 - 130) / 130) - 1
        expected_final = backtest_config.initial_capital * (1 + expected_return)

        assert abs(result.equity_curve.iloc[-1] - expected_final) < 1.0


# =============================================================================
# TEST REALISTIC SCENARIOS
# =============================================================================

class TestRealisticScenarios:
    """Tests for realistic trading scenarios."""

    def test_maximum_return_is_bounded(
        self, trending_up_data, backtest_config, standard_cost_config
    ):
        """Maximum return should be bounded by price movement and position size."""
        bt = VectorizedBacktest(
            config=backtest_config,
            cost_config=standard_cost_config,
            position_size_pct=1.0,
        )

        # Buy at start, close at end
        signals = np.zeros(len(trending_up_data))
        signals[1] = Signal.BUY.value
        signals[-2] = Signal.CLOSE.value

        result = bt.run(data=trending_up_data, signals=signals)

        # Price doubles from 100 to 200, max return should be ~100%
        # (less due to fees and slippage)
        max_theoretical_return = 100  # 100%
        actual_return = result.metrics["total_return_pct"]

        assert actual_return < max_theoretical_return
        assert actual_return > 0  # Should still be profitable
        assert actual_return < 200  # Sanity check: not more than 2x

    def test_frequent_trading_reduces_returns(
        self, trending_up_data, backtest_config, standard_cost_config
    ):
        """Frequent trading should reduce returns due to fees."""
        # Strategy 1: Hold the entire time
        bt1 = VectorizedBacktest(
            config=backtest_config,
            cost_config=standard_cost_config,
            position_size_pct=1.0,
        )
        signals1 = np.zeros(len(trending_up_data))
        signals1[1] = Signal.BUY.value
        signals1[-2] = Signal.CLOSE.value
        result1 = bt1.run(data=trending_up_data, signals=signals1)

        # Strategy 2: Trade frequently
        bt2 = VectorizedBacktest(
            config=backtest_config,
            cost_config=standard_cost_config,
            position_size_pct=1.0,
        )
        signals2 = np.zeros(len(trending_up_data))
        for i in range(10, 90, 10):
            signals2[i] = Signal.BUY.value
            signals2[i + 5] = Signal.CLOSE.value
        result2 = bt2.run(data=trending_up_data, signals=signals2)

        # Holding should outperform frequent trading (due to fees)
        assert result1.metrics["total_return_pct"] > result2.metrics["total_return_pct"]

    def test_losing_trade_reduces_equity(
        self, trending_down_data, backtest_config, zero_cost_config
    ):
        """Going long in a downtrend should result in losses."""
        bt = VectorizedBacktest(
            config=backtest_config,
            cost_config=zero_cost_config,
            position_size_pct=1.0,
        )

        # Buy in downtrend
        signals = np.zeros(len(trending_down_data))
        signals[10] = Signal.BUY.value   # Buy at 190
        signals[50] = Signal.CLOSE.value  # Close at 150

        result = bt.run(data=trending_down_data, signals=signals)

        # Should have lost money
        assert result.equity_curve.iloc[-1] < backtest_config.initial_capital
        assert result.metrics["total_return_pct"] < 0


# =============================================================================
# TEST METRICS ACCURACY
# =============================================================================

class TestMetricsAccuracy:
    """Tests for verifying metrics are calculated correctly."""

    def test_total_return_calculation(
        self, trending_up_data, backtest_config, zero_cost_config
    ):
        """Total return should be (final - initial) / initial * 100."""
        bt = VectorizedBacktest(
            config=backtest_config,
            cost_config=zero_cost_config,
            position_size_pct=1.0,
        )

        signals = np.zeros(len(trending_up_data))
        signals[10] = Signal.BUY.value
        signals[50] = Signal.CLOSE.value

        result = bt.run(data=trending_up_data, signals=signals)

        initial = backtest_config.initial_capital
        final = result.equity_curve.iloc[-1]
        expected_return = (final - initial) / initial * 100

        assert abs(result.metrics["total_return_pct"] - expected_return) < 0.01

    def test_win_rate_calculation(
        self, trending_up_data, backtest_config, zero_cost_config
    ):
        """Win rate should be winning trades / total trades * 100."""
        # Create data that goes up then down
        dates = pd.date_range(start="2024-01-01", periods=100, freq="15min")
        prices = [100 + i if i < 50 else 150 - (i - 50) for i in range(100)]
        data = pd.DataFrame({
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": prices,
            "volume": [1000.0] * 100,
        }, index=dates)

        bt = VectorizedBacktest(
            config=backtest_config,
            cost_config=zero_cost_config,
            position_size_pct=1.0,
        )

        # One winning trade, one losing trade
        signals = np.zeros(100)
        signals[10] = Signal.BUY.value   # Buy at 110
        signals[30] = Signal.CLOSE.value  # Close at 130 (win)
        signals[60] = Signal.BUY.value   # Buy at 140
        signals[80] = Signal.CLOSE.value  # Close at 120 (loss)

        result = bt.run(data=data, signals=signals)

        # Should be 50% win rate
        assert result.metrics["win_rate_pct"] == 50.0

    def test_max_drawdown_calculation(
        self, simple_data, backtest_config, zero_cost_config
    ):
        """Max drawdown should track the largest peak-to-trough decline."""
        # Create price data that goes up, then down significantly, then up
        dates = pd.date_range(start="2024-01-01", periods=100, freq="15min")
        prices = []
        for i in range(100):
            if i < 30:
                prices.append(100 + i)  # Rise to 130
            elif i < 60:
                prices.append(130 - (i - 30) * 2)  # Drop to 70
            else:
                prices.append(70 + (i - 60))  # Rise to 110

        data = pd.DataFrame({
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": prices,
            "volume": [1000.0] * 100,
        }, index=dates)

        bt = VectorizedBacktest(
            config=backtest_config,
            cost_config=zero_cost_config,
            position_size_pct=1.0,
        )

        # Hold throughout
        signals = np.zeros(100)
        signals[1] = Signal.BUY.value

        result = bt.run(data=data, signals=signals)

        # Max drawdown should be significant (peak at 130, trough at 70)
        # Drawdown = (130 - 70) / 130 ≈ 46%
        assert result.metrics["max_drawdown_pct"] > 40
        assert result.metrics["max_drawdown_pct"] < 50
