"""Parity test: DualRegimeSignalCore vs signal generator.

Verifies that the DualRegimeSignalGenerator (which delegates to DualRegimeSignalCore)
produces consistent results when compared to running the core directly.
"""

import numpy as np
import pandas as pd
import pytest

from strategy.indicators.dual_regime import DualRegimeSignalCore
from strategy.strategies.dual_regime.core import DualRegimeConfig
from strategy.strategies.dual_regime.signal import (
    DualRegimeSignalGenerator,
    DualRegimeTradeFilterConfig,
)


def _generate_dual_regime_ohlcv(n: int = 2000, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV with alternating trending and ranging periods."""
    rng = np.random.RandomState(seed)

    close = np.zeros(n)
    close[0] = 100.0

    for i in range(1, n):
        regime = (i // 250) % 4
        if regime == 0:
            drift = 0.002  # Strong uptrend (triggers momentum)
        elif regime == 1:
            drift = 0.0  # Range (triggers BB)
        elif regime == 2:
            drift = -0.002  # Downtrend (triggers momentum)
        else:
            drift = 0.0  # Range again
        close[i] = close[i - 1] * (1 + drift + rng.normal(0, 0.012))

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


class TestDualRegimeSignalCoreParity:
    """Test DualRegimeSignalCore produces consistent signals via generator."""

    def test_generator_uses_core(self):
        """DualRegimeSignalGenerator should produce identical results to direct core usage."""
        data = _generate_dual_regime_ohlcv(1500, seed=42)
        config = DualRegimeConfig()
        filter_config = DualRegimeTradeFilterConfig()

        gen = DualRegimeSignalGenerator(config, filter_config)
        gen_signals = gen.generate(data)

        core = DualRegimeSignalCore(
            config,
            min_holding_bars=filter_config.min_holding_bars,
            cooldown_bars=filter_config.cooldown_bars,
            signal_confirmation=filter_config.signal_confirmation,
        )
        core_signals = np.zeros(len(data))
        for i in range(len(data)):
            core_signals[i] = core.update(
                close=data["close"].values[i],
                high=data["high"].values[i],
                low=data["low"].values[i],
                volume=data["volume"].values[i],
            )

        np.testing.assert_array_equal(gen_signals, core_signals)

    @pytest.mark.parametrize("seed", [1, 17, 99, 123])
    def test_random_data_parity(self, seed):
        """Parity across multiple random datasets."""
        data = _generate_dual_regime_ohlcv(1500, seed=seed)
        config = DualRegimeConfig()
        filter_config = DualRegimeTradeFilterConfig()

        gen = DualRegimeSignalGenerator(config, filter_config)
        gen_signals = gen.generate(data)

        core = DualRegimeSignalCore(
            config,
            min_holding_bars=filter_config.min_holding_bars,
            cooldown_bars=filter_config.cooldown_bars,
            signal_confirmation=filter_config.signal_confirmation,
        )
        core_signals = np.zeros(len(data))
        for i in range(len(data)):
            core_signals[i] = core.update(
                close=data["close"].values[i],
                high=data["high"].values[i],
                low=data["low"].values[i],
                volume=data["volume"].values[i],
            )

        mismatches = np.where(gen_signals != core_signals)[0]
        assert len(mismatches) == 0, (
            f"Seed {seed}: {len(mismatches)} mismatches. "
            f"First 5: {mismatches[:5].tolist()}"
        )

    def test_custom_config_parity(self):
        """Parity with non-default config parameters."""
        data = _generate_dual_regime_ohlcv(1500, seed=77)
        config = DualRegimeConfig(
            adx_period=10,
            adx_trend_threshold=20.0,
            roc_period=8,
            roc_threshold=0.01,
            ema_fast=5,
            ema_slow=20,
            ema_trend=40,
            bb_period=15,
            bb_std=1.5,
            stop_loss_pct=0.05,
        )
        filter_config = DualRegimeTradeFilterConfig(
            min_holding_bars=3, cooldown_bars=1, signal_confirmation=2
        )

        gen = DualRegimeSignalGenerator(config, filter_config)
        gen_signals = gen.generate(data)

        core = DualRegimeSignalCore(
            config, min_holding_bars=3, cooldown_bars=1, signal_confirmation=2
        )
        core_signals = np.zeros(len(data))
        for i in range(len(data)):
            core_signals[i] = core.update(
                close=data["close"].values[i],
                high=data["high"].values[i],
                low=data["low"].values[i],
                volume=data["volume"].values[i],
            )

        np.testing.assert_array_equal(gen_signals, core_signals)

    def test_signals_have_trades(self):
        """Sanity check: the generated data should produce some trades."""
        data = _generate_dual_regime_ohlcv(2000, seed=42)
        config = DualRegimeConfig()
        filter_config = DualRegimeTradeFilterConfig()

        gen = DualRegimeSignalGenerator(config, filter_config)
        signals = gen.generate(data)

        non_hold = np.count_nonzero(signals)
        assert non_hold > 0, "No trades generated — test data may need adjustment"
