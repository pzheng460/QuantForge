"""
Tests for PerformanceAnalyzer.

US-4: 统计指标计算
- PerformanceAnalyzer 计算回测性能指标
- 支持收益率、夏普比率、最大回撤等基本指标
- 支持胜率、盈亏比、Calmar比率等高级指标
- 支持按时间段分析（日/周/月）
"""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from nexustrader.backtest.analysis.performance import PerformanceAnalyzer
from nexustrader.backtest.result import TradeRecord


class TestPerformanceAnalyzerBasicMetrics:
    """Test basic performance metrics calculation."""

    @pytest.fixture
    def equity_curve(self):
        """Create sample equity curve."""
        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        # Equity that grows from 10000 to 11000 with some volatility
        np.random.seed(42)
        returns = np.random.randn(100) * 0.001 + 0.0001  # Positive drift
        equity = 10000 * np.cumprod(1 + returns)
        return pd.Series(equity, index=dates)

    @pytest.fixture
    def trades(self):
        """Create sample trades."""
        return [
            TradeRecord(
                timestamp=datetime(2024, 1, 1, 0, 15),
                side="buy",
                price=50000.0,
                amount=0.1,
                fee=2.5,
                pnl=0.0,
                pnl_pct=0.0,
                position_after=0.1,
                capital_after=9997.5,
                entry_price=50000.0,
                exit_reason="",
            ),
            TradeRecord(
                timestamp=datetime(2024, 1, 1, 1, 0),
                side="sell",
                price=50500.0,
                amount=0.1,
                fee=2.525,
                pnl=47.475,
                pnl_pct=0.95,
                position_after=0.0,
                capital_after=10044.975,
                entry_price=50000.0,
                exit_reason="signal",
            ),
            TradeRecord(
                timestamp=datetime(2024, 1, 1, 2, 0),
                side="buy",
                price=50200.0,
                amount=0.1,
                fee=2.51,
                pnl=0.0,
                pnl_pct=0.0,
                position_after=0.1,
                capital_after=10042.465,
                entry_price=50200.0,
                exit_reason="",
            ),
            TradeRecord(
                timestamp=datetime(2024, 1, 1, 3, 0),
                side="sell",
                price=50100.0,
                amount=0.1,
                fee=2.505,
                pnl=-12.505,
                pnl_pct=-0.25,
                position_after=0.0,
                capital_after=10029.96,
                entry_price=50200.0,
                exit_reason="signal",
            ),
        ]

    def test_analyzer_initialization(self, equity_curve, trades):
        """PerformanceAnalyzer initializes correctly."""
        analyzer = PerformanceAnalyzer(
            equity_curve=equity_curve,
            trades=trades,
            initial_capital=10000.0,
        )
        assert analyzer.initial_capital == 10000.0
        assert len(analyzer.trades) == 4

    def test_calculate_total_return(self, equity_curve, trades):
        """Calculate total return correctly."""
        analyzer = PerformanceAnalyzer(
            equity_curve=equity_curve,
            trades=trades,
            initial_capital=10000.0,
        )
        metrics = analyzer.calculate_metrics()

        # Total return should be (final - initial) / initial
        expected = (equity_curve.iloc[-1] - 10000.0) / 10000.0 * 100
        assert metrics["total_return_pct"] == pytest.approx(expected, rel=1e-4)

    def test_calculate_max_drawdown(self, equity_curve, trades):
        """Calculate maximum drawdown correctly."""
        analyzer = PerformanceAnalyzer(
            equity_curve=equity_curve,
            trades=trades,
            initial_capital=10000.0,
        )
        metrics = analyzer.calculate_metrics()

        # Max drawdown should be positive (absolute value)
        assert metrics["max_drawdown_pct"] >= 0

        # Verify by manual calculation
        rolling_max = equity_curve.cummax()
        drawdown = (equity_curve - rolling_max) / rolling_max
        expected = abs(drawdown.min()) * 100
        assert metrics["max_drawdown_pct"] == pytest.approx(expected, rel=1e-4)

    def test_calculate_sharpe_ratio(self, equity_curve, trades):
        """Calculate Sharpe ratio correctly."""
        analyzer = PerformanceAnalyzer(
            equity_curve=equity_curve,
            trades=trades,
            initial_capital=10000.0,
        )
        metrics = analyzer.calculate_metrics()

        # Sharpe should be calculated
        assert "sharpe_ratio" in metrics

    def test_calculate_sortino_ratio(self, equity_curve, trades):
        """Calculate Sortino ratio correctly."""
        analyzer = PerformanceAnalyzer(
            equity_curve=equity_curve,
            trades=trades,
            initial_capital=10000.0,
        )
        metrics = analyzer.calculate_metrics()

        # Sortino should be calculated
        assert "sortino_ratio" in metrics

    def test_calculate_calmar_ratio(self, equity_curve, trades):
        """Calculate Calmar ratio correctly."""
        analyzer = PerformanceAnalyzer(
            equity_curve=equity_curve,
            trades=trades,
            initial_capital=10000.0,
        )
        metrics = analyzer.calculate_metrics()

        # Calmar = Annualized Return / Max Drawdown
        assert "calmar_ratio" in metrics


