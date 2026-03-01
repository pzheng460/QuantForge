"""Register Dual Regime strategy with the backtest framework."""

from nexustrader.constants import KlineInterval
from strategy.backtest.registry import (
    HeatmapConfig,
    LiveConfig,
    StrategyRegistration,
        ParityTestConfig,
    register_strategy,
)
from strategy.strategies.dual_regime.signal_core import DualRegimeSignalCore
from strategy.strategies._base.registration_helpers import (
    make_export_config,
    make_filter_config_factory,
    make_mesa_dict_to_config,
    make_split_params_fn,
)
from strategy.strategies._base.signal_generator import (
    COLUMNS_CLOSE_HIGH_LOW_VOLUME,
    BaseSignalGenerator,
    TradeFilterConfig,
)
from strategy.strategies._base.test_data import generate_dual_regime_ohlcv
from strategy.strategies.dual_regime.core import DualRegimeConfig


def _make_generator(config, filter_config):
    return BaseSignalGenerator(
        config,
        filter_config,
        core_cls=DualRegimeSignalCore,
        update_columns=COLUMNS_CLOSE_HIGH_LOW_VOLUME,
    )


register_strategy(
    StrategyRegistration(
        name="dual_regime",
        display_name="Dual Regime (Momentum + Bollinger Band)",
        signal_generator_cls=_make_generator,
        config_cls=DualRegimeConfig,
        filter_config_cls=TradeFilterConfig,
        default_interval=KlineInterval.HOUR_1,
        default_grid={
            "bb_period": [15, 20, 30],
            "bb_std": [1.5, 2.0, 2.5],
            "adx_trend_threshold": [20, 25, 30],
            "stop_loss_pct": [0.02, 0.03],
        },
        heatmap_config=HeatmapConfig(
            x_param_name="bb_period",
            y_param_name="bb_std",
            x_range=(10, 35),
            y_range=(1.0, 3.0),
            x_label="BB Period",
            y_label="BB Std Multiplier",
            third_param_choices={
                "adx_trend_threshold": [20.0, 25.0, 30.0],
                "stop_loss_pct": [0.02, 0.03],
            },
            fixed_params={
                # Momentum parameters from Mesa #3
                "roc_period": 5,
                "roc_threshold": 0.02,
                "ema_fast": 8,
                "ema_slow": 15,
                "ema_trend": 50,
                "atr_period": 14,
                "atr_multiplier": 1.5,
                "volume_sma_period": 20,
                "volume_threshold": 1.2,
                "adx_period": 14,
                "position_size_pct": 0.20,
                "daily_loss_limit": 0.03,
                # Default values for third parameters
                "adx_trend_threshold": 25.0,
                "stop_loss_pct": 0.03,
            },
            filter_config_factory=make_filter_config_factory(TradeFilterConfig),
        ),
        default_filter_kwargs={},
        split_params_fn=make_split_params_fn(DualRegimeConfig),
        mesa_dict_to_config_fn=make_mesa_dict_to_config(
            DualRegimeConfig,
            TradeFilterConfig,
            "bb_period",
            "bb_std",
            x_label="BB Period",
            y_label="BB Std",
        ),
        export_config_fn=make_export_config(
            "dual_regime",
            DualRegimeConfig,
            TradeFilterConfig,
            "strategy.strategies.dual_regime.core",
            "strategy.strategies._base.signal_generator",
        ),
        live_config=LiveConfig(
            core_cls=DualRegimeSignalCore,
            update_columns=COLUMNS_CLOSE_HIGH_LOW_VOLUME,
            enable_stale_guard=True,
            max_kline_age_s=120.0,
        ),
    
        parity_config=ParityTestConfig(
            data_generator=generate_dual_regime_ohlcv,
            custom_config_kwargs={
                "adx_period": 10,
                "adx_trend_threshold": 20.0,
                "roc_period": 8,
                "roc_threshold": 0.01,
                "ema_fast": 5,
                "ema_slow": 20,
                "ema_trend": 40,
                "bb_period": 15,
                "bb_std": 1.5,
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
