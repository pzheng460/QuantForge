"""
Tests for EventDrivenBacktest engine.

US-3: 向量化回测引擎 (Event-Driven complement)
- EventDrivenBacktest 类逐条处理K线数据
- 支持复杂策略逻辑和状态管理
- 支持多种订单类型（市价、限价）
- 返回权益曲线和交易记录
"""

from datetime import datetime
from typing import Dict, Optional

import numpy as np
import pandas as pd
import pytest

from nexustrader.backtest.engine.event_driven import (
    EventDrivenBacktest,
    BaseStrategy,
    Order,
    OrderType,
    OrderSide,
    Position,
)
from nexustrader.backtest.engine.cost_model import CostConfig
from nexustrader.backtest.result import BacktestConfig, BacktestResult
from nexustrader.constants import KlineInterval


class TestOrderAndPosition:
    """Test Order and Position classes."""

    def test_order_creation(self):
        """Order can be created with required fields."""
        order = Order(
            timestamp=datetime(2024, 1, 1),
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=0.1,
        )
        assert order.side == OrderSide.BUY
        assert order.order_type == OrderType.MARKET
        assert order.amount == 0.1

    def test_limit_order_has_price(self):
        """Limit order includes price."""
        order = Order(
            timestamp=datetime(2024, 1, 1),
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            amount=0.1,
            price=50000.0,
        )
        assert order.price == 50000.0

    def test_position_creation(self):
        """Position tracks size and entry price."""
        position = Position(size=0.1, entry_price=50000.0)
        assert position.size == 0.1
        assert position.entry_price == 50000.0

    def test_position_is_long(self):
        """Position correctly identifies long positions."""
        long_pos = Position(size=0.1, entry_price=50000.0)
        short_pos = Position(size=-0.1, entry_price=50000.0)
        flat_pos = Position(size=0.0, entry_price=0.0)

        assert long_pos.is_long is True
        assert short_pos.is_long is False
        assert flat_pos.is_long is False

    def test_position_is_short(self):
        """Position correctly identifies short positions."""
        long_pos = Position(size=0.1, entry_price=50000.0)
        short_pos = Position(size=-0.1, entry_price=50000.0)
        flat_pos = Position(size=0.0, entry_price=0.0)

        assert short_pos.is_short is True
        assert long_pos.is_short is False
        assert flat_pos.is_short is False

    def test_position_unrealized_pnl(self):
        """Position calculates unrealized PnL correctly."""
        # Long position profit
        long_pos = Position(size=0.1, entry_price=50000.0)
        assert long_pos.unrealized_pnl(51000.0) == pytest.approx(100.0, rel=1e-6)

        # Long position loss
        assert long_pos.unrealized_pnl(49000.0) == pytest.approx(-100.0, rel=1e-6)

        # Short position profit
        short_pos = Position(size=-0.1, entry_price=50000.0)
        assert short_pos.unrealized_pnl(49000.0) == pytest.approx(100.0, rel=1e-6)

        # Short position loss
        assert short_pos.unrealized_pnl(51000.0) == pytest.approx(-100.0, rel=1e-6)


class SimpleStrategy(BaseStrategy):
    """Simple strategy for testing: buy at bar 10, sell at bar 50."""

    def __init__(self, buy_bar: int = 10, sell_bar: int = 50):
        super().__init__()
        self.buy_bar = buy_bar
        self.sell_bar = sell_bar
        self.bar_count = 0

    def on_bar(self, bar: pd.Series, position: Position) -> Optional[Order]:
        """Process a bar and return order if any."""
        self.bar_count += 1

        if self.bar_count == self.buy_bar and position.size == 0:
            return Order(
                timestamp=bar.name,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                amount=1.0,  # Will be adjusted by position sizing
            )
        elif self.bar_count == self.sell_bar and position.size > 0:
            return Order(
                timestamp=bar.name,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                amount=position.size,
            )
        return None


