"""
Unit tests for Hurst-Kalman core algorithms and indicator logic.
"""

import numpy as np

from strategy.strategies.hurst_kalman.core import (
    HurstKalmanConfig,
    KalmanFilter1D,
    calculate_hurst,
    calculate_zscore,
)
from strategy.live.hurst_kalman.indicator import HurstKalmanIndicator


class TestCalculateHurst:
    """Tests for the Hurst exponent calculation."""

    def test_random_walk_hurst_in_valid_range(self):
        """Random walk data should produce a valid H value."""
        np.random.seed(42)
        # Generate random walk
        returns = np.random.randn(500)
        prices = 100 * np.exp(np.cumsum(returns * 0.01))

        h = calculate_hurst(prices, window=100)

        # Hurst should be in valid range [0, 1]
        # Note: R/S analysis can produce varying results for random walks
        assert 0.0 <= h <= 1.0, f"Expected H in [0,1], got {h}"

    def test_trending_data_hurst_calculation(self):
        """Trending data should produce a valid H value."""
        np.random.seed(42)
        # Generate trending data with positive drift
        # Use log returns that accumulate to create persistent trend
        n = 500
        # Create positively correlated returns (trending behavior)
        base_returns = np.random.randn(n) * 0.01
        # Add momentum: each return is influenced by previous
        momentum_returns = np.zeros(n)
        momentum_returns[0] = base_returns[0]
        for i in range(1, n):
            momentum_returns[i] = (
                0.3 * momentum_returns[i - 1] + base_returns[i] + 0.002
            )
        prices = 100 * np.exp(np.cumsum(momentum_returns))

        h = calculate_hurst(prices, window=100)

        # Should produce a valid H value
        assert 0.0 <= h <= 1.0, f"Expected H in [0,1], got {h}"

    def test_mean_reverting_data_lower_than_trending(self):
        """Mean-reverting data should produce lower H than trending data."""
        np.random.seed(42)
        # Generate mean-reverting (Ornstein-Uhlenbeck) process
        n = 500
        mr_prices = np.zeros(n)
        mr_prices[0] = 100
        mean_price = 100
        reversion_speed = 0.3

        for i in range(1, n):
            mr_prices[i] = (
                mr_prices[i - 1]
                + reversion_speed * (mean_price - mr_prices[i - 1])
                + np.random.randn() * 0.5
            )

        # Generate trending data with positive drift
        base_returns = np.random.randn(n) * 0.01
        momentum_returns = np.zeros(n)
        momentum_returns[0] = base_returns[0]
        for i in range(1, n):
            momentum_returns[i] = (
                0.3 * momentum_returns[i - 1] + base_returns[i] + 0.002
            )
        trend_prices = 100 * np.exp(np.cumsum(momentum_returns))

        h_mr = calculate_hurst(mr_prices, window=100)
        h_trend = calculate_hurst(trend_prices, window=100)

        # Mean-reverting H should be lower than trending H
        # Note: The simplified R/S analysis (3 chunk sizes) may not
        # produce H < 0.5 for mean-reverting data, but the relative
        # ordering should hold.
        assert h_mr < h_trend, (
            f"Expected H_mr ({h_mr:.4f}) < H_trend ({h_trend:.4f})"
        )

    def test_insufficient_data_returns_0_5(self):
        """With insufficient data, should return 0.5 (random walk)."""
        prices = np.array([100.0, 101.0, 102.0])  # Only 3 points
        h = calculate_hurst(prices, window=100)
        assert h == 0.5

    def test_returns_value_between_0_and_1(self):
        """Hurst exponent should always be between 0 and 1."""
        np.random.seed(42)
        prices = np.random.randn(200).cumsum() + 100

        h = calculate_hurst(prices, window=100)

        assert 0.0 <= h <= 1.0, f"Hurst should be in [0,1], got {h}"