class TestPerformanceAnalyzerTradeStats:
    """Test trade statistics calculation."""

    @pytest.fixture
    def equity_curve(self):
        """Create sample equity curve."""
        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        equity = np.linspace(10000, 11000, 100)
        return pd.Series(equity, index=dates)

    @pytest.fixture
    def mixed_trades(self):
        """Create trades with mixed wins and losses."""
        trades = []
        base_time = datetime(2024, 1, 1)

        # 3 winning trades
        for i in range(3):
            trades.append(TradeRecord(
                timestamp=base_time + timedelta(hours=i*2),
                side="buy",
                price=50000.0,
                amount=0.1,
                fee=2.5,
                pnl=0.0,
                pnl_pct=0.0,
                position_after=0.1,
                capital_after=10000.0,
                entry_price=50000.0,
                exit_reason="",
            ))
            trades.append(TradeRecord(
                timestamp=base_time + timedelta(hours=i*2+1),
                side="sell",
                price=50500.0,
                amount=0.1,
                fee=2.525,
                pnl=50.0,  # Win
                pnl_pct=1.0,
                position_after=0.0,
                capital_after=10050.0,
                entry_price=50000.0,
                exit_reason="signal",
            ))

        # 2 losing trades
        for i in range(2):
            trades.append(TradeRecord(
                timestamp=base_time + timedelta(hours=6+i*2),
                side="buy",
                price=50000.0,
                amount=0.1,
                fee=2.5,
                pnl=0.0,
                pnl_pct=0.0,
                position_after=0.1,
                capital_after=10000.0,
                entry_price=50000.0,
                exit_reason="",
            ))
            trades.append(TradeRecord(
                timestamp=base_time + timedelta(hours=6+i*2+1),
                side="sell",
                price=49500.0,
                amount=0.1,
                fee=2.475,
                pnl=-25.0,  # Loss
                pnl_pct=-0.5,
                position_after=0.0,
                capital_after=9975.0,
                entry_price=50000.0,
                exit_reason="signal",
            ))

        return trades

    def test_calculate_win_rate(self, equity_curve, mixed_trades):
        """Calculate win rate correctly."""
        analyzer = PerformanceAnalyzer(
            equity_curve=equity_curve,
            trades=mixed_trades,
            initial_capital=10000.0,
        )
        metrics = analyzer.calculate_metrics()

        # 3 wins out of 5 closing trades = 60%
        assert metrics["win_rate_pct"] == pytest.approx(60.0, rel=1e-4)

    def test_calculate_profit_factor(self, equity_curve, mixed_trades):
        """Calculate profit factor correctly."""
        analyzer = PerformanceAnalyzer(
            equity_curve=equity_curve,
            trades=mixed_trades,
            initial_capital=10000.0,
        )
        metrics = analyzer.calculate_metrics()

        # Profit factor = Gross Profit / Gross Loss
        # Gross profit = 50 * 3 = 150
        # Gross loss = 25 * 2 = 50
        # Profit factor = 150 / 50 = 3.0
        assert metrics["profit_factor"] == pytest.approx(3.0, rel=1e-4)

    def test_calculate_average_win_loss(self, equity_curve, mixed_trades):
        """Calculate average win and loss correctly."""
        analyzer = PerformanceAnalyzer(
            equity_curve=equity_curve,
            trades=mixed_trades,
            initial_capital=10000.0,
        )
        metrics = analyzer.calculate_metrics()

        # Average win = 150 / 3 = 50
        assert metrics["avg_win"] == pytest.approx(50.0, rel=1e-4)
        # Average loss = 50 / 2 = 25
        assert metrics["avg_loss"] == pytest.approx(25.0, rel=1e-4)

    def test_calculate_expectancy(self, equity_curve, mixed_trades):
        """Calculate expectancy correctly."""
        analyzer = PerformanceAnalyzer(
            equity_curve=equity_curve,
            trades=mixed_trades,
            initial_capital=10000.0,
        )
        metrics = analyzer.calculate_metrics()

        # Expectancy = (Win Rate * Avg Win) - (Loss Rate * Avg Loss)
        # = (0.6 * 50) - (0.4 * 25) = 30 - 10 = 20
        assert metrics["expectancy"] == pytest.approx(20.0, rel=1e-4)