class ShortStrategy(BaseStrategy):
    """Strategy that opens short positions."""

    def __init__(self, short_bar: int = 10, cover_bar: int = 50):
        super().__init__()
        self.short_bar = short_bar
        self.cover_bar = cover_bar
        self.bar_count = 0

    def on_bar(self, bar: pd.Series, position: Position) -> Optional[Order]:
        """Process a bar and return order if any."""
        self.bar_count += 1

        if self.bar_count == self.short_bar and position.size == 0:
            return Order(
                timestamp=bar.name,
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                amount=1.0,
            )
        elif self.bar_count == self.cover_bar and position.size < 0:
            return Order(
                timestamp=bar.name,
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                amount=abs(position.size),
            )
        return None


class LimitOrderStrategy(BaseStrategy):
    """Strategy that uses limit orders."""

    def __init__(self, limit_price: float = 50000.0):
        super().__init__()
        self.limit_price = limit_price
        self.order_placed = False

    def on_bar(self, bar: pd.Series, position: Position) -> Optional[Order]:
        """Place limit order on first bar."""
        if not self.order_placed and position.size == 0:
            self.order_placed = True
            return Order(
                timestamp=bar.name,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                amount=1.0,
                price=self.limit_price,
            )
        return None


class TestBaseStrategy:
    """Test BaseStrategy class."""

    def test_strategy_initialization(self):
        """Strategy initializes correctly."""
        strategy = SimpleStrategy()
        assert strategy.bar_count == 0

    def test_strategy_on_bar_returns_order(self):
        """on_bar can return an Order."""
        strategy = SimpleStrategy(buy_bar=1)
        bar = pd.Series({"open": 50000, "high": 50100, "low": 49900, "close": 50050})
        bar.name = datetime(2024, 1, 1)
        position = Position(size=0.0, entry_price=0.0)

        order = strategy.on_bar(bar, position)

        assert order is not None
        assert order.side == OrderSide.BUY


class TestEventDrivenBacktestBasic:
    """Test basic EventDrivenBacktest functionality."""

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
        """EventDrivenBacktest initializes correctly."""
        strategy = SimpleStrategy()
        bt = EventDrivenBacktest(
            config=backtest_config,
            strategy=strategy,
            cost_config=cost_config,
        )
        assert bt.config == backtest_config
        assert bt.strategy == strategy

    def test_run_returns_backtest_result(self, sample_data, backtest_config, cost_config):
        """run() returns BacktestResult."""
        strategy = SimpleStrategy()
        bt = EventDrivenBacktest(
            config=backtest_config,
            strategy=strategy,
            cost_config=cost_config,
        )

        result = bt.run(data=sample_data)

        assert isinstance(result, BacktestResult)
        assert len(result.equity_curve) == len(sample_data)

    def test_no_trades_with_inactive_strategy(self, sample_data, backtest_config, cost_config):
        """No trades when strategy never triggers."""
        # Strategy that never triggers (buy_bar > data length)
        strategy = SimpleStrategy(buy_bar=200, sell_bar=300)
        bt = EventDrivenBacktest(
            config=backtest_config,
            strategy=strategy,
            cost_config=cost_config,
        )

        result = bt.run(data=sample_data)

        assert len(result.trades) == 0
        assert result.equity_curve.iloc[-1] == backtest_config.initial_capital


class TestEventDrivenBacktestTrading:
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
        strategy = SimpleStrategy(buy_bar=10, sell_bar=90)
        bt = EventDrivenBacktest(
            config=backtest_config,
            strategy=strategy,
            cost_config=cost_config,
        )

        result = bt.run(data=sample_data)

        # Should have trades
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

        strategy = ShortStrategy(short_bar=10, cover_bar=90)
        bt = EventDrivenBacktest(
            config=backtest_config,
            strategy=strategy,
            cost_config=cost_config,
        )

        result = bt.run(data=data)

        # Final equity should be higher than initial (profit from short)
        assert result.equity_curve.iloc[-1] > backtest_config.initial_capital

    def test_trade_records_capture_entry_exit(self, sample_data, backtest_config, cost_config):
        """Trade records capture entry and exit."""
        strategy = SimpleStrategy(buy_bar=10, sell_bar=50)
        bt = EventDrivenBacktest(
            config=backtest_config,
            strategy=strategy,
            cost_config=cost_config,
        )

        result = bt.run(data=sample_data)

        # Should have 2 trade records (entry + exit)
        assert len(result.trades) == 2

        # First trade is entry (buy)
        assert result.trades[0].side == "buy"
        # Second trade is exit (sell)
        assert result.trades[1].side == "sell"


