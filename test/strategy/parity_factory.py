"""
Parity Test Factory.

Generates parity test classes from strategy metadata, eliminating
per-strategy test boilerplate. Each generated class contains 4 tests:
- test_generator_uses_core
- test_random_data_parity (parametrized with 5 seeds)
- test_custom_config_parity
- test_signals_have_trades
"""

from typing import Any, Callable, Dict, Optional, Tuple, Type

import numpy as np
import pytest

from strategy.strategies._base.test_data import (
    generate_dual_regime_ohlcv,
    generate_funding_ohlcv,
    generate_funding_rates,
    generate_mean_reverting_ohlcv,
    generate_range_bound_ohlcv,
    generate_trending_ohlcv,
    generate_vwap_ohlcv,
)

__all__ = [
    "generate_dual_regime_ohlcv",
    "generate_funding_ohlcv",
    "generate_funding_rates",
    "generate_mean_reverting_ohlcv",
    "generate_range_bound_ohlcv",
    "generate_trending_ohlcv",
    "generate_vwap_ohlcv",
    "make_parity_test_class",
]


# ---------------------------------------------------------------------------
# Test class factory
# ---------------------------------------------------------------------------


def make_parity_test_class(
    *,
    name: str,
    core_cls: Type,
    config_cls: Type,
    filter_config_cls: Type,
    generator_factory: Callable,
    update_columns: Tuple[str, ...],
    core_extra_kwargs: Optional[Dict[str, Any]] = None,
    core_filter_fields: Tuple[str, ...] = ("signal_confirmation",),
    custom_config_kwargs: Optional[Dict[str, Any]] = None,
    custom_filter_kwargs: Optional[Dict[str, Any]] = None,
    trades_config_kwargs: Optional[Dict[str, Any]] = None,
    trades_filter_kwargs: Optional[Dict[str, Any]] = None,
    trades_data_size: Optional[int] = None,
    data_generator: Callable = generate_trending_ohlcv,
    data_size: int = 1500,
    random_seeds: Tuple[int, ...] = (1, 17, 99, 123, 456),
    pre_generate_hook: Optional[Callable] = None,
    core_bar_hook: Optional[Callable] = None,
    pre_core_hook: Optional[Callable] = None,
) -> Type:
    """Create a parity test class for a strategy.

    Args:
        name: Strategy name (used for class naming).
        core_cls: The SignalCore class.
        config_cls: Strategy config dataclass.
        filter_config_cls: Trade filter config dataclass.
        generator_factory: Callable(config, filter_config) -> generator with .generate().
        update_columns: Columns passed to core.update() in standard loop.
        core_extra_kwargs: Extra kwargs for core.__init__ beyond config/min_hold/cooldown.
        core_filter_fields: Filter config fields passed to core.__init__
            (default: ("signal_confirmation",)). Set to () for cores that
            don't accept signal_confirmation.
        custom_config_kwargs: Custom config kwargs for test_custom_config_parity.
        custom_filter_kwargs: Custom filter kwargs for test_custom_config_parity.
        trades_config_kwargs: Config kwargs for test_signals_have_trades
            (uses default config if None).
        trades_filter_kwargs: Filter kwargs for test_signals_have_trades
            (uses default filter if None).
        trades_data_size: Data size for test_signals_have_trades
            (defaults to max(data_size, 2000)).
        data_generator: Callable(n, seed) -> pd.DataFrame.
        data_size: Number of bars for test data.
        random_seeds: Seeds for parametrized random test.
        pre_generate_hook: Callable(generator, data, seed) called before gen.generate().
        core_bar_hook: Callable(core, data, index) -> dict of extra kwargs for core.update().
        pre_core_hook: Callable(core, data, seed) called before core loop.

    Returns:
        A pytest-collectible test class.
    """
    extra_kw = core_extra_kwargs or {}

    def _run_core(data, config, filter_config, seed=42):
        kw = {
            "config": config,
            "min_holding_bars": filter_config.min_holding_bars,
            "cooldown_bars": filter_config.cooldown_bars,
        }
        for field_name in core_filter_fields:
            if hasattr(filter_config, field_name):
                kw[field_name] = getattr(filter_config, field_name)
        kw.update(extra_kw)
        # Also override extra_kw from filter_config
        for k in extra_kw:
            if hasattr(filter_config, k):
                kw[k] = getattr(filter_config, k)

        core = core_cls(**kw)

        if pre_core_hook:
            pre_core_hook(core, data, seed)

        n = len(data)
        signals = np.zeros(n)
        intrabar_low = data["low"].values if "low" in data.columns else None
        intrabar_high = data["high"].values if "high" in data.columns else None
        stop_loss_pct = getattr(getattr(core, "_config", None), "stop_loss_pct", None)

        for i in range(n):
            bar_kw = {col: data[col].values[i] for col in update_columns}
            if core_bar_hook:
                bar_kw.update(core_bar_hook(core, data, i))
            signals[i] = core.update(**bar_kw)

            # Mirror intrabar stop logic from BaseSignalGenerator.generate()
            if (
                stop_loss_pct
                and signals[i] != 2
                and core.position != 0
                and intrabar_low is not None
            ):
                entry = core.entry_price
                if entry > 0:
                    cooldown = getattr(core, "_cooldown_bars", 0)
                    if core.position == 1 and intrabar_low[i] <= entry * (1 - stop_loss_pct):
                        signals[i] = 2
                        core.position = 0
                        core.entry_price = 0.0
                        core.cooldown_until = core.bar_index + cooldown
                    elif core.position == -1 and intrabar_high[i] >= entry * (1 + stop_loss_pct):
                        signals[i] = 2
                        core.position = 0
                        core.entry_price = 0.0
                        core.cooldown_until = core.bar_index + cooldown

        return signals

    def _run_generator(data, config, filter_config, seed=42):
        gen = generator_factory(config, filter_config)
        if pre_generate_hook:
            pre_generate_hook(gen, data, seed)
        return gen.generate(data)

    class _ParityTest:
        def test_generator_uses_core(self):
            data = data_generator(data_size, seed=42)
            config = config_cls()
            filter_config = filter_config_cls()
            gen_signals = _run_generator(data, config, filter_config, seed=42)
            core_signals = _run_core(data, config, filter_config, seed=42)
            np.testing.assert_array_equal(gen_signals, core_signals)

        @pytest.mark.parametrize("seed", list(random_seeds))
        def test_random_data_parity(self, seed):
            data = data_generator(data_size, seed=seed)
            config = config_cls()
            filter_config = filter_config_cls()
            gen_signals = _run_generator(data, config, filter_config, seed=seed)
            core_signals = _run_core(data, config, filter_config, seed=seed)
            mismatches = np.where(gen_signals != core_signals)[0]
            assert len(mismatches) == 0, (
                f"Seed {seed}: {len(mismatches)} mismatches. "
                f"First 5: {mismatches[:5].tolist()}"
            )

        def test_custom_config_parity(self):
            data = data_generator(data_size, seed=77)
            config = config_cls(**(custom_config_kwargs or {}))
            filt_kw = custom_filter_kwargs or {
                "min_holding_bars": 3,
                "cooldown_bars": 1,
            }
            filter_config = filter_config_cls(**filt_kw)
            gen_signals = _run_generator(data, config, filter_config, seed=77)
            core_signals = _run_core(data, config, filter_config, seed=77)
            np.testing.assert_array_equal(gen_signals, core_signals)

        def test_signals_have_trades(self):
            n = trades_data_size or max(data_size, 2000)
            data = data_generator(n, seed=42)
            config = config_cls(**(trades_config_kwargs or {}))
            filter_config = filter_config_cls(**(trades_filter_kwargs or {}))
            gen_signals = _run_generator(data, config, filter_config, seed=42)
            non_hold = np.count_nonzero(gen_signals)
            assert non_hold > 0, "No trades generated — test data may need adjustment"

    # Set proper class name for pytest discovery
    class_name = f"Test{''.join(w.title() for w in name.split('_'))}Parity"
    _ParityTest.__name__ = class_name
    _ParityTest.__qualname__ = class_name

    return _ParityTest
