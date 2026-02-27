"""Parity test: EMASignalCore vs signal generator.

Verifies that the EMASignalGenerator (which delegates to EMASignalCore)
produces consistent results when compared to running the core directly.
"""

import numpy as np
import pandas as pd
import pytest

from strategy.indicators.ema_crossover import EMASignalCore
from strategy.strategies.ema_crossover.core import EMAConfig
from strategy.strategies.ema_crossover.signal import (
    EMASignalGenerator,
    EMATradeFilterConfig,
)


def _generate_trending_ohlcv(n: int = 2000, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data with trending/ranging regimes."""
    rng = np.random.RandomState(seed)

    close = np.zeros(n)
    close[0] = 100.0

    for i in range(1, n):
        regime = (i // 200) % 3
        if regime == 0:
            drift = 0.001
        elif regime == 1:
            drift = -0.001
        else:
            drift = 0.0
        close[i] = close[i - 1] * (1 + drift + rng.normal(0, 0.015))

    spread = rng.uniform(0.005, 0.02, n) * close
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


class TestEMASignalCoreParity:
    """Test EMASignalCore produces consistent signals via generator."""

    def test_generator_uses_core(self):
        """EMASignalGenerator should produce identical results to direct core usage."""
        data = _generate_trending_ohlcv(1500, seed=42)
        config = EMAConfig()
        filter_config = EMATradeFilterConfig()

        # Use the generator
        gen = EMASignalGenerator(config, filter_config)
        gen_signals = gen.generate(data)

        # Use core directly
        core = EMASignalCore(
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
        data = _generate_trending_ohlcv(1500, seed=seed)
        config = EMAConfig()
        filter_config = EMATradeFilterConfig()

        gen = EMASignalGenerator(config, filter_config)
        gen_signals = gen.generate(data)

        core = EMASignalCore(
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
        data = _generate_trending_ohlcv(1500, seed=77)
        config = EMAConfig(fast_period=8, slow_period=21, stop_loss_pct=0.03)
        filter_config = EMATradeFilterConfig(
            min_holding_bars=3, cooldown_bars=1, signal_confirmation=2
        )

        gen = EMASignalGenerator(config, filter_config)
        gen_signals = gen.generate(data)

        core = EMASignalCore(
            config,
            min_holding_bars=3,
            cooldown_bars=1,
            signal_confirmation=2,
        )
        core_signals = np.zeros(len(data))
        for i in range(len(data)):
            core_signals[i] = core.update(close=data["close"].values[i])

        np.testing.assert_array_equal(gen_signals, core_signals)

    def test_signals_have_trades(self):
        """Sanity check: the generated data should produce some trades."""
        data = _generate_trending_ohlcv(2000, seed=42)
        config = EMAConfig()
        filter_config = EMATradeFilterConfig()

        gen = EMASignalGenerator(config, filter_config)
        signals = gen.generate(data)

        non_hold = np.count_nonzero(signals)
        assert non_hold > 0, "No trades generated — test data may need adjustment"

    def test_generator_with_param_overrides(self):
        """Generator with params should match core with modified config."""
        data = _generate_trending_ohlcv(1500, seed=55)
        config = EMAConfig()
        filter_config = EMATradeFilterConfig()
        params = {"fast_period": 8, "slow_period": 30}

        gen = EMASignalGenerator(config, filter_config)
        gen_signals = gen.generate(data, params=params)

        modified_config = EMAConfig(fast_period=8, slow_period=30)
        core = EMASignalCore(
            modified_config,
            min_holding_bars=filter_config.min_holding_bars,
            cooldown_bars=filter_config.cooldown_bars,
            signal_confirmation=filter_config.signal_confirmation,
        )
        core_signals = np.zeros(len(data))
        for i in range(len(data)):
            core_signals[i] = core.update(close=data["close"].values[i])

        np.testing.assert_array_equal(gen_signals, core_signals)
