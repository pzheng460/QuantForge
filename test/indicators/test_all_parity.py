"""Unified parity tests for all strategies.

Auto-discovers strategy registrations and generates parity test classes from
each strategy's parity_config metadata.  Adding a new strategy no longer
requires editing this file — just populate parity_config in the strategy's
registration.py.
"""

import strategy.strategies  # noqa: F401 — triggers auto-discovery of all registrations

from strategy.backtest.registry import get_strategy, list_strategies
from strategy.strategies._base.test_data import generate_trending_ohlcv
from test.indicators.parity_factory import make_parity_test_class

for _name in list_strategies():
    _reg = get_strategy(_name)
    if _reg.parity_config is None or _reg.live_config is None:
        continue
    _pc = _reg.parity_config
    _lc = _reg.live_config
    _cls = make_parity_test_class(
        name=_name,
        core_cls=_lc.core_cls,
        config_cls=_reg.config_cls,
        filter_config_cls=_reg.filter_config_cls,
        generator_factory=_reg.signal_generator_cls,
        update_columns=_lc.update_columns,
        core_extra_kwargs=_pc.core_extra_kwargs,
        core_filter_fields=_pc.core_filter_fields,
        custom_config_kwargs=_pc.custom_config_kwargs,
        custom_filter_kwargs=_pc.custom_filter_kwargs,
        trades_config_kwargs=_pc.trades_config_kwargs,
        trades_filter_kwargs=_pc.trades_filter_kwargs,
        trades_data_size=_pc.trades_data_size,
        data_generator=_pc.data_generator or generate_trending_ohlcv,
        data_size=_pc.data_size,
        random_seeds=_pc.random_seeds,
        pre_generate_hook=_pc.pre_generate_hook,
        core_bar_hook=_pc.core_bar_hook,
        pre_core_hook=_pc.pre_core_hook,
    )
    globals()[_cls.__name__] = _cls
