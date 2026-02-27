"""
Base Signal Generator.

Generic signal generator that replaces per-strategy signal.py boilerplate.
Delegates to any SignalCore class for bar-by-bar signal generation.
"""

import dataclasses
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple, Type

import numpy as np
import pandas as pd

# Pre-defined column tuples for common update() signatures
COLUMNS_CLOSE: Tuple[str, ...] = ("close",)
COLUMNS_CLOSE_HIGH_LOW: Tuple[str, ...] = ("close", "high", "low")
COLUMNS_CLOSE_HIGH_LOW_VOLUME: Tuple[str, ...] = ("close", "high", "low", "volume")


@dataclass
class TradeFilterConfig:
    """Universal trade filter configuration.

    Strategies with extra fields (e.g. only_mean_reversion) should
    subclass this and add the extra fields.
    """

    min_holding_bars: int = 4
    cooldown_bars: int = 2
    signal_confirmation: int = 1


class BaseSignalGenerator:
    """Generic signal generator that delegates to any SignalCore.

    Replaces all per-strategy XxxSignalGenerator classes by encoding the
    variance (which core class, which columns, which extra filter fields)
    as constructor parameters.

    Args:
        config: Strategy config dataclass instance.
        filter_config: Trade filter config dataclass instance.
        core_cls: The SignalCore class to instantiate.
        update_columns: Tuple of DataFrame column names to pass to core.update().
        core_extra_filter_fields: Extra filter fields beyond min_holding_bars
            and cooldown_bars to pass to core.__init__().
            Default: ("signal_confirmation",).
            Set to () for cores that don't accept signal_confirmation.
        pre_loop_hook: Optional callable(core, data, params, effective_config, generator)
            called once before the bar loop. Used by funding_rate to build
            the per-bar funding rate array.
        bar_hook: Optional callable(bar_kwargs, core, data, index, params,
            effective_config, generator) -> bar_kwargs.
            Called per bar to inject extra arguments. Used by VWAP for day
            and funding_rate for timing parameters.
    """

    def __init__(
        self,
        config: Any,
        filter_config: Any,
        *,
        core_cls: Type,
        update_columns: Tuple[str, ...] = COLUMNS_CLOSE,
        core_extra_filter_fields: Tuple[str, ...] = ("signal_confirmation",),
        pre_loop_hook: Optional[Callable] = None,
        bar_hook: Optional[Callable] = None,
    ):
        self.config = config
        self.filter = filter_config
        self.funding_rates: Optional[pd.DataFrame] = None

        self._core_cls = core_cls
        self._update_columns = update_columns
        self._core_extra_filter_fields = core_extra_filter_fields
        self._pre_loop_hook = pre_loop_hook
        self._bar_hook = bar_hook
        self._config_fields = {f.name for f in dataclasses.fields(type(config))}

    def generate(
        self, data: pd.DataFrame, params: Optional[Dict] = None
    ) -> np.ndarray:
        """Generate signals from OHLCV data.

        Args:
            data: OHLCV DataFrame.
            params: Optional parameter overrides.

        Returns:
            Array of signal values (0=HOLD, 1=BUY, -1=SELL, 2=CLOSE).
        """
        p = params or {}

        # Step 1: Apply config parameter overrides
        config_overrides = {}
        for field_name in self._config_fields:
            if field_name in p:
                original = getattr(self.config, field_name)
                config_overrides[field_name] = type(original)(p[field_name])
        effective_config = (
            dataclasses.replace(self.config, **config_overrides)
            if config_overrides
            else self.config
        )

        # Step 2: Build core constructor kwargs
        core_kwargs: Dict[str, Any] = {
            "config": effective_config,
            "min_holding_bars": int(
                p.get("min_holding_bars", self.filter.min_holding_bars)
            ),
            "cooldown_bars": int(
                p.get("cooldown_bars", self.filter.cooldown_bars)
            ),
        }
        for field_name in self._core_extra_filter_fields:
            default = getattr(self.filter, field_name, 1)
            val = p.get(field_name, default)
            # Preserve the type of the default
            if isinstance(default, bool):
                core_kwargs[field_name] = bool(val)
            elif isinstance(default, int):
                core_kwargs[field_name] = int(val)
            elif isinstance(default, float):
                core_kwargs[field_name] = float(val)
            else:
                core_kwargs[field_name] = val

        # Step 3: Construct core
        core = self._core_cls(**core_kwargs)

        # Step 4: Extract column arrays from DataFrame
        n = len(data)
        signals = np.zeros(n)
        col_arrays = {col: data[col].values for col in self._update_columns}

        # Step 5: Optional pre-loop hook
        if self._pre_loop_hook:
            self._pre_loop_hook(
                core=core,
                data=data,
                params=p,
                effective_config=effective_config,
                generator=self,
            )

        # Step 6: Bar loop
        for i in range(n):
            bar_kwargs = {col: col_arrays[col][i] for col in self._update_columns}

            if self._bar_hook:
                bar_kwargs = self._bar_hook(
                    bar_kwargs=bar_kwargs,
                    core=core,
                    data=data,
                    index=i,
                    params=p,
                    effective_config=effective_config,
                    generator=self,
                )

            signals[i] = core.update(**bar_kwargs)

        return signals
