"""Parity test: VWAPSignalCore vs signal generator.

Verifies that the VWAPSignalGenerator (which delegates to VWAPSignalCore)
produces consistent results when compared to running the core directly.
"""

import numpy as np
import pandas as pd
import pytest

from strategy.indicators.vwap import VWAPSignalCore
from strategy.strategies.vwap.core import VWAPConfig
from strategy.strategies.vwap.signal import (
    VWAPSignalGenerator,
    VWAPTradeFilterConfig,
)


def _generate_vwap_ohlcv(n: int = 2000, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data with DatetimeIndex for VWAP testing."""
    rng = np.random.RandomState(seed)

    close = np.zeros(n)
    close[0] = 100.0
    mean_level = 100.0

    for i in range(1, n):
        reversion_speed = 0.02
        close[i] = (
            close[i - 1]
            + reversion_speed * (mean_level - close[i - 1])
            + rng.normal(0, 1.2)
        )
        if i % 500 == 0:
            mean_level += rng.normal(0, 5)

    spread = rng.uniform(0.005, 0.02, n) * np.abs(close)
    high = close + spread * rng.uniform(0.3, 1.0, n)
    low = close - spread * rng.uniform(0.3, 1.0, n)
    volume = rng.uniform(500, 5000, n) * (1 + rng.uniform(0, 2, n))

    # Create 5-minute intervals with DatetimeIndex
    timestamps = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")

    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.002, n)),
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=timestamps,
    )


class TestVWAPSignalCoreParity:
    """Test VWAPSignalCore produces consistent signals via generator."""

    def test_generator_uses_core(self):
        """VWAPSignalGenerator should produce identical results to direct core usage."""
        data = _generate_vwap_ohlcv(1500, seed=42)
        config = VWAPConfig()
        filter_config = VWAPTradeFilterConfig()

        gen = VWAPSignalGenerator(config, filter_config)
        gen_signals = gen.generate(data)

        core = VWAPSignalCore(
            config,
            min_holding_bars=filter_config.min_holding_bars,
            cooldown_bars=filter_config.cooldown_bars,
            signal_confirmation=filter_config.signal_confirmation,
        )
        core_signals = np.zeros(len(data))
        for i in range(len(data)):
            day = data.index[i].date()
            core_signals[i] = core.update(
                close=data["close"].values[i],
                high=data["high"].values[i],
                low=data["low"].values[i],
                volume=data["volume"].values[i],
                day=day,
            )

        np.testing.assert_array_equal(gen_signals, core_signals)

    @pytest.mark.parametrize("seed", [1, 17, 99])
    def test_random_data_parity(self, seed):
        """Parity across multiple random datasets."""
        data = _generate_vwap_ohlcv(1500, seed=seed)
        config = VWAPConfig()
        filter_config = VWAPTradeFilterConfig()

        gen = VWAPSignalGenerator(config, filter_config)
        gen_signals = gen.generate(data)

        core = VWAPSignalCore(
            config,
            min_holding_bars=filter_config.min_holding_bars,
            cooldown_bars=filter_config.cooldown_bars,
            signal_confirmation=filter_config.signal_confirmation,
        )
        core_signals = np.zeros(len(data))
        for i in range(len(data)):
            day = data.index[i].date()
            core_signals[i] = core.update(
                close=data["close"].values[i],
                high=data["high"].values[i],
                low=data["low"].values[i],
                volume=data["volume"].values[i],
                day=day,
            )

        mismatches = np.where(gen_signals != core_signals)[0]
        assert len(mismatches) == 0, (
            f"Seed {seed}: {len(mismatches)} mismatches. "
            f"First 5: {mismatches[:5].tolist()}"
        )

    def test_custom_config_parity(self):
        """Parity with non-default config parameters."""
        data = _generate_vwap_ohlcv(1500, seed=77)
        config = VWAPConfig(
            std_window=100,
            rsi_period=10,
            zscore_entry=1.5,
            zscore_exit=0.5,
            zscore_stop=3.0,
            rsi_oversold=25.0,
            rsi_overbought=75.0,
            stop_loss_pct=0.05,
        )
        filter_config = VWAPTradeFilterConfig(
            min_holding_bars=3, cooldown_bars=1, signal_confirmation=2
        )

        gen = VWAPSignalGenerator(config, filter_config)
        gen_signals = gen.generate(data)

        core = VWAPSignalCore(
            config, min_holding_bars=3, cooldown_bars=1, signal_confirmation=2
        )
        core_signals = np.zeros(len(data))
        for i in range(len(data)):
            day = data.index[i].date()
            core_signals[i] = core.update(
                close=data["close"].values[i],
                high=data["high"].values[i],
                low=data["low"].values[i],
                volume=data["volume"].values[i],
                day=day,
            )

        np.testing.assert_array_equal(gen_signals, core_signals)

    def test_signals_have_trades(self):
        """Sanity check: the generated data should produce some trades."""
        data = _generate_vwap_ohlcv(2000, seed=42)
        config = VWAPConfig()
        filter_config = VWAPTradeFilterConfig()

        gen = VWAPSignalGenerator(config, filter_config)
        signals = gen.generate(data)

        non_hold = np.count_nonzero(signals)
        assert non_hold > 0, "No trades generated — test data may need adjustment"