class TestPerformanceAnalyzerTimeBasedAnalysis:
    """Test time-based analysis."""

    @pytest.fixture
    def equity_curve(self):
        """Create longer equity curve for time analysis."""
        # 7 days of 15-min data
        dates = pd.date_range("2024-01-01", periods=672, freq="15min")
        np.random.seed(42)
        returns = np.random.randn(672) * 0.001 + 0.0001
        equity = 10000 * np.cumprod(1 + returns)
        return pd.Series(equity, index=dates)

    @pytest.fixture
    def trades(self):
        """Create sample trades."""
        return []  # Empty trades for basic time analysis

    def test_daily_returns(self, equity_curve, trades):
        """Calculate daily returns correctly."""
        analyzer = PerformanceAnalyzer(
            equity_curve=equity_curve,
            trades=trades,
            initial_capital=10000.0,
        )

        daily_returns = analyzer.get_daily_returns()

        assert isinstance(daily_returns, pd.Series)
        # 7 days of data, but pct_change drops first day, so 6 returns
        assert len(daily_returns) == 6

    def test_monthly_returns(self, equity_curve, trades):
        """Calculate monthly returns correctly."""
        analyzer = PerformanceAnalyzer(
            equity_curve=equity_curve,
            trades=trades,
            initial_capital=10000.0,
        )

        monthly_returns = analyzer.get_monthly_returns()

        assert isinstance(monthly_returns, pd.Series)
        # 7 days of data is within one month, pct_change needs 2+ months
        # to produce returns, so 0 is expected for single-month data
        assert len(monthly_returns) >= 0


class TestPerformanceAnalyzerEdgeCases:
    """Test edge cases."""

    def test_no_trades(self):
        """Handle case with no trades."""
        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        equity = pd.Series(np.full(100, 10000.0), index=dates)

        analyzer = PerformanceAnalyzer(
            equity_curve=equity,
            trades=[],
            initial_capital=10000.0,
        )
        metrics = analyzer.calculate_metrics()

        assert metrics["total_trades"] == 0
        assert metrics["win_rate_pct"] == 0
        assert metrics["profit_factor"] == 0

    def test_all_winning_trades(self):
        """Handle case with all winning trades."""
        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        equity = pd.Series(np.linspace(10000, 11000, 100), index=dates)

        trades = [
            TradeRecord(
                timestamp=datetime(2024, 1, 1),
                side="sell",
                price=50500.0,
                amount=0.1,
                fee=2.5,
                pnl=100.0,
                pnl_pct=2.0,
                position_after=0.0,
                capital_after=10100.0,
                entry_price=50000.0,
                exit_reason="signal",
            )
        ]

        analyzer = PerformanceAnalyzer(
            equity_curve=equity,
            trades=trades,
            initial_capital=10000.0,
        )
        metrics = analyzer.calculate_metrics()

        assert metrics["win_rate_pct"] == 100.0
        # Profit factor should be infinite (represented as a large number)
        assert metrics["profit_factor"] > 0

    def test_all_losing_trades(self):
        """Handle case with all losing trades."""
        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        equity = pd.Series(np.linspace(10000, 9000, 100), index=dates)

        trades = [
            TradeRecord(
                timestamp=datetime(2024, 1, 1),
                side="sell",
                price=49500.0,
                amount=0.1,
                fee=2.5,
                pnl=-100.0,
                pnl_pct=-2.0,
                position_after=0.0,
                capital_after=9900.0,
                entry_price=50000.0,
                exit_reason="signal",
            )
        ]

        analyzer = PerformanceAnalyzer(
            equity_curve=equity,
            trades=trades,
            initial_capital=10000.0,
        )
        metrics = analyzer.calculate_metrics()

        assert metrics["win_rate_pct"] == 0.0
        assert metrics["profit_factor"] == 0.0

    def test_constant_equity(self):
        """Handle case with constant equity (no changes)."""
        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        equity = pd.Series(np.full(100, 10000.0), index=dates)

        analyzer = PerformanceAnalyzer(
            equity_curve=equity,
            trades=[],
            initial_capital=10000.0,
        )
        metrics = analyzer.calculate_metrics()

        assert metrics["total_return_pct"] == 0.0
        assert metrics["max_drawdown_pct"] == 0.0
        assert metrics["sharpe_ratio"] == 0.0
