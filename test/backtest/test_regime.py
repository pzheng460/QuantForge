"""
Tests for RegimeClassifier.

US-10: 市场状态分类
- RegimeClassifier 根据价格走势分类市场状态
- 识别三种状态：牛市（上涨 > 20%）、熊市（下跌 > 20%）、震荡（其他）
- 分别统计各状态下的策略表现
- 输出分状态的性能报告
"""

import numpy as np
import pandas as pd
import pytest

from nexustrader.backtest.analysis.regime import (
    RegimeClassifier,
    MarketRegime,
    SimpleRegime,
)


class TestMarketRegimeEnum:
    """Test MarketRegime enum."""

    def test_regime_values(self):
        """MarketRegime enum has expected values."""
        assert MarketRegime.TRENDING_UP.value == "trending_up"
        assert MarketRegime.TRENDING_DOWN.value == "trending_down"
        assert MarketRegime.RANGING.value == "ranging"
        assert MarketRegime.HIGH_VOLATILITY.value == "high_volatility"


class TestRegimeClassifierBasic:
    """Test basic RegimeClassifier functionality."""

    @pytest.fixture
    def sample_data(self):
        """Create sample OHLCV data."""
        dates = pd.date_range("2024-01-01", periods=500, freq="15min")
        np.random.seed(42)
        close = 50000 + np.cumsum(np.random.randn(500) * 100)
        return pd.DataFrame({
            "open": close - np.random.uniform(0, 50, 500),
            "high": close + np.random.uniform(0, 100, 500),
            "low": close - np.random.uniform(0, 100, 500),
            "close": close,
            "volume": np.random.uniform(100, 1000, 500),
        }, index=dates)

    def test_classifier_initialization(self):
        """RegimeClassifier initializes correctly."""
        classifier = RegimeClassifier()
        assert classifier is not None

    def test_classifier_with_custom_params(self):
        """RegimeClassifier accepts custom parameters."""
        classifier = RegimeClassifier(
            trend_threshold=0.02,
            volatility_lookback=20,
            volatility_threshold=2.0,
        )
        assert classifier.trend_threshold == 0.02
        assert classifier.volatility_lookback == 20
        assert classifier.volatility_threshold == 2.0

    def test_classify_returns_series(self, sample_data):
        """classify() returns a Series of regimes."""
        classifier = RegimeClassifier()

        regimes = classifier.classify(sample_data)

        assert isinstance(regimes, pd.Series)
        assert len(regimes) == len(sample_data)

    def test_regimes_are_valid_values(self, sample_data):
        """All classified regimes are valid MarketRegime values."""
        classifier = RegimeClassifier()

        regimes = classifier.classify(sample_data)

        valid_values = [r.value for r in MarketRegime]
        for regime in regimes.unique():
            assert regime in valid_values


class TestRegimeClassifierTrending:
    """Test trend detection."""

    def test_detect_uptrend(self):
        """Detect upward trending market."""
        dates = pd.date_range("2024-01-01", periods=200, freq="15min")
        # Strong uptrend
        close = np.linspace(50000, 55000, 200)  # 10% increase
        data = pd.DataFrame({
            "open": close - 10,
            "high": close + 20,
            "low": close - 20,
            "close": close,
            "volume": np.full(200, 500.0),
        }, index=dates)

        # Use lower threshold to detect smaller per-bar movements
        # 10% over 200 bars = ~0.05% per bar, 20-bar lookback = ~1%
        classifier = RegimeClassifier(trend_threshold=0.005, trend_lookback=20)
        regimes = classifier.classify(data)

        # Should detect some uptrend periods after warmup
        trending_up_count = (regimes == MarketRegime.TRENDING_UP.value).sum()
        assert trending_up_count > 0

    def test_detect_downtrend(self):
        """Detect downward trending market."""
        dates = pd.date_range("2024-01-01", periods=200, freq="15min")
        # Strong downtrend
        close = np.linspace(55000, 50000, 200)  # 10% decrease
        data = pd.DataFrame({
            "open": close + 10,
            "high": close + 20,
            "low": close - 20,
            "close": close,
            "volume": np.full(200, 500.0),
        }, index=dates)

        # Use lower threshold to detect smaller per-bar movements
        classifier = RegimeClassifier(trend_threshold=0.005, trend_lookback=20)
        regimes = classifier.classify(data)

        # Should detect some downtrend periods after warmup
        trending_down_count = (regimes == MarketRegime.TRENDING_DOWN.value).sum()
        assert trending_down_count > 0