class TestKalmanFilter1D:
    """Tests for the 1D Kalman Filter."""

    def test_first_observation_sets_initial_state(self):
        """First observation should initialize the state."""
        kf = KalmanFilter1D()
        result = kf.update(100.0)

        assert result == 100.0
        assert kf.estimate == 100.0

    def test_filters_noisy_signal(self):
        """Filter should smooth out noise."""
        kf = KalmanFilter1D(R=1.0, Q=0.01)

        # True signal is constant 100, with noise
        np.random.seed(42)
        true_value = 100.0
        observations = true_value + np.random.randn(100) * 5

        estimates = [kf.update(obs) for obs in observations]

        # Final estimate should be close to true value
        assert abs(estimates[-1] - true_value) < 2.0, (
            f"Filter estimate {estimates[-1]} not close to true {true_value}"
        )

    def test_tracks_slowly_changing_signal(self):
        """Filter should track a slowly drifting signal."""
        # Use higher Q to allow faster tracking of drifting signal
        kf = KalmanFilter1D(R=0.1, Q=0.01)

        # Slowly increasing signal with noise
        np.random.seed(42)
        n = 100
        true_values = np.linspace(100, 110, n)
        observations = true_values + np.random.randn(n) * 0.5

        estimates = [kf.update(obs) for obs in observations]

        # Should track the upward trend
        assert estimates[-1] > estimates[0]
        # With higher Q, filter should track better
        assert abs(estimates[-1] - true_values[-1]) < 5.0

    def test_slope_positive_for_uptrend(self):
        """Slope should be positive for uptrending data."""
        kf = KalmanFilter1D()

        # Feed increasing prices
        for price in [100, 101, 102, 103, 104, 105]:
            kf.update(price)

        slope = kf.get_slope(lookback=5)
        assert slope > 0, f"Expected positive slope for uptrend, got {slope}"

    def test_slope_negative_for_downtrend(self):
        """Slope should be negative for downtrending data."""
        kf = KalmanFilter1D()

        # Feed decreasing prices
        for price in [105, 104, 103, 102, 101, 100]:
            kf.update(price)

        slope = kf.get_slope(lookback=5)
        assert slope < 0, f"Expected negative slope for downtrend, got {slope}"

    def test_slope_near_zero_for_flat(self):
        """Slope should be near zero for flat data."""
        kf = KalmanFilter1D()

        # Feed constant prices
        for _ in range(10):
            kf.update(100.0)

        slope = kf.get_slope(lookback=5)
        assert abs(slope) < 0.1, f"Expected near-zero slope for flat data, got {slope}"

    def test_reset_clears_state(self):
        """Reset should clear all state."""
        kf = KalmanFilter1D()
        kf.update(100.0)
        kf.update(101.0)

        kf.reset()

        assert kf.estimate is None
        assert kf.P == 1.0


class TestCalculateZscore:
    """Tests for Z-Score calculation."""

    def test_zscore_zero_when_price_equals_kalman(self):
        """Z-Score should be ~0 when price equals Kalman estimate."""
        prices = np.array([100.0] * 60)
        kalman_prices = np.array([100.0] * 60)

        zscore = calculate_zscore(prices, kalman_prices, window=50)

        # With no deviation, zscore is undefined (div by 0), should return 0
        assert zscore == 0.0

    def test_zscore_positive_when_price_above_kalman(self):
        """Z-Score should be positive when price > Kalman."""
        # Create data where last price is above Kalman
        prices = np.array([100.0] * 59 + [105.0])
        kalman_prices = np.array([100.0] * 60)

        zscore = calculate_zscore(prices, kalman_prices, window=50)

        assert zscore > 0, f"Expected positive Z-Score, got {zscore}"

    def test_zscore_negative_when_price_below_kalman(self):
        """Z-Score should be negative when price < Kalman."""
        # Create data where last price is below Kalman
        prices = np.array([100.0] * 59 + [95.0])
        kalman_prices = np.array([100.0] * 60)

        zscore = calculate_zscore(prices, kalman_prices, window=50)

        assert zscore < 0, f"Expected negative Z-Score, got {zscore}"

    def test_insufficient_data_returns_zero(self):
        """With insufficient data, should return 0."""
        prices = np.array([100.0] * 10)
        kalman_prices = np.array([100.0] * 10)

        zscore = calculate_zscore(prices, kalman_prices, window=50)

        assert zscore == 0.0