class TestEventDrivenBacktestLimitOrders:
    """Test limit order handling."""

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
        """Create a cost config with zero fees."""
        return CostConfig(
            maker_fee=0.0,
            taker_fee=0.0,
            slippage_pct=0.0,
            use_funding_rate=False,
        )

    def test_limit_buy_fills_when_price_drops(self, backtest_config, cost_config):
        """Limit buy order fills when price drops to limit."""
        # Price starts at 51000, drops to 49000
        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        close = np.linspace(51000, 49000, 100)
        data = pd.DataFrame({
            "open": close + 50,
            "high": close + 100,
            "low": close - 100,
            "close": close,
            "volume": np.full(100, 500.0),
        }, index=dates)

        strategy = LimitOrderStrategy(limit_price=50000.0)
        bt = EventDrivenBacktest(
            config=backtest_config,
            strategy=strategy,
            cost_config=cost_config,
        )

        result = bt.run(data=data)

        # Should have filled the limit order
        assert len(result.trades) >= 1
        # Entry should be at or near limit price
        assert result.trades[0].price <= 50000.0

    def test_limit_buy_no_fill_when_price_above(self, backtest_config, cost_config):
        """Limit buy order doesn't fill when price stays above."""
        # Price stays above 50000
        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        close = np.linspace(51000, 52000, 100)
        data = pd.DataFrame({
            "open": close - 50,
            "high": close + 100,
            "low": close - 100,
            "close": close,
            "volume": np.full(100, 500.0),
        }, index=dates)

        strategy = LimitOrderStrategy(limit_price=50000.0)
        bt = EventDrivenBacktest(
            config=backtest_config,
            strategy=strategy,
            cost_config=cost_config,
        )

        result = bt.run(data=data)

        # Should not have filled the limit order
        assert len(result.trades) == 0


class TestEventDrivenBacktestCosts:
    """Test cost calculations in event-driven backtest."""

    @pytest.fixture
    def sample_data(self):
        """Create sample data with constant price."""
        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        close = np.full(100, 50000.0)
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
        cost_with_fees = CostConfig(
            maker_fee=0.0,
            taker_fee=0.001,
            slippage_pct=0.0,
            use_funding_rate=False,
        )
        cost_no_fees = CostConfig(
            maker_fee=0.0,
            taker_fee=0.0,
            slippage_pct=0.0,
            use_funding_rate=False,
        )

        strategy_fees = SimpleStrategy(buy_bar=10, sell_bar=50)
        strategy_no_fees = SimpleStrategy(buy_bar=10, sell_bar=50)

        bt_with_fees = EventDrivenBacktest(
            config=backtest_config,
            strategy=strategy_fees,
            cost_config=cost_with_fees,
        )
        bt_no_fees = EventDrivenBacktest(
            config=backtest_config,
            strategy=strategy_no_fees,
            cost_config=cost_no_fees,
        )

        result_with_fees = bt_with_fees.run(data=sample_data)
        result_no_fees = bt_no_fees.run(data=sample_data)

        # With fees should have less equity
        assert result_with_fees.equity_curve.iloc[-1] < result_no_fees.equity_curve.iloc[-1]


class TestEventDrivenBacktestMetrics:
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
        strategy = SimpleStrategy(buy_bar=10, sell_bar=50)
        bt = EventDrivenBacktest(
            config=backtest_config,
            strategy=strategy,
            cost_config=cost_config,
        )

        result = bt.run(data=sample_data)

        assert "total_return_pct" in result.metrics
        assert "max_drawdown_pct" in result.metrics
        assert "total_trades" in result.metrics

    def test_equity_curve_length_matches_data(self, sample_data, backtest_config, cost_config):
        """Equity curve has same length as input data."""
        strategy = SimpleStrategy(buy_bar=10, sell_bar=50)
        bt = EventDrivenBacktest(
            config=backtest_config,
            strategy=strategy,
            cost_config=cost_config,
        )

        result = bt.run(data=sample_data)

        assert len(result.equity_curve) == len(sample_data)
