"""Parity test: BBSignalCore vs signal generator.

Verifies that the BBSignalGenerator (which delegates to BBSignalCore)
produces consistent results when compared to running the core directly.
"""

import numpy as np
import pandas as pd
import pytest

from strategy.indicators.bollinger_band import BBSignalCore
from strategy.strategies.bollinger_band.core import BBConfig
from strategy.strategies.bollinger_band.signal import (
    BBSignalGenerator,
    BBTradeFilterConfig,
)


def _generate_mean_reverting_ohlcv(n: int = 2000, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data with mean-reverting characteristics."""
    rng = np.random.RandomState(seed)

    close = np.zeros(n)
    close[0] = 100.0
    mean_level = 100.0

    for i in range(1, n):
        # Mean-reverting process (Ornstein-Uhlenbeck-like)
        reversion_speed = 0.02
        close[i] = (
            close[i - 1]
            + reversion_speed * (mean_level - close[i - 1])
            + rng.normal(0, 1.5)
        )
        # Shift mean occasionally
        if i % 500 == 0:
            mean_level += rng.normal(0, 5)

    spread = rng.uniform(0.005, 0.02, n) * np.abs(close)
    high = close + spread * rng.uniform(0.3, 1.0, n)
    low = close - spread * rng.uniform(0.3, 1.0, n)
    volume = rng.uniform(500, 5000, n) * (1 + rng.uniform(0, 2, n))

    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.002, n)),
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


class TestBBSignalCoreParity:
    """Test BBSignalCore produces consistent signals via generator."""

    def test_generator_uses_core(self):
        """BBSignalGenerator should produce identical results to direct core usage."""
        data = _generate_mean_reverting_ohlcv(1500, seed=42)
        config = BBConfig()
        filter_config = BBTradeFilterConfig()

        gen = BBSignalGenerator(config, filter_config)
        gen_signals = gen.generate(data)

        core = BBSignalCore(
            config,
            min_holding_bars=filter_config.min_holding_bars,
            cooldown_bars=filter_config.cooldown_bars,
            signal_confirmation=filter_config.signal_confirmation,
        )
        core_signals = np.zeros(len(data))
        for i in range(len(data)):
            core_signals[i] = core.update(close=data["close"].values[i])

        np.testing.assert_array_equal(gen_signals, core_signals)

    @pytest.mark.parametrize("seed", [1, 17, 99, 123, 456])
    def test_random_data_parity(self, seed):
        """Parity across multiple random datasets."""
        data = _generate_mean_reverting_ohlcv(1500, seed=seed)
        config = BBConfig()
        filter_config = BBTradeFilterConfig()

        gen = BBSignalGenerator(config, filter_config)
        gen_signals = gen.generate(data)

        core = BBSignalCore(
            config,
            min_holding_bars=filter_config.min_holding_bars,
            cooldown_bars=filter_config.cooldown_bars,
            signal_confirmation=filter_config.signal_confirmation,
        )
        core_signals = np.zeros(len(data))
        for i in range(len(data)):
            core_signals[i] = core.update(close=data["close"].values[i])

        mismatches = np.where(gen_signals != core_signals)[0]
        assert len(mismatches) == 0, (
            f"Seed {seed}: {len(mismatches)} mismatches. "
            f"First 5: {mismatches[:5].tolist()}"
        )

    def test_custom_config_parity(self):
        """Parity with non-default config parameters."""
        data = _generate_mean_reverting_ohlcv(1500, seed=77)
        config = BBConfig(
            bb_period=15,
            bb_multiplier=1.5,
            exit_threshold=0.2,
            stop_loss_pct=0.03,
            trend_bias="auto",
        )
        filter_config = BBTradeFilterConfig(
            min_holding_bars=3, cooldown_bars=1, signal_confirmation=2
        )

        gen = BBSignalGenerator(config, filter_config)
        gen_signals = gen.generate(data)

        core = BBSignalCore(
            config, min_holding_bars=3, cooldown_bars=1, signal_confirmation=2
        )
        core_signals = np.zeros(len(data))
        for i in range(len(data)):
            core_signals[i] = core.update(close=data["close"].values[i])

        np.testing.assert_array_equal(gen_signals, core_signals)

    def test_signals_have_trades(self):
        """Sanity check: the generated data should produce some trades."""
        data = _generate_mean_reverting_ohlcv(2000, seed=42)
        config = BBConfig()
        filter_config = BBTradeFilterConfig()

        gen = BBSignalGenerator(config, filter_config)
        signals = gen.generate(data)

        non_hold = np.count_nonzero(signals)
        assert non_hold > 0, "No trades generated — test data may need adjustment"

    def test_trend_bias_auto_parity(self):
        """Parity with auto trend bias."""
        data = _generate_mean_reverting_ohlcv(1500, seed=33)
        config = BBConfig(trend_bias="auto")
        filter_config = BBTradeFilterConfig()

        gen = BBSignalGenerator(config, filter_config)
        gen_signals = gen.generate(data)

        core = BBSignalCore(
            config,
            min_holding_bars=filter_config.min_holding_bars,
            cooldown_bars=filter_config.cooldown_bars,
            signal_confirmation=filter_config.signal_confirmation,
        )
        core_signals = np.zeros(len(data))
        for i in range(len(data)):
            core_signals[i] = core.update(close=data["close"].values[i])

        np.testing.assert_array_equal(gen_signals, core_signals)