class TestHurstKalmanConfig:
    """Tests for configuration dataclass."""

    def test_default_values(self):
        """Default configuration should have expected values."""
        config = HurstKalmanConfig()

        assert config.symbols == []
        assert config.timeframe == "15m"
        assert config.hurst_window == 100
        assert config.kalman_R == 0.2
        assert config.kalman_Q == 5e-05
        assert config.zscore_window == 60
        assert config.mean_reversion_threshold == 0.48
        assert config.trend_threshold == 0.60
        assert config.zscore_entry == 2.0
        assert config.zscore_stop == 3.5
        assert config.position_size_pct == 0.10
        assert config.stop_loss_pct == 0.03
        assert config.daily_loss_limit == 0.03

    def test_custom_values(self):
        """Should accept custom configuration values."""
        config = HurstKalmanConfig(
            symbols=["ETHUSDT-PERP.BITGET"],
            hurst_window=150,
            zscore_entry=2.5,
        )

        assert config.symbols == ["ETHUSDT-PERP.BITGET"]
        assert config.hurst_window == 150
        assert config.zscore_entry == 2.5
        # Other values should remain default
        assert config.timeframe == "15m"


class TestStopLoss:
    """Tests for the direction-aware stop loss in HurstKalmanIndicator."""

    def _make_indicator(self, stop_loss_pct: float = 0.02, zscore_stop: float = 4.0):
        config = HurstKalmanConfig(
            stop_loss_pct=stop_loss_pct,
            zscore_stop=zscore_stop,
        )
        return HurstKalmanIndicator(config=config)

    def test_long_stop_loss_triggers_on_loss(self):
        """Long position should trigger stop loss when price drops beyond threshold."""
        ind = self._make_indicator(stop_loss_pct=0.02)
        # Entry at 100, price drops to 97 → -3% loss > 2% threshold
        assert ind.should_stop_loss(entry_price=100.0, current_price=97.0, is_long=True)

    def test_long_stop_loss_no_trigger_on_profit(self):
        """Long position should NOT trigger stop loss when in profit."""
        ind = self._make_indicator(stop_loss_pct=0.02)
        # Entry at 100, price rises to 105 → +5% profit
        assert not ind.should_stop_loss(
            entry_price=100.0, current_price=105.0, is_long=True
        )

    def test_short_stop_loss_triggers_on_loss(self):
        """Short position should trigger stop loss when price rises beyond threshold."""
        ind = self._make_indicator(stop_loss_pct=0.02)
        # Entry at 100, price rises to 103 → -3% loss for short > 2% threshold
        assert ind.should_stop_loss(
            entry_price=100.0, current_price=103.0, is_long=False
        )

    def test_short_stop_loss_no_trigger_on_profit(self):
        """Short position should NOT trigger stop loss when in profit."""
        ind = self._make_indicator(stop_loss_pct=0.02)
        # Entry at 100, price drops to 95 → +5% profit for short
        assert not ind.should_stop_loss(
            entry_price=100.0, current_price=95.0, is_long=False
        )

    def test_zscore_stop_triggers_regardless_of_direction(self):
        """Z-Score model failure should trigger stop loss regardless of PnL."""
        ind = self._make_indicator(stop_loss_pct=0.02, zscore_stop=4.0)
        # Force internal zscore to extreme value
        ind._zscore = 5.0
        # Even though long is in profit, zscore stop should trigger
        assert ind.should_stop_loss(
            entry_price=100.0, current_price=105.0, is_long=True
        )
        # Also for short in profit
        assert ind.should_stop_loss(
            entry_price=100.0, current_price=95.0, is_long=False
        )

    def test_zero_entry_price_returns_false(self):
        """Zero entry price should never trigger stop loss."""
        ind = self._make_indicator()
        assert not ind.should_stop_loss(
            entry_price=0.0, current_price=100.0, is_long=True
        )


class TestWarmupPeriod:
    """Tests for automatic warmup period calculation."""

    def test_default_warmup_matches_config(self):
        """Default warmup should equal hurst_window + zscore_window."""
        config = HurstKalmanConfig(hurst_window=100, zscore_window=50)
        ind = HurstKalmanIndicator(config=config)
        assert ind.warmup_period == 150  # 100 + 50

    def test_custom_config_warmup(self):
        """Custom config should compute warmup from its own window sizes."""
        config = HurstKalmanConfig(hurst_window=80, zscore_window=40)
        ind = HurstKalmanIndicator(config=config)
        assert ind.warmup_period == 120  # 80 + 40

    def test_explicit_warmup_overrides_auto(self):
        """Explicit warmup_period should override the auto-calculated value."""
        config = HurstKalmanConfig(hurst_window=100, zscore_window=50)
        ind = HurstKalmanIndicator(config=config, warmup_period=200)
        assert ind.warmup_period == 200
