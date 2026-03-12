"""
Tests for ReportGenerator.

US-6: 回测报告生成
- ReportGenerator 生成交互式 HTML 报告
- 包含权益曲线图、回撤图、交易分布等可视化
- 支持导出为 HTML 文件
"""

from datetime import datetime
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd
import pytest

from quantforge.backtest.analysis.report import ReportGenerator
from quantforge.backtest.result import BacktestConfig, BacktestResult, TradeRecord
from quantforge.constants import KlineInterval


class TestReportGeneratorBasic:
    """Test basic ReportGenerator functionality."""

    @pytest.fixture
    def backtest_result(self):
        """Create sample backtest result."""
        config = BacktestConfig(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 7),
            initial_capital=10000.0,
        )

        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        np.random.seed(42)
        returns = np.random.randn(100) * 0.001 + 0.0001
        equity = 10000 * np.cumprod(1 + returns)
        equity_curve = pd.Series(equity, index=dates)

        trades = [
            TradeRecord(
                timestamp=datetime(2024, 1, 1, 1, 0),
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
                timestamp=datetime(2024, 1, 1, 2, 0),
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
        ]

        metrics = {
            "total_return_pct": 5.0,
            "max_drawdown_pct": 2.0,
            "sharpe_ratio": 1.5,
            "sortino_ratio": 2.0,
            "win_rate_pct": 60.0,
            "profit_factor": 1.8,
            "total_trades": 1,
        }

        return BacktestResult(
            config=config,
            equity_curve=equity_curve,
            trades=trades,
            metrics=metrics,
            run_time=datetime.now(),
            duration_seconds=0.5,
        )

    def test_generator_initialization(self, backtest_result):
        """ReportGenerator initializes correctly."""
        generator = ReportGenerator(backtest_result)
        assert generator.result == backtest_result

    def test_generate_returns_html(self, backtest_result):
        """generate() returns HTML string."""
        generator = ReportGenerator(backtest_result)
        html = generator.generate()

        assert isinstance(html, str)
        assert len(html) > 0
        assert "<html" in html.lower()

    def test_html_contains_title(self, backtest_result):
        """HTML contains backtest title."""
        generator = ReportGenerator(backtest_result)
        html = generator.generate()

        assert "BTC/USDT" in html

    def test_html_contains_metrics(self, backtest_result):
        """HTML contains performance metrics."""
        generator = ReportGenerator(backtest_result)
        html = generator.generate()

        assert "5.0" in html or "5%" in html  # Total return
        assert "Sharpe" in html or "sharpe" in html


class TestReportGeneratorCharts:
    """Test chart generation."""

    @pytest.fixture
    def backtest_result(self):
        """Create sample backtest result with more data."""
        config = BacktestConfig(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 7),
            initial_capital=10000.0,
        )

        dates = pd.date_range("2024-01-01", periods=500, freq="15min")
        np.random.seed(42)
        returns = np.random.randn(500) * 0.001 + 0.0001
        equity = 10000 * np.cumprod(1 + returns)
        equity_curve = pd.Series(equity, index=dates)

        trades = []
        metrics = {
            "total_return_pct": 5.0,
            "max_drawdown_pct": 2.0,
            "sharpe_ratio": 1.5,
            "total_trades": 0,
        }

        return BacktestResult(
            config=config,
            equity_curve=equity_curve,
            trades=trades,
            metrics=metrics,
            run_time=datetime.now(),
            duration_seconds=0.5,
        )

    def test_html_contains_equity_chart(self, backtest_result):
        """HTML contains equity curve chart."""
        generator = ReportGenerator(backtest_result)
        html = generator.generate()

        # Should reference chart/visualization
        assert "equity" in html.lower() or "chart" in html.lower() or "plotly" in html.lower()

    def test_html_contains_drawdown_chart(self, backtest_result):
        """HTML contains drawdown chart."""
        generator = ReportGenerator(backtest_result)
        html = generator.generate()

        # Should reference drawdown
        assert "drawdown" in html.lower()