class TestRegimeClassifierRanging:
    """Test ranging market detection."""

    def test_detect_ranging_market(self):
        """Detect ranging/sideways market."""
        dates = pd.date_range("2024-01-01", periods=200, freq="15min")
        # Oscillating price with no clear trend
        np.random.seed(42)
        close = 50000 + np.sin(np.linspace(0, 4 * np.pi, 200)) * 100 + np.random.randn(200) * 10
        data = pd.DataFrame({
            "open": close - 5,
            "high": close + 10,
            "low": close - 10,
            "close": close,
            "volume": np.full(200, 500.0),
        }, index=dates)

        classifier = RegimeClassifier(trend_threshold=0.02)
        regimes = classifier.classify(data)

        # Should detect some ranging periods
        ranging_count = (regimes == MarketRegime.RANGING.value).sum()
        assert ranging_count > 0


class TestRegimeClassifierVolatility:
    """Test volatility detection."""

    def test_detect_high_volatility(self):
        """Detect high volatility periods."""
        dates = pd.date_range("2024-01-01", periods=200, freq="15min")
        np.random.seed(42)
        # Create data with varying volatility: low vol then high vol
        close_low = 50000 + np.cumsum(np.random.randn(100) * 50)  # Low volatility
        close_high = close_low[-1] + np.cumsum(np.random.randn(100) * 500)  # High volatility
        close = np.concatenate([close_low, close_high])
        data = pd.DataFrame({
            "open": close - np.random.uniform(0, 50, 200),
            "high": close + np.random.uniform(10, 100, 200),
            "low": close - np.random.uniform(10, 100, 200),
            "close": close,
            "volume": np.full(200, 500.0),
        }, index=dates)

        # Lower threshold to detect regime change
        classifier = RegimeClassifier(volatility_threshold=1.2, volatility_lookback=10)
        regimes = classifier.classify(data)

        # Should detect some high volatility in the second half
        high_vol_count = (regimes == MarketRegime.HIGH_VOLATILITY.value).sum()
        assert high_vol_count > 0


class TestRegimeClassifierPerformanceByRegime:
    """Test performance bucketing by regime."""

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
    def equity_curve(self):
        """Create sample equity curve."""
        dates = pd.date_range("2024-01-01", periods=200, freq="15min")
        np.random.seed(42)
        returns = np.random.randn(200) * 0.001 + 0.0001
        equity = 10000 * np.cumprod(1 + returns)
        return pd.Series(equity, index=dates)

    def test_get_performance_by_regime(self, sample_data, equity_curve):
        """Get performance metrics grouped by regime."""
        classifier = RegimeClassifier()
        regimes = classifier.classify(sample_data)

        performance = classifier.get_performance_by_regime(
            regimes=regimes,
            equity_curve=equity_curve,
        )

        assert isinstance(performance, dict)
        # Should have at least one regime
        assert len(performance) > 0

        # Each regime should have return metrics
        for regime, metrics in performance.items():
            assert "return_pct" in metrics
            assert "count" in metrics

    def test_performance_by_regime_has_all_detected_regimes(self, sample_data, equity_curve):
        """Performance dict includes all detected regime types."""
        classifier = RegimeClassifier()
        regimes = classifier.classify(sample_data)

        performance = classifier.get_performance_by_regime(
            regimes=regimes,
            equity_curve=equity_curve,
        )

        # All unique regimes should be in performance dict
        unique_regimes = regimes.unique()
        for regime in unique_regimes:
            assert regime in performance


