"""
Tests for CostModel (trading cost simulation).

Part of US-3: 向量化回测引擎
- 计算手续费（maker/taker 可配置）
- 计算资金费率（每 8 小时结算）
- 模拟滑点（固定 + ATR 动态）
"""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from quantforge.backtest.engine.cost_model import CostModel, CostConfig


class TestCostConfig:
    """Test CostConfig dataclass."""

    def test_default_values(self):
        """CostConfig has sensible defaults."""
        config = CostConfig()
        assert config.maker_fee == 0.0002
        assert config.taker_fee == 0.0005
        assert config.slippage_pct == 0.0005
        assert config.use_funding_rate is True

    def test_custom_values(self):
        """CostConfig accepts custom values."""
        config = CostConfig(
            maker_fee=0.0001,
            taker_fee=0.0003,
            slippage_pct=0.001,
            use_funding_rate=False,
        )
        assert config.maker_fee == 0.0001
        assert config.taker_fee == 0.0003
        assert config.slippage_pct == 0.001
        assert config.use_funding_rate is False


class TestCostModelFees:
    """Test fee calculations."""

    @pytest.fixture
    def cost_model(self):
        """Create a CostModel with default config."""
        return CostModel(CostConfig())

    def test_calculate_maker_fee(self, cost_model):
        """Calculate maker fee correctly."""
        # 10000 USDT trade value * 0.02% fee = 2 USDT
        fee = cost_model.calculate_fee(
            trade_value=10000.0,
            is_maker=True,
        )
        assert fee == pytest.approx(2.0, rel=1e-6)

    def test_calculate_taker_fee(self, cost_model):
        """Calculate taker fee correctly."""
        # 10000 USDT trade value * 0.05% fee = 5 USDT
        fee = cost_model.calculate_fee(
            trade_value=10000.0,
            is_maker=False,
        )
        assert fee == pytest.approx(5.0, rel=1e-6)

    def test_fee_for_zero_value(self, cost_model):
        """Fee is zero for zero trade value."""
        fee = cost_model.calculate_fee(trade_value=0.0, is_maker=True)
        assert fee == 0.0


class TestCostModelSlippage:
    """Test slippage calculations."""

    @pytest.fixture
    def cost_model(self):
        """Create a CostModel with default config."""
        return CostModel(CostConfig(slippage_pct=0.0005))

    def test_calculate_fixed_slippage_buy(self, cost_model):
        """Calculate fixed slippage for buy order."""
        # Buy at 50000, slippage 0.05% = 50025
        adjusted_price = cost_model.apply_slippage(
            price=50000.0,
            is_buy=True,
        )
        assert adjusted_price == pytest.approx(50025.0, rel=1e-6)

    def test_calculate_fixed_slippage_sell(self, cost_model):
        """Calculate fixed slippage for sell order."""
        # Sell at 50000, slippage 0.05% = 49975
        adjusted_price = cost_model.apply_slippage(
            price=50000.0,
            is_buy=False,
        )
        assert adjusted_price == pytest.approx(49975.0, rel=1e-6)

    def test_calculate_atr_slippage(self, cost_model):
        """Calculate ATR-based slippage."""
        # Buy at 50000 with ATR 500, multiplier 0.1 = 50050
        adjusted_price = cost_model.apply_slippage(
            price=50000.0,
            is_buy=True,
            atr=500.0,
            atr_multiplier=0.1,
        )
        # Fixed slippage (25) + ATR slippage (50) = 50075
        assert adjusted_price == pytest.approx(50075.0, rel=1e-6)

    def test_zero_slippage_config(self):
        """Zero slippage when configured."""
        cost_model = CostModel(CostConfig(slippage_pct=0.0))
        adjusted_price = cost_model.apply_slippage(
            price=50000.0,
            is_buy=True,
        )
        assert adjusted_price == 50000.0