class TestReportGeneratorExport:
    """Test report export functionality."""

    @pytest.fixture
    def backtest_result(self):
        """Create sample backtest result."""
        config = BacktestConfig(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 7),
            initial_capital=10000.0,
        )

        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        equity = pd.Series(np.linspace(10000, 10500, 100), index=dates)

        return BacktestResult(
            config=config,
            equity_curve=equity,
            trades=[],
            metrics={"total_return_pct": 5.0, "max_drawdown_pct": 1.0, "total_trades": 0},
            run_time=datetime.now(),
            duration_seconds=0.5,
        )

    def test_save_to_file(self, backtest_result):
        """save() writes HTML to file."""
        generator = ReportGenerator(backtest_result)

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "report.html"
            generator.save(filepath)

            assert filepath.exists()
            content = filepath.read_text()
            assert "<html" in content.lower()

    def test_save_creates_parent_directories(self, backtest_result):
        """save() creates parent directories if needed."""
        generator = ReportGenerator(backtest_result)

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "subdir" / "report.html"
            generator.save(filepath)

            assert filepath.exists()


class TestReportGeneratorTradeAnalysis:
    """Test trade analysis in reports."""

    @pytest.fixture
    def backtest_result_with_trades(self):
        """Create backtest result with multiple trades."""
        config = BacktestConfig(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 7),
            initial_capital=10000.0,
        )

        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        equity = pd.Series(np.linspace(10000, 10500, 100), index=dates)

        trades = [
            TradeRecord(
                timestamp=datetime(2024, 1, 1, 1, 0),
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
                timestamp=datetime(2024, 1, 1, 2, 0),
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
                timestamp=datetime(2024, 1, 1, 3, 0),
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
                timestamp=datetime(2024, 1, 1, 4, 0),
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

        metrics = {
            "total_return_pct": 0.3,
            "max_drawdown_pct": 0.15,
            "sharpe_ratio": 0.5,
            "total_trades": 2,
            "win_rate_pct": 50.0,
        }

        return BacktestResult(
            config=config,
            equity_curve=equity,
            trades=trades,
            metrics=metrics,
            run_time=datetime.now(),
            duration_seconds=0.5,
        )

    def test_html_contains_trade_list(self, backtest_result_with_trades):
        """HTML contains trade list or table."""
        generator = ReportGenerator(backtest_result_with_trades)
        html = generator.generate()

        # Should contain trade information
        assert "trade" in html.lower()

    def test_html_contains_win_rate(self, backtest_result_with_trades):
        """HTML contains win rate statistic."""
        generator = ReportGenerator(backtest_result_with_trades)
        html = generator.generate()

        assert "win" in html.lower() or "50" in html


class TestReportGeneratorEdgeCases:
    """Test edge cases."""

    def test_empty_trades(self):
        """Handle result with no trades."""
        config = BacktestConfig(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 7),
            initial_capital=10000.0,
        )

        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        equity = pd.Series(np.full(100, 10000.0), index=dates)

        result = BacktestResult(
            config=config,
            equity_curve=equity,
            trades=[],
            metrics={"total_return_pct": 0.0, "max_drawdown_pct": 0.0, "total_trades": 0},
            run_time=datetime.now(),
            duration_seconds=0.5,
        )

        generator = ReportGenerator(result)
        html = generator.generate()

        assert isinstance(html, str)
        assert "<html" in html.lower()

    def test_single_data_point(self):
        """Handle result with minimal data."""
        config = BacktestConfig(
            symbol="BTC/USDT",
            interval=KlineInterval.MINUTE_15,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 1),
            initial_capital=10000.0,
        )

        dates = pd.date_range("2024-01-01", periods=1, freq="15min")
        equity = pd.Series([10000.0], index=dates)

        result = BacktestResult(
            config=config,
            equity_curve=equity,
            trades=[],
            metrics={"total_return_pct": 0.0, "max_drawdown_pct": 0.0, "total_trades": 0},
            run_time=datetime.now(),
            duration_seconds=0.1,
        )

        generator = ReportGenerator(result)
        html = generator.generate()

        assert isinstance(html, str)