class TestRegimeClassifierEdgeCases:
    """Test edge cases."""

    def test_short_data(self):
        """Handle very short data."""
        dates = pd.date_range("2024-01-01", periods=10, freq="15min")
        close = np.linspace(50000, 50100, 10)
        data = pd.DataFrame({
            "open": close - 5,
            "high": close + 10,
            "low": close - 10,
            "close": close,
            "volume": np.full(10, 500.0),
        }, index=dates)

        classifier = RegimeClassifier()
        regimes = classifier.classify(data)

        assert isinstance(regimes, pd.Series)
        assert len(regimes) == 10

    def test_constant_price(self):
        """Handle constant price data."""
        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        close = np.full(100, 50000.0)
        data = pd.DataFrame({
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": np.full(100, 500.0),
        }, index=dates)

        classifier = RegimeClassifier()
        regimes = classifier.classify(data)

        # Should classify as ranging (no trend, no volatility)
        assert isinstance(regimes, pd.Series)


class TestSimpleRegimeEnum:
    """Test SimpleRegime enum (US-10 spec)."""

    def test_simple_regime_values(self):
        """SimpleRegime enum has expected values."""
        assert SimpleRegime.BULL.value == "bull"
        assert SimpleRegime.BEAR.value == "bear"
        assert SimpleRegime.RANGING.value == "ranging"


class TestSimpleClassification:
    """Test simple 3-state classification (US-10 spec)."""

    def test_detect_bull_market(self):
        """Detect bull market when price rises > 20%."""
        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        # 25% price increase
        close = np.linspace(50000, 62500, 100)
        data = pd.DataFrame({
            "open": close - 10,
            "high": close + 20,
            "low": close - 20,
            "close": close,
            "volume": np.full(100, 500.0),
        }, index=dates)

        classifier = RegimeClassifier()
        regimes = classifier.classify_simple(data)

        # Should detect bull market after 20% rise
        bull_count = (regimes == SimpleRegime.BULL.value).sum()
        assert bull_count > 0

    def test_detect_bear_market(self):
        """Detect bear market when price drops > 20%."""
        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        # 25% price decrease
        close = np.linspace(50000, 37500, 100)
        data = pd.DataFrame({
            "open": close + 10,
            "high": close + 20,
            "low": close - 20,
            "close": close,
            "volume": np.full(100, 500.0),
        }, index=dates)

        classifier = RegimeClassifier()
        regimes = classifier.classify_simple(data)

        # Should detect bear market after 20% drop
        bear_count = (regimes == SimpleRegime.BEAR.value).sum()
        assert bear_count > 0

    def test_detect_ranging_market(self):
        """Detect ranging market when price stays within 20% bounds."""
        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        # 10% oscillation (within 20% bounds)
        close = 50000 + np.sin(np.linspace(0, 4 * np.pi, 100)) * 5000
        data = pd.DataFrame({
            "open": close - 10,
            "high": close + 20,
            "low": close - 20,
            "close": close,
            "volume": np.full(100, 500.0),
        }, index=dates)

        classifier = RegimeClassifier()
        regimes = classifier.classify_simple(data)

        # Should be mostly ranging
        ranging_count = (regimes == SimpleRegime.RANGING.value).sum()
        assert ranging_count > 50  # Majority should be ranging

    def test_simple_regime_summary(self):
        """Get summary statistics for simple regime."""
        dates = pd.date_range("2024-01-01", periods=100, freq="15min")
        close = np.linspace(50000, 60000, 100)  # 20% increase
        data = pd.DataFrame({
            "open": close - 10,
            "high": close + 20,
            "low": close - 20,
            "close": close,
            "volume": np.full(100, 500.0),
        }, index=dates)

        classifier = RegimeClassifier()
        regimes = classifier.classify_simple(data)
        summary = classifier.get_simple_regime_summary(regimes)

        assert "bull_pct" in summary
        assert "bear_pct" in summary
        assert "ranging_pct" in summary
        # All percentages should sum to 100
        total = summary["bull_pct"] + summary["bear_pct"] + summary["ranging_pct"]
        assert abs(total - 100.0) < 0.01
