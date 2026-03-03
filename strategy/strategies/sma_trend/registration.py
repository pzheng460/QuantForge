"""Register SMA Trend strategy with the backtest framework."""

import numpy as np
import pandas as pd

from nexustrader.constants import KlineInterval
from strategy.backtest.registry import (
    HeatmapConfig,
    LiveConfig,
    ParityTestConfig,
    StrategyRegistration,
    register_strategy,
)
from strategy.strategies._base.registration_helpers import (
    make_export_config,
    make_filter_config_factory,
    make_mesa_dict_to_config,
    make_split_params_fn,
)
from strategy.strategies._base.signal_generator import (
    COLUMNS_CLOSE,
    BaseSignalGenerator,
    TradeFilterConfig,
)
from strategy.strategies._base.test_data import generate_trending_ohlcv
from strategy.strategies.sma_trend.core import SMATrendConfig
from strategy.strategies.sma_trend.signal_core import SMATrendSignalCore


# ---------------------------------------------------------------------------
# Hooks for BaseSignalGenerator: resample 1h -> daily SMA
# ---------------------------------------------------------------------------


def _sma_pre_loop_hook(core, data, params, effective_config, generator, **_kw):
    """Resample 1h closes to daily, compute rolling SMA, forward-fill to 1h index."""
    sma_period = effective_config.sma_period

    if data.index.tz is None:
        idx = data.index.tz_localize("UTC")
    else:
        idx = data.index

    daily_close = data["close"].copy()
    daily_close.index = idx
    daily_close = daily_close.resample("1D").last().dropna()

    daily_sma = daily_close.rolling(sma_period).mean()

    # Forward-fill daily SMA back to 1h index
    sma_at_1h = daily_sma.reindex(idx, method="ffill")
    generator._daily_sma = sma_at_1h.values


def _sma_bar_hook(bar_kwargs, core, data, index, generator, **_kw):
    """Inject daily SMA value into each 1h bar."""
    sma_val = generator._daily_sma[index]
    return {
        "close": bar_kwargs["close"],
        "sma_value": sma_val if not np.isnan(sma_val) else None,
    }


# ---------------------------------------------------------------------------
# Parity test hooks
# ---------------------------------------------------------------------------


def _parity_sma_pre_generate_hook(generator, data, seed):
    """No special pre-generate setup needed for SMA trend."""
    pass


def _parity_sma_pre_core_hook(core, data, seed):
    """Pre-compute daily SMA array for direct core loop in parity tests."""
    sma_period = core._config.sma_period

    if data.index.tz is None:
        idx = data.index.tz_localize("UTC")
    else:
        idx = data.index

    daily_close = data["close"].copy()
    daily_close.index = idx
    daily_close = daily_close.resample("1D").last().dropna()
    daily_sma = daily_close.rolling(sma_period).mean()
    sma_at_1h = daily_sma.reindex(idx, method="ffill")
    core._test_daily_sma = sma_at_1h.values


def _parity_sma_core_bar_hook(core, data, index):
    """Supply pre-computed daily SMA for direct core.update() calls."""
    sma_val = core._test_daily_sma[index]
    return {
        "sma_value": sma_val if not np.isnan(sma_val) else None,
    }


def _generate_sma_ohlcv(n: int = 2000, seed: int = 42) -> pd.DataFrame:
    """Generate hourly OHLCV data with DatetimeIndex for SMA trend testing."""
    df = generate_trending_ohlcv(n=n, seed=seed)
    timestamps = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    df.index = timestamps
    return df


# ---------------------------------------------------------------------------
# Generator factory
# ---------------------------------------------------------------------------


def _make_generator(config, filter_config):
    return BaseSignalGenerator(
        config,
        filter_config,
        core_cls=SMATrendSignalCore,
        update_columns=COLUMNS_CLOSE,
        pre_loop_hook=_sma_pre_loop_hook,
        bar_hook=_sma_bar_hook,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_mesa_dict_to_config = make_mesa_dict_to_config(
    SMATrendConfig,
    TradeFilterConfig,
    "sma_period",
    "min_holding_bars",
    x_label="SMA Period",
    y_label="Min Hold",
)

register_strategy(
    StrategyRegistration(
        name="sma_trend",
        display_name="SMA Trend (Daily)",
        signal_generator_cls=_make_generator,
        config_cls=SMATrendConfig,
        filter_config_cls=TradeFilterConfig,
        default_interval=KlineInterval.HOUR_1,
        default_grid={
            "sma_period": [50, 100, 150, 200],
            "min_holding_bars": [4, 8, 16, 24],
            "cooldown_bars": [2, 4, 8],
            "signal_confirmation": [1, 2, 3],
        },
        heatmap_config=HeatmapConfig(
            x_param_name="sma_period",
            y_param_name="min_holding_bars",
            x_range=(20, 250),
            y_range=(2, 48),
            x_label="SMA Period (days)",
            y_label="Min Holding Bars (1h)",
            third_param_choices={
                "signal_confirmation": [1, 2, 3],
                "cooldown_bars": [2, 4, 8],
            },
            fixed_params={
                "position_size_pct": 1.0,
                "daily_loss_limit": 0.05,
            },
            filter_config_factory=make_filter_config_factory(TradeFilterConfig),
        ),
        default_filter_kwargs={},
        split_params_fn=make_split_params_fn(SMATrendConfig),
        mesa_dict_to_config_fn=_mesa_dict_to_config,
        export_config_fn=make_export_config(
            "sma_trend",
            SMATrendConfig,
            TradeFilterConfig,
            "strategy.strategies.sma_trend.core",
            "strategy.strategies._base.signal_generator",
        ),
        live_config=LiveConfig(
            core_cls=SMATrendSignalCore,
            update_columns=COLUMNS_CLOSE,
            warmup_fn=lambda cfg: cfg.sma_period * 24 + 48,
            use_dual_mode=True,
        ),
        parity_config=ParityTestConfig(
            data_generator=_generate_sma_ohlcv,
            data_size=2000,
            random_seeds=(1, 17, 99),
            pre_generate_hook=_parity_sma_pre_generate_hook,
            pre_core_hook=_parity_sma_pre_core_hook,
            core_bar_hook=_parity_sma_core_bar_hook,
        ),
    )
)
