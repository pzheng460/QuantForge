"""Parity test: GridSignalCore vs signal generator.

Verifies that the GridSignalGenerator (which delegates to GridSignalCore)
produces consistent results when compared to running the core directly.
"""

import numpy as np
import pandas as pd
import pytest

from strategy.indicators.grid_trading import GridSignalCore
from strategy.strategies.grid_trading.core import GridConfig
from strategy.strategies.grid_trading.signal import (
    GridSignalGenerator,
    GridTradeFilterConfig,
)


def _generate_range_bound_ohlcv(n: int = 2000, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data suitable for grid trading."""
    rng = np.random.RandomState(seed)

    close = np.zeros(n)
    close[0] = 100.0
    mean_level = 100.0

    for i in range(1, n):
        # Range-bound with occasional breakouts
        reversion_speed = 0.01
        close[i] = (
            close[i - 1]
            + reversion_speed * (mean_level - close[i - 1])
            + rng.normal(0, 0.8)
        )

    spread = rng.uniform(0.003, 0.015, n) * np.abs(close)
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


class TestGridSignalCoreParity:
    """Test GridSignalCore produces consistent signals via generator."""

    def test_generator_uses_core(self):
        """GridSignalGenerator should produce identical results to direct core usage."""
        data = _generate_range_bound_ohlcv(1000, seed=42)
        config = GridConfig()
        filter_config = GridTradeFilterConfig()

        gen = GridSignalGenerator(config, filter_config)
        gen_signals = gen.generate(data)

        core = GridSignalCore(
            config,
            min_holding_bars=filter_config.min_holding_bars,
            cooldown_bars=filter_config.cooldown_bars,
        )
        core_signals = np.zeros(len(data))
        for i in range(len(data)):
            core_signals[i] = core.update(
                close=data["close"].values[i],
                high=data["high"].values[i],
                low=data["low"].values[i],
            )

        np.testing.assert_array_equal(gen_signals, core_signals)

    @pytest.mark.parametrize("seed", [1, 17, 99, 123])
    def test_random_data_parity(self, seed):
        """Parity across multiple random datasets."""
        data = _generate_range_bound_ohlcv(1000, seed=seed)
        config = GridConfig()
        filter_config = GridTradeFilterConfig()

        gen = GridSignalGenerator(config, filter_config)
        gen_signals = gen.generate(data)

        core = GridSignalCore(
            config,
            min_holding_bars=filter_config.min_holding_bars,
            cooldown_bars=filter_config.cooldown_bars,
        )
        core_signals = np.zeros(len(data))
        for i in range(len(data)):
            core_signals[i] = core.update(
                close=data["close"].values[i],
                high=data["high"].values[i],
                low=data["low"].values[i],
            )

        mismatches = np.where(gen_signals != core_signals)[0]
        assert len(mismatches) == 0, (
            f"Seed {seed}: {len(mismatches)} mismatches. "
            f"First 5: {mismatches[:5].tolist()}"
        )

    def test_custom_config_parity(self):
        """Parity with non-default config parameters."""
        data = _generate_range_bound_ohlcv(1000, seed=77)
        config = GridConfig(
            grid_count=10,
            atr_multiplier=3.0,
            sma_period=30,
            atr_period=10,
            recalc_period=48,
            entry_lines=1,
            profit_lines=2,
            stop_loss_pct=0.03,
        )
        filter_config = GridTradeFilterConfig(min_holding_bars=2, cooldown_bars=1)

        gen = GridSignalGenerator(config, filter_config)
        gen_signals = gen.generate(data)

        core = GridSignalCore(config, min_holding_bars=2, cooldown_bars=1)
        core_signals = np.zeros(len(data))
        for i in range(len(data)):
            core_signals[i] = core.update(
                close=data["close"].values[i],
                high=data["high"].values[i],
                low=data["low"].values[i],
            )

        np.testing.assert_array_equal(gen_signals, core_signals)

    def test_signals_have_trades(self):
        """Sanity check: the generated data should produce some trades."""
        data = _generate_range_bound_ohlcv(1000, seed=42)
        config = GridConfig()
        filter_config = GridTradeFilterConfig()

        gen = GridSignalGenerator(config, filter_config)
        signals = gen.generate(data)

        non_hold = np.count_nonzero(signals)
        assert non_hold > 0, "No trades generated — test data may need adjustment"
