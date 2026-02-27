"""Parity test: FundingRateSignalCore vs signal generator.

Verifies that the FundingRateSignalGenerator (which delegates to FundingRateSignalCore)
produces consistent results when compared to running the core directly.
"""

import numpy as np
import pandas as pd
import pytest

from strategy.indicators.funding_rate import FundingRateSignalCore
from strategy.strategies.funding_rate.core import FundingRateConfig
from strategy.strategies.funding_rate.signal import (
    FundingRateFilterConfig,
    FundingRateSignalGenerator,
    _hours_since_last_settlement,
    _hours_until_next_settlement,
)


def _generate_funding_ohlcv(n: int = 500, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data with hourly DatetimeIndex for funding rate testing."""
    rng = np.random.RandomState(seed)

    close = np.zeros(n)
    close[0] = 50000.0  # BTC-like price

    for i in range(1, n):
        close[i] = close[i - 1] * (1 + rng.normal(0, 0.005))

    spread = rng.uniform(10, 100, n)
    high = close + spread * rng.uniform(0.3, 1.0, n)
    low = close - spread * rng.uniform(0.3, 1.0, n)
    volume = rng.uniform(100, 1000, n) * (1 + rng.uniform(0, 2, n))

    # Create 1-hour intervals
    timestamps = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")

    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.001, n)),
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=timestamps,
    )


def _generate_funding_rates(
    timestamps: pd.DatetimeIndex, seed: int = 42
) -> pd.DataFrame:
    """Generate synthetic funding rate data (every 8 hours)."""
    rng = np.random.RandomState(seed)

    # Generate 8h funding rate snapshots
    fr_times = pd.date_range(timestamps[0], timestamps[-1], freq="8h", tz="UTC")
    rates = rng.uniform(0.0001, 0.002, len(fr_times))

    return pd.DataFrame(
        {"funding_rate": rates},
        index=fr_times,
    )


class TestFundingRateSignalCoreParity:
    """Test FundingRateSignalCore produces consistent signals via generator."""

    def test_generator_uses_core(self):
        """FundingRateSignalGenerator should produce identical results to direct core usage."""
        data = _generate_funding_ohlcv(500, seed=42)
        config = FundingRateConfig()
        filter_config = FundingRateFilterConfig()

        gen = FundingRateSignalGenerator(config, filter_config)
        fr_data = _generate_funding_rates(data.index, seed=42)
        gen.funding_rates = fr_data
        gen_signals = gen.generate(data)

        # Replicate exact same logic as generator
        core = FundingRateSignalCore(
            config,
            min_holding_bars=filter_config.min_holding_bars,
            cooldown_bars=filter_config.cooldown_bars,
        )

        from strategy.strategies.funding_rate.signal import _build_funding_rate_series

        avg_funding = _build_funding_rate_series(
            data.index, fr_data, config.funding_lookback
        )

        core_signals = np.zeros(len(data))
        for i in range(len(data)):
            core.set_funding_rate(avg_funding[i])
            ts = data.index[i]
            hours_to_next = _hours_until_next_settlement(ts)
            hours_since_last = _hours_since_last_settlement(ts)
            core_signals[i] = core.update(
                close=data["close"].values[i],
                hours_to_next=hours_to_next,
                hours_since_last=hours_since_last,
            )

        np.testing.assert_array_equal(gen_signals, core_signals)

    @pytest.mark.parametrize("seed", [1, 17, 99])
    def test_random_data_parity(self, seed):
        """Parity across multiple random datasets."""
        data = _generate_funding_ohlcv(500, seed=seed)
        config = FundingRateConfig()
        filter_config = FundingRateFilterConfig()

        gen = FundingRateSignalGenerator(config, filter_config)
        fr_data = _generate_funding_rates(data.index, seed=seed)
        gen.funding_rates = fr_data
        gen_signals = gen.generate(data)

        from strategy.strategies.funding_rate.signal import _build_funding_rate_series

        core = FundingRateSignalCore(
            config,
            min_holding_bars=filter_config.min_holding_bars,
            cooldown_bars=filter_config.cooldown_bars,
        )
        avg_funding = _build_funding_rate_series(
            data.index, fr_data, config.funding_lookback
        )

        core_signals = np.zeros(len(data))
        for i in range(len(data)):
            core.set_funding_rate(avg_funding[i])
            ts = data.index[i]
            hours_to_next = _hours_until_next_settlement(ts)
            hours_since_last = _hours_since_last_settlement(ts)
            core_signals[i] = core.update(
                close=data["close"].values[i],
                hours_to_next=hours_to_next,
                hours_since_last=hours_since_last,
            )

        mismatches = np.where(gen_signals != core_signals)[0]
        assert len(mismatches) == 0, (
            f"Seed {seed}: {len(mismatches)} mismatches. "
            f"First 5: {mismatches[:5].tolist()}"
        )

    def test_signals_have_trades(self):
        """Sanity check: the generated data should produce some trades."""
        data = _generate_funding_ohlcv(500, seed=42)
        config = FundingRateConfig()
        filter_config = FundingRateFilterConfig()

        gen = FundingRateSignalGenerator(config, filter_config)
        fr_data = _generate_funding_rates(data.index, seed=42)
        gen.funding_rates = fr_data
        signals = gen.generate(data)

        non_hold = np.count_nonzero(signals)
        assert non_hold > 0, "No trades generated — test data may need adjustment"
