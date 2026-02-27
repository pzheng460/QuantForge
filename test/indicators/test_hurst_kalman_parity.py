"""Parity test: HurstKalmanSignalCore vs signal generator.

Verifies that the HurstKalmanSignalGenerator (which delegates to HurstKalmanSignalCore)
produces consistent results when compared to running the core directly.
"""

import numpy as np
import pandas as pd
import pytest

from strategy.indicators.hurst_kalman import HurstKalmanSignalCore
from strategy.strategies.hurst_kalman.core import HurstKalmanConfig
from strategy.strategies.hurst_kalman.signal import (
    HurstKalmanSignalGenerator,
    TradeFilterConfig,
)


def _generate_mean_reverting_ohlcv(n: int = 2000, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data with mean-reverting characteristics."""
    rng = np.random.RandomState(seed)

    close = np.zeros(n)
    close[0] = 100.0
    mean_level = 100.0

    for i in range(1, n):
        reversion_speed = 0.03
        close[i] = (
            close[i - 1]
            + reversion_speed * (mean_level - close[i - 1])
            + rng.normal(0, 1.0)
        )
        if i % 400 == 0:
            mean_level += rng.normal(0, 3)

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


class TestHurstKalmanSignalCoreParity:
    """Test HurstKalmanSignalCore produces consistent signals via generator."""

    def test_generator_uses_core(self):
        """HurstKalmanSignalGenerator should produce identical results to direct core usage."""
        data = _generate_mean_reverting_ohlcv(800, seed=42)
        config = HurstKalmanConfig()
        filter_config = TradeFilterConfig()

        gen = HurstKalmanSignalGenerator(config, filter_config)
        gen_signals = gen.generate(data)

        core = HurstKalmanSignalCore(
            config,
            min_holding_bars=filter_config.min_holding_bars,
            cooldown_bars=filter_config.cooldown_bars,
            signal_confirmation=filter_config.signal_confirmation,
            only_mean_reversion=filter_config.only_mean_reversion,
        )
        core_signals = np.zeros(len(data))
        for i in range(len(data)):
            core_signals[i] = core.update(close=data["close"].values[i])

        np.testing.assert_array_equal(gen_signals, core_signals)

    @pytest.mark.parametrize("seed", [1, 17, 99])
    def test_random_data_parity(self, seed):
        """Parity across multiple random datasets."""
        data = _generate_mean_reverting_ohlcv(800, seed=seed)
        config = HurstKalmanConfig()
        filter_config = TradeFilterConfig()

        gen = HurstKalmanSignalGenerator(config, filter_config)
        gen_signals = gen.generate(data)

        core = HurstKalmanSignalCore(
            config,
            min_holding_bars=filter_config.min_holding_bars,
            cooldown_bars=filter_config.cooldown_bars,
            signal_confirmation=filter_config.signal_confirmation,
            only_mean_reversion=filter_config.only_mean_reversion,
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
        data = _generate_mean_reverting_ohlcv(800, seed=77)
        config = HurstKalmanConfig(
            hurst_window=80,
            kalman_R=0.3,
            kalman_Q=1e-4,
            zscore_window=40,
            zscore_entry=1.5,
            zscore_stop=3.0,
            stop_loss_pct=0.05,
        )
        filter_config = TradeFilterConfig(
            min_holding_bars=4, cooldown_bars=2, only_mean_reversion=False
        )

        gen = HurstKalmanSignalGenerator(config, filter_config)
        gen_signals = gen.generate(data)

        core = HurstKalmanSignalCore(
            config,
            min_holding_bars=4,
            cooldown_bars=2,
            signal_confirmation=1,
            only_mean_reversion=False,
        )
        core_signals = np.zeros(len(data))
        for i in range(len(data)):
            core_signals[i] = core.update(close=data["close"].values[i])

        np.testing.assert_array_equal(gen_signals, core_signals)

    def test_signals_have_trades(self):
        """Sanity check: the generated data should produce some trades."""
        # Hurst-Kalman needs hurst_window + zscore_window (~160) bars warmup,
        # so we need enough data beyond that to trigger signals
        data = _generate_mean_reverting_ohlcv(2000, seed=42)
        config = HurstKalmanConfig(
            hurst_window=60,
            zscore_window=30,
            zscore_entry=1.5,
        )
        filter_config = TradeFilterConfig(
            min_holding_bars=4,
            cooldown_bars=2,
            only_mean_reversion=True,
        )

        gen = HurstKalmanSignalGenerator(config, filter_config)
        signals = gen.generate(data)

        non_hold = np.count_nonzero(signals)
        assert non_hold > 0, "No trades generated — test data may need adjustment"
