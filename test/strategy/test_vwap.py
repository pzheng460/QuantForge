"""
Unit tests for VWAP Mean Reversion core algorithms and indicator logic.
"""

import numpy as np

from strategy.bitget.vwap.core import (
    VWAPConfig,
    calculate_rsi,
    calculate_vwap,
    calculate_vwap_zscore,
)
from strategy.bitget.vwap.indicator import VWAPIndicator


class TestCalculateVWAP:
    """Tests for the VWAP calculation."""

    def test_constant_price_vwap_equals_price(self):
        """VWAP should equal price when price is constant."""
        n = 50
        prices = np.full(n, 100.0)
        highs = np.full(n, 101.0)
        lows = np.full(n, 99.0)
        volumes = np.full(n, 1000.0)

        vwap = calculate_vwap(highs, lows, prices, volumes)

        # Typical price = (101 + 99 + 100) / 3 = 100.0
        assert not np.isnan(vwap[-1])
        assert abs(vwap[-1] - 100.0) < 0.01

    def test_vwap_volume_weighting(self):
        """VWAP should be volume-weighted toward high-volume bars."""
        highs = np.array([100.0, 200.0])
        lows = np.array([100.0, 200.0])
        closes = np.array([100.0, 200.0])
        volumes = np.array([1000.0, 1.0])

        vwap = calculate_vwap(highs, lows, closes, volumes)

        # Typical prices: 100, 200. Volumes: 1000, 1.
        # VWAP = (100*1000 + 200*1) / 1001 ~ 100.1
        assert vwap[-1] < 110.0  # Should be close to 100, not 150

    def test_vwap_daily_reset(self):
        """VWAP should reset at day boundaries when timestamps provided."""
        import pandas as pd

        # Two days of data: 3 bars day1, 3 bars day2
        timestamps = pd.DatetimeIndex(
            [
                "2024-01-01 22:00",
                "2024-01-01 23:00",
                "2024-01-01 23:30",
                "2024-01-02 00:00",
                "2024-01-02 01:00",
                "2024-01-02 02:00",
            ],
            tz="UTC",
        )
        # Day 1: price ~100, Day 2: price ~200
        closes = np.array([100.0, 100.0, 100.0, 200.0, 200.0, 200.0])
        highs = closes + 1.0
        lows = closes - 1.0
        volumes = np.full(6, 100.0)

        vwap = calculate_vwap(highs, lows, closes, volumes, timestamps)

        # After daily reset, VWAP should be close to 200 on day 2
        assert vwap[-1] > 190.0

    def test_zero_volume_carries_forward(self):
        """Zero-volume bars should carry forward previous VWAP."""
        highs = np.array([100.0, 200.0, 300.0])
        lows = np.array([100.0, 200.0, 300.0])
        closes = np.array([100.0, 200.0, 300.0])
        volumes = np.array([100.0, 0.0, 100.0])

        vwap = calculate_vwap(highs, lows, closes, volumes)

        # Bar 1 (zero vol): should carry forward bar 0's VWAP
        assert vwap[1] == vwap[0]

    def test_insufficient_data(self):
        """Single bar should still compute VWAP."""
        vwap = calculate_vwap(
            np.array([101.0]),
            np.array([99.0]),
            np.array([100.0]),
            np.array([1000.0]),
        )
        assert not np.isnan(vwap[0])


class TestCalculateRSI:
    """Tests for the RSI calculation."""

    def test_oversold_after_consistent_drops(self):
        """RSI should be low after consistent price drops."""
        # Generate declining prices
        prices = np.linspace(100, 70, 30)
        rsi = calculate_rsi(prices, period=14)

        # RSI at the end should be oversold (< 30)
        assert rsi[-1] < 30.0

    def test_overbought_after_consistent_rises(self):
        """RSI should be high after consistent price rises."""
        # Generate rising prices
        prices = np.linspace(70, 100, 30)
        rsi = calculate_rsi(prices, period=14)

        # RSI at the end should be overbought (> 70)
        assert rsi[-1] > 70.0

    def test_rsi_range(self):
        """RSI should always be between 0 and 100."""
        np.random.seed(42)
        prices = 100 + np.cumsum(np.random.randn(200) * 0.5)
        rsi = calculate_rsi(prices, period=14)

        valid = rsi[~np.isnan(rsi)]
        assert np.all(valid >= 0.0)
        assert np.all(valid <= 100.0)

    def test_rsi_nan_before_period(self):
        """RSI should be NaN before having enough data."""
        prices = np.linspace(100, 110, 20)
        rsi = calculate_rsi(prices, period=14)

        # First 14 values should be NaN
        assert np.all(np.isnan(rsi[:14]))
        assert not np.isnan(rsi[14])

    def test_insufficient_data_all_nan(self):
        """With insufficient data, all values should be NaN."""
        prices = np.array([100.0, 101.0, 102.0])
        rsi = calculate_rsi(prices, period=14)
        assert np.all(np.isnan(rsi))


