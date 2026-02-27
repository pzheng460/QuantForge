"""Parity test: MomentumSignalCore vs original vectorized signal generation.

Verifies that MomentumSignalCore.update() produces identical signals
to the original vectorized implementation for the same OHLCV data.
"""

import numpy as np
import pandas as pd
import pytest

from strategy.indicators.momentum import MomentumSignalCore
from strategy.strategies.momentum.core import MomentumConfig


def _generate_trending_ohlcv(n: int = 2000, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV data with realistic trending/ranging regimes."""
    rng = np.random.RandomState(seed)

    # Create regime changes for more interesting signals
    close = np.zeros(n)
    close[0] = 100.0

    for i in range(1, n):
        # Alternate between trending and ranging
        regime = (i // 200) % 3
        if regime == 0:
            drift = 0.001  # Uptrend
        elif regime == 1:
            drift = -0.001  # Downtrend
        else:
            drift = 0.0  # Range

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


class TestMomentumSignalCoreParity:
    """Test MomentumSignalCore produces identical signals to original vectorized code."""

    def _run_original_vectorized(
        self,
        data: pd.DataFrame,
        config: MomentumConfig,
        min_holding_bars: int = 4,
        cooldown_bars: int = 2,
        signal_confirmation: int = 1,
    ) -> np.ndarray:
        """Original vectorized implementation (copied from signal.py before refactor).

        This is the reference implementation used to verify parity.
        """
        from strategy.strategies.momentum.core import (
            calculate_adx,
            calculate_atr,
            calculate_ema,
            calculate_roc,
            calculate_sma,
        )

        n = len(data)
        signals = np.zeros(n)
        prices = data["close"].values
        highs = data["high"].values
        lows = data["low"].values
        volumes = data["volume"].values

        roc = calculate_roc(prices, config.roc_period)
        ema_f = calculate_ema(prices, config.ema_fast)
        ema_s = calculate_ema(prices, config.ema_slow)
        ema_t = calculate_ema(prices, config.ema_trend)
        atr = calculate_atr(highs, lows, prices, config.atr_period)
        vol_sma = calculate_sma(volumes, config.volume_sma_period)
        adx = calculate_adx(highs, lows, prices, config.adx_period)

        position = 0
        entry_bar = 0
        entry_price = 0.0
        trailing_stop = 0.0
        cooldown_until = 0
        signal_count = {1: 0, -1: 0}

        start_bar = max(
            config.ema_trend,
            config.roc_period,
            config.atr_period + 1,
            config.volume_sma_period,
        )

        for i in range(start_bar, n):
            price = prices[i]
            if (
                np.isnan(roc[i])
                or np.isnan(ema_f[i])
                or np.isnan(ema_s[i])
                or np.isnan(ema_t[i])
                or np.isnan(atr[i])
                or np.isnan(vol_sma[i])
            ):
                continue

            current_roc = roc[i]
            current_atr = atr[i]
            vol_ok = volumes[i] > vol_sma[i] * config.volume_threshold
            current_adx = adx[i] if not np.isnan(adx[i]) else 0.0
            is_trending = current_adx >= config.adx_trend_threshold

            if not is_trending and position != 0:
                if i - entry_bar >= min_holding_bars:
                    signals[i] = 2  # CLOSE
                    position = 0
                    entry_price = 0.0
                    trailing_stop = 0.0
                    cooldown_until = i + cooldown_bars
                    continue

            if position != 0 and entry_price > 0:
                is_long = position == 1
                if is_long:
                    pnl_pct = (price - entry_price) / entry_price
                else:
                    pnl_pct = (entry_price - price) / entry_price
                if pnl_pct < -config.stop_loss_pct:
                    signals[i] = 2
                    position = 0
                    entry_price = 0.0
                    trailing_stop = 0.0
                    cooldown_until = i + cooldown_bars
                    continue

                if is_long:
                    new_stop = price - current_atr * config.atr_multiplier
                    trailing_stop = max(trailing_stop, new_stop)
                    if price < trailing_stop:
                        signals[i] = 2
                        position = 0
                        entry_price = 0.0
                        trailing_stop = 0.0
                        cooldown_until = i + cooldown_bars
                        continue
                else:
                    new_stop = price + current_atr * config.atr_multiplier
                    if trailing_stop == 0.0:
                        trailing_stop = new_stop
                    else:
                        trailing_stop = min(trailing_stop, new_stop)
                    if price > trailing_stop:
                        signals[i] = 2
                        position = 0
                        entry_price = 0.0
                        trailing_stop = 0.0
                        cooldown_until = i + cooldown_bars
                        continue

                if is_long:
                    if current_roc < 0 or ema_f[i] < ema_s[i]:
                        if i - entry_bar >= min_holding_bars:
                            signals[i] = 2
                            position = 0
                            entry_price = 0.0
                            trailing_stop = 0.0
                            cooldown_until = i + cooldown_bars
                            continue
                else:
                    if current_roc > 0 or ema_f[i] > ema_s[i]:
                        if i - entry_bar >= min_holding_bars:
                            signals[i] = 2
                            position = 0
                            entry_price = 0.0
                            trailing_stop = 0.0
                            cooldown_until = i + cooldown_bars
                            continue

            raw_signal = 0
            long_ok = (
                is_trending
                and current_roc > config.roc_threshold
                and ema_f[i] > ema_s[i]
                and price > ema_t[i]
                and vol_ok
            )
            short_ok = (
                is_trending
                and current_roc < -config.roc_threshold
                and ema_f[i] < ema_s[i]
                and price < ema_t[i]
                and vol_ok
            )
            if long_ok:
                raw_signal = 1
            elif short_ok:
                raw_signal = -1

            if i < cooldown_until:
                continue

            if raw_signal == 1:
                signal_count[1] += 1
                signal_count[-1] = 0
            elif raw_signal == -1:
                signal_count[-1] += 1
                signal_count[1] = 0
            else:
                signal_count[1] = 0
                signal_count[-1] = 0

            confirmed_signal = 0
            if signal_count[1] >= signal_confirmation:
                confirmed_signal = 1
            elif signal_count[-1] >= signal_confirmation:
                confirmed_signal = -1

            if confirmed_signal == 1:
                if position == -1 and i - entry_bar >= min_holding_bars:
                    signals[i] = 2
                    position = 0
                    entry_price = 0.0
                    trailing_stop = 0.0
                    cooldown_until = i + cooldown_bars
                elif position == 0:
                    signals[i] = 1
                    position = 1
                    entry_bar = i
                    entry_price = price
                    trailing_stop = price - current_atr * config.atr_multiplier
            elif confirmed_signal == -1:
                if position == 1 and i - entry_bar >= min_holding_bars:
                    signals[i] = 2
                    position = 0
                    entry_price = 0.0
                    trailing_stop = 0.0
                    cooldown_until = i + cooldown_bars
                elif position == 0:
                    signals[i] = -1
                    position = -1
                    entry_bar = i
                    entry_price = price
                    trailing_stop = price + current_atr * config.atr_multiplier

        return signals

    def test_default_config_parity(self):
        """MomentumSignalCore should produce same signals as original with default config."""
        data = _generate_trending_ohlcv(2000, seed=42)
        config = MomentumConfig()

        # Original vectorized
        vec_signals = self._run_original_vectorized(data, config)

        # New streaming core
        core = MomentumSignalCore(config)
        n = len(data)
        core_signals = np.zeros(n)
        for i in range(n):
            core_signals[i] = core.update(
                close=data["close"].values[i],
                high=data["high"].values[i],
                low=data["low"].values[i],
                volume=data["volume"].values[i],
            )

        # Compare
        mismatches = np.where(vec_signals != core_signals)[0]
        assert len(mismatches) == 0, (
            f"Signal mismatch at {len(mismatches)} indices. "
            f"First 10: {mismatches[:10].tolist()}\n"
            f"Vec signals at mismatches: {vec_signals[mismatches[:10]].tolist()}\n"
            f"Core signals at mismatches: {core_signals[mismatches[:10]].tolist()}"
        )

    @pytest.mark.parametrize("seed", [1, 17, 99, 123, 456])
    def test_random_data_parity(self, seed):
        """Parity across multiple random datasets."""
        data = _generate_trending_ohlcv(1500, seed=seed)
        config = MomentumConfig()

        vec_signals = self._run_original_vectorized(data, config)

        core = MomentumSignalCore(config)
        core_signals = np.zeros(len(data))
        for i in range(len(data)):
            core_signals[i] = core.update(
                close=data["close"].values[i],
                high=data["high"].values[i],
                low=data["low"].values[i],
                volume=data["volume"].values[i],
            )

        mismatches = np.where(vec_signals != core_signals)[0]
        assert len(mismatches) == 0, (
            f"Seed {seed}: {len(mismatches)} mismatches. "
            f"First 5: {mismatches[:5].tolist()}"
        )

    def test_custom_config_parity(self):
        """Parity with non-default config parameters."""
        data = _generate_trending_ohlcv(1500, seed=77)
        config = MomentumConfig(
            roc_period=10,
            roc_threshold=0.01,
            ema_fast=5,
            ema_slow=15,
            ema_trend=34,
            atr_period=10,
            atr_multiplier=2.0,
            volume_sma_period=15,
            volume_threshold=1.0,
            adx_period=10,
            adx_trend_threshold=20.0,
            stop_loss_pct=0.05,
        )

        vec_signals = self._run_original_vectorized(
            data, config, min_holding_bars=3, cooldown_bars=1, signal_confirmation=2
        )

        core = MomentumSignalCore(
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

        mismatches = np.where(vec_signals != core_signals)[0]
        assert len(mismatches) == 0, (
            f"Custom config: {len(mismatches)} mismatches. "
            f"First 5: {mismatches[:5].tolist()}"
        )

    def test_signals_have_trades(self):
        """Sanity check: the generated data should produce some trades."""
        data = _generate_trending_ohlcv(2000, seed=42)
        config = MomentumConfig()

        core = MomentumSignalCore(config)
        signals = np.zeros(len(data))
        for i in range(len(data)):
            signals[i] = core.update(
                close=data["close"].values[i],
                high=data["high"].values[i],
                low=data["low"].values[i],
                volume=data["volume"].values[i],
            )

        non_hold = np.count_nonzero(signals)
        assert non_hold > 0, "No trades generated — test data may need adjustment"


class TestMomentumSignalGeneratorUsesCore:
    """Test that the refactored MomentumSignalGenerator produces same results."""

    def test_generator_uses_core(self):
        """MomentumSignalGenerator.generate() should use MomentumSignalCore internally."""
        from strategy.strategies.momentum.signal import (
            MomentumSignalGenerator,
            MomentumTradeFilterConfig,
        )

        data = _generate_trending_ohlcv(1500, seed=42)
        config = MomentumConfig()
        filter_config = MomentumTradeFilterConfig()

        # Use the generator
        gen = MomentumSignalGenerator(config, filter_config)
        gen_signals = gen.generate(data)

        # Use core directly
        core = MomentumSignalCore(
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

        np.testing.assert_array_equal(
            gen_signals,
            core_signals,
            "MomentumSignalGenerator should produce identical results to MomentumSignalCore",
        )

    def test_generator_with_param_overrides(self):
        """MomentumSignalGenerator.generate() with params should match core with modified config."""
        from strategy.strategies.momentum.signal import (
            MomentumSignalGenerator,
            MomentumTradeFilterConfig,
        )

        data = _generate_trending_ohlcv(1500, seed=55)
        config = MomentumConfig()
        filter_config = MomentumTradeFilterConfig()
        params = {"roc_period": 10, "ema_slow": 30, "atr_multiplier": 2.0}

        gen = MomentumSignalGenerator(config, filter_config)
        gen_signals = gen.generate(data, params=params)

        modified_config = MomentumConfig(
            roc_period=10,
            ema_slow=30,
            atr_multiplier=2.0,
        )
        core = MomentumSignalCore(
            modified_config,
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
