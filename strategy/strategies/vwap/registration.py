"""Register VWAP Mean Reversion strategy with the backtest framework."""

import pandas as pd

from nexustrader.constants import KlineInterval
from datetime import datetime, timezone

from strategy.backtest.registry import (
    HeatmapConfig,
    LiveConfig,
    StrategyRegistration,
        ParityTestConfig,
    register_strategy,
)
from strategy.strategies.vwap.signal_core import VWAPSignalCore
from strategy.strategies._base.registration_helpers import (
    make_export_config,
    make_mesa_dict_to_config,
    make_split_params_fn,
)
from strategy.strategies._base.signal_generator import (
    COLUMNS_CLOSE_HIGH_LOW_VOLUME,
    BaseSignalGenerator,
    TradeFilterConfig,
)
from strategy.strategies._base.test_data import generate_vwap_ohlcv
from strategy.strategies.vwap.core import VWAPConfig


# ---------------------------------------------------------------------------
# Bar hook: inject ``day`` parameter for daily VWAP reset
# ---------------------------------------------------------------------------


def _vwap_bar_hook(bar_kwargs, data, index, **_kw):
    """Inject ``day`` into bar_kwargs for VWAP daily boundary detection."""
    timestamps = data.index if isinstance(data.index, pd.DatetimeIndex) else None
    if timestamps is not None:
        bar_kwargs["day"] = timestamps[index].date()
    else:
        bar_kwargs["day"] = None
    return bar_kwargs


def _make_generator(config, filter_config):
    return BaseSignalGenerator(
        config,
        filter_config,
        core_cls=VWAPSignalCore,
        update_columns=COLUMNS_CLOSE_HIGH_LOW_VOLUME,
        core_extra_filter_fields=("signal_confirmation",),
        bar_hook=_vwap_bar_hook,
    )


# ---------------------------------------------------------------------------
# Heatmap filter config factory (custom min_hold formula)
# ---------------------------------------------------------------------------


def _vwap_filter_config_factory(xv, yv, params):
    """Build TradeFilterConfig from heatmap params."""
    min_hold = int(params.get("min_holding_bars", max(2, int(yv) // 40)))
    cooldown = max(1, min_hold // 2)
    return TradeFilterConfig(
        min_holding_bars=min_hold,
        cooldown_bars=cooldown,
        signal_confirmation=int(params.get("signal_confirmation", 1)),
    )


def _vwap_pre_update_hook(core, kline):
    """Inject ``day`` parameter for VWAP daily boundary detection in live mode."""
    ts = kline.timestamp
    if hasattr(ts, "date"):
        day = ts.date()
    else:
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        day = dt.date()
    return {"day": day}



def _parity_vwap_core_bar_hook(core, data, index):
    """Supply the day argument for direct core.update() calls in parity tests."""
    import pandas as pd
    timestamps = data.index if isinstance(data.index, pd.DatetimeIndex) else None
    if timestamps is not None:
        return {"day": timestamps[index].date()}
    return {"day": None}

_mesa_dict_to_config = make_mesa_dict_to_config(
    VWAPConfig,
    TradeFilterConfig,
    "zscore_entry",
    "std_window",
    x_label="Z-Entry",
    y_label="StdWin",
)

register_strategy(
    StrategyRegistration(
        name="vwap",
        display_name="VWAP Mean Reversion",
        signal_generator_cls=_make_generator,
        config_cls=VWAPConfig,
        filter_config_cls=TradeFilterConfig,
        default_interval=KlineInterval.MINUTE_5,
        default_grid={
            "zscore_entry": [1.5, 2.0, 2.5],
            "std_window": [100, 150, 200],
            "rsi_period": [10, 14, 20],
            "rsi_oversold": [25, 30, 35],
            "stop_loss_pct": [0.02, 0.03, 0.05],
        },
        heatmap_config=HeatmapConfig(
            x_param_name="zscore_entry",
            y_param_name="std_window",
            x_range=(1.0, 4.0),
            y_range=(50, 300),
            x_label="Z-Score Entry",
            y_label="Std Window",
            third_param_choices={
                "rsi_period": [10, 14, 20],
                "rsi_oversold": [25, 30, 35],
            },
            fixed_params={
                "zscore_exit": 0.0,
                "zscore_stop": 3.5,
                "rsi_overbought": 70.0,
                "position_size_pct": 0.20,
                "stop_loss_pct": 0.03,
                "daily_loss_limit": 0.03,
            },
            filter_config_factory=_vwap_filter_config_factory,
        ),
        default_filter_kwargs={},
        split_params_fn=make_split_params_fn(VWAPConfig),
        mesa_dict_to_config_fn=_mesa_dict_to_config,
        export_config_fn=make_export_config(
            "vwap",
            VWAPConfig,
            TradeFilterConfig,
            "strategy.strategies.vwap.core",
            "strategy.strategies._base.signal_generator",
        ),
        live_config=LiveConfig(
            core_cls=VWAPSignalCore,
            update_columns=COLUMNS_CLOSE_HIGH_LOW_VOLUME,
            warmup_fn=lambda cfg: cfg.std_window + getattr(cfg, "rsi_period", 14),
            enable_stale_guard=True,
            max_kline_age_s=60.0,
            pre_update_hook=_vwap_pre_update_hook,
        ),
    
        parity_config=ParityTestConfig(
            data_generator=generate_vwap_ohlcv,
            random_seeds=(1, 17, 99),
            core_bar_hook=_parity_vwap_core_bar_hook,
            custom_config_kwargs={
                "std_window": 100,
                "rsi_period": 10,
                "zscore_entry": 1.5,
                "zscore_exit": 0.5,
                "zscore_stop": 3.0,
                "rsi_oversold": 25.0,
                "rsi_overbought": 75.0,
                "stop_loss_pct": 0.05,
            },
            custom_filter_kwargs={
                "min_holding_bars": 3,
                "cooldown_bars": 1,
                "signal_confirmation": 2,
            },
        ),
    )
)