class TestCostModelFundingRate:
    """Test funding rate calculations."""

    @pytest.fixture
    def cost_model(self):
        """Create a CostModel with funding rate enabled."""
        return CostModel(CostConfig(use_funding_rate=True))

    def test_calculate_funding_payment_long(self, cost_model):
        """Calculate funding payment for long position."""
        # Long 1 BTC at 50000, funding rate 0.01% = -5 USDT (pay)
        payment = cost_model.calculate_funding_payment(
            position_value=50000.0,
            is_long=True,
            funding_rate=0.0001,
        )
        assert payment == pytest.approx(-5.0, rel=1e-6)

    def test_calculate_funding_payment_short(self, cost_model):
        """Calculate funding payment for short position."""
        # Short 1 BTC at 50000, funding rate 0.01% = +5 USDT (receive)
        payment = cost_model.calculate_funding_payment(
            position_value=50000.0,
            is_long=False,
            funding_rate=0.0001,
        )
        assert payment == pytest.approx(5.0, rel=1e-6)

    def test_negative_funding_rate(self, cost_model):
        """Handle negative funding rates (shorts pay)."""
        # Long 1 BTC at 50000, funding rate -0.01% = +5 USDT (receive)
        payment = cost_model.calculate_funding_payment(
            position_value=50000.0,
            is_long=True,
            funding_rate=-0.0001,
        )
        assert payment == pytest.approx(5.0, rel=1e-6)

    def test_funding_disabled(self):
        """Funding payment is zero when disabled."""
        cost_model = CostModel(CostConfig(use_funding_rate=False))
        payment = cost_model.calculate_funding_payment(
            position_value=50000.0,
            is_long=True,
            funding_rate=0.0001,
        )
        assert payment == 0.0

    def test_zero_position(self, cost_model):
        """Funding payment is zero for zero position."""
        payment = cost_model.calculate_funding_payment(
            position_value=0.0,
            is_long=True,
            funding_rate=0.0001,
        )
        assert payment == 0.0


class TestCostModelLimitOrderFill:
    """Test limit order fill probability model."""

    @pytest.fixture
    def cost_model(self):
        """Create a CostModel."""
        return CostModel(CostConfig())

    def test_limit_buy_fills_when_price_crosses(self, cost_model):
        """Limit buy order fills when price goes below limit."""
        fills = cost_model.check_limit_fill(
            limit_price=50000.0,
            is_buy=True,
            candle_low=49900.0,
            candle_high=50100.0,
        )
        assert fills is True

    def test_limit_buy_no_fill_when_price_above(self, cost_model):
        """Limit buy order doesn't fill when price stays above."""
        fills = cost_model.check_limit_fill(
            limit_price=50000.0,
            is_buy=True,
            candle_low=50100.0,
            candle_high=50200.0,
        )
        assert fills is False

    def test_limit_sell_fills_when_price_crosses(self, cost_model):
        """Limit sell order fills when price goes above limit."""
        fills = cost_model.check_limit_fill(
            limit_price=50000.0,
            is_buy=False,
            candle_low=49900.0,
            candle_high=50100.0,
        )
        assert fills is True

    def test_limit_sell_no_fill_when_price_below(self, cost_model):
        """Limit sell order doesn't fill when price stays below."""
        fills = cost_model.check_limit_fill(
            limit_price=50000.0,
            is_buy=False,
            candle_low=49800.0,
            candle_high=49900.0,
        )
        assert fills is False


class TestCostModelTotalCost:
    """Test total cost calculation."""

    @pytest.fixture
    def cost_model(self):
        """Create a CostModel with default config."""
        return CostModel(CostConfig())

    def test_calculate_total_cost_buy(self, cost_model):
        """Calculate total cost for a buy trade."""
        # Price 50000, amount 0.1 BTC
        # Slippage: 50000 * 0.0005 = 25 -> adjusted price = 50025
        # Trade value: 50025 * 0.1 = 5002.5
        # Taker fee: 5002.5 * 0.0005 = 2.50125
        result = cost_model.calculate_total_cost(
            price=50000.0,
            amount=0.1,
            is_buy=True,
            is_maker=False,
        )

        assert result["adjusted_price"] == pytest.approx(50025.0, rel=1e-4)
        assert result["trade_value"] == pytest.approx(5002.5, rel=1e-4)
        assert result["fee"] == pytest.approx(2.50125, rel=1e-4)
        assert result["total_cost"] == pytest.approx(5002.5 + 2.50125, rel=1e-4)