class TestCalculateVWAPZscore:
    """Tests for the VWAP Z-Score calculation."""

    def test_zscore_zero_when_price_equals_vwap(self):
        """Z-Score should be ~0 when price equals VWAP."""
        prices = np.full(60, 100.0)
        vwap = np.full(60, 100.0)
        zscore = calculate_vwap_zscore(prices, vwap, std_window=50)

        # With no deviation, zscore should be NaN or 0
        valid = zscore[~np.isnan(zscore)]
        if len(valid) > 0:
            assert abs(valid[-1]) < 1e-5

    def test_zscore_positive_when_price_above_vwap(self):
        """Z-Score should be positive when price is above VWAP."""
        n = 220
        np.random.seed(42)
        vwap = np.full(n, 100.0)
        prices = 100.0 + np.random.randn(n) * 0.5
        # Force last price well above VWAP
        prices[-1] = 105.0

        zscore = calculate_vwap_zscore(prices, vwap, std_window=200)

        assert zscore[-1] > 0

    def test_zscore_negative_when_price_below_vwap(self):
        """Z-Score should be negative when price is below VWAP."""
        n = 220
        np.random.seed(42)
        vwap = np.full(n, 100.0)
        prices = 100.0 + np.random.randn(n) * 0.5
        # Force last price well below VWAP
        prices[-1] = 95.0

        zscore = calculate_vwap_zscore(prices, vwap, std_window=200)

        assert zscore[-1] < 0

    def test_insufficient_data_returns_nan(self):
        """With insufficient data, should return NaN."""
        prices = np.array([100.0] * 10)
        vwap = np.array([100.0] * 10)
        zscore = calculate_vwap_zscore(prices, vwap, std_window=50)

        assert np.all(np.isnan(zscore))


class TestVWAPConfig:
    """Tests for configuration dataclass."""

    def test_default_values(self):
        """Default configuration should have expected values."""
        config = VWAPConfig()

        assert config.symbols == []
        assert config.timeframe == "5m"
        assert config.std_window == 200
        assert config.rsi_period == 14
        assert config.zscore_entry == 2.0
        assert config.zscore_exit == 0.0
        assert config.zscore_stop == 3.5
        assert config.rsi_oversold == 30.0
        assert config.rsi_overbought == 70.0
        assert config.position_size_pct == 0.20
        assert config.stop_loss_pct == 0.03
        assert config.daily_loss_limit == 0.03

    def test_custom_values(self):
        """Should accept custom configuration values."""
        config = VWAPConfig(
            symbols=["ETHUSDT-PERP.BITGET"],
            std_window=150,
            zscore_entry=2.5,
            rsi_period=20,
        )

        assert config.symbols == ["ETHUSDT-PERP.BITGET"]
        assert config.std_window == 150
        assert config.zscore_entry == 2.5
        assert config.rsi_period == 20
        # Other values should remain default
        assert config.timeframe == "5m"


class TestVWAPIndicator:
    """Tests for VWAPIndicator."""

    def test_default_warmup_matches_config(self):
        """Default warmup should equal std_window + rsi_period."""
        config = VWAPConfig(std_window=200, rsi_period=14)
        ind = VWAPIndicator(config=config)
        assert ind.warmup_period == 214  # 200 + 14

    def test_custom_config_warmup(self):
        """Custom config should compute warmup from its own parameters."""
        config = VWAPConfig(std_window=100, rsi_period=20)
        ind = VWAPIndicator(config=config)
        assert ind.warmup_period == 120  # 100 + 20

    def test_explicit_warmup_overrides_auto(self):
        """Explicit warmup_period should override the auto-calculated value."""
        config = VWAPConfig(std_window=200, rsi_period=14)
        ind = VWAPIndicator(config=config, warmup_period=300)
        assert ind.warmup_period == 300


class TestStopLoss:
    """Tests for the direction-aware stop loss in VWAPIndicator."""

    def _make_indicator(self, stop_loss_pct: float = 0.02, zscore_stop: float = 4.0):
        config = VWAPConfig(
            stop_loss_pct=stop_loss_pct,
            zscore_stop=zscore_stop,
        )
        return VWAPIndicator(config=config)

    def test_long_stop_loss_triggers_on_loss(self):
        """Long position should trigger stop loss when price drops beyond threshold."""
        ind = self._make_indicator(stop_loss_pct=0.02)
        # Entry at 100, price drops to 97 -> -3% loss > 2% threshold
        assert ind.should_stop_loss(entry_price=100.0, current_price=97.0, is_long=True)

    def test_long_stop_loss_no_trigger_on_profit(self):
        """Long position should NOT trigger stop loss when in profit."""
        ind = self._make_indicator(stop_loss_pct=0.02)
        # Entry at 100, price rises to 105 -> +5% profit
        assert not ind.should_stop_loss(
            entry_price=100.0, current_price=105.0, is_long=True
        )

    def test_short_stop_loss_triggers_on_loss(self):
        """Short position should trigger stop loss when price rises beyond threshold."""
        ind = self._make_indicator(stop_loss_pct=0.02)
        # Entry at 100, price rises to 103 -> -3% loss for short > 2% threshold
        assert ind.should_stop_loss(
            entry_price=100.0, current_price=103.0, is_long=False
        )

    def test_short_stop_loss_no_trigger_on_profit(self):
        """Short position should NOT trigger stop loss when in profit."""
        ind = self._make_indicator(stop_loss_pct=0.02)
        # Entry at 100, price drops to 95 -> +5% profit for short
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
