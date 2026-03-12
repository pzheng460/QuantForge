"""Register Multi-Timeframe Momentum strategy with the backtest framework."""

from quantforge.constants import KlineInterval
from strategy.backtest.registry import (
    HeatmapConfig,
    LiveConfig,
    StrategyRegistration,
        ParityTestConfig,
    register_strategy,
)
from strategy.strategies.momentum.signal_core import MomentumSignalCore
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
from strategy.strategies.momentum.core import MomentumConfig


def _make_generator(config, filter_config):
    return BaseSignalGenerator(
        config,
        filter_config,
        core_cls=MomentumSignalCore,
        update_columns=COLUMNS_CLOSE_HIGH_LOW_VOLUME,
    )


register_strategy(
    StrategyRegistration(
        name="momentum",
        display_name="Multi-Timeframe Momentum",
        signal_generator_cls=_make_generator,
        config_cls=MomentumConfig,
        filter_config_cls=TradeFilterConfig,
        default_interval=KlineInterval.HOUR_1,
        default_grid={
            "roc_period": [7, 10, 14, 21],
            "ema_slow": [15, 21, 30, 40],
            "roc_threshold": [0.01, 0.02, 0.03],
            "atr_multiplier": [1.0, 1.5, 2.0],
            "adx_trend_threshold": [20, 25, 30],
            "stop_loss_pct": [0.02, 0.03, 0.05],
        },
        heatmap_config=HeatmapConfig(
            x_param_name="roc_period",
            y_param_name="ema_slow",
            x_range=(5, 30),
            y_range=(10, 50),
            x_label="ROC Period",
            y_label="EMA Slow",
            third_param_choices={
                "roc_threshold": [0.01, 0.02, 0.03],
                "atr_multiplier": [1.0, 1.5, 2.0],
                "adx_trend_threshold": [20.0, 25.0, 30.0],
                "ema_fast": [5, 8, 13],
                "ema_trend": [34, 50, 89],
                "volume_threshold": [1.0, 1.2, 1.5],
            },
            fixed_params={
                "atr_period": 14,
                "adx_period": 14,
                "volume_sma_period": 20,
                "position_size_pct": 0.20,
                "daily_loss_limit": 0.03,
            },
            filter_config_factory=make_filter_config_factory(TradeFilterConfig),
        ),
        default_filter_kwargs={},
        split_params_fn=make_split_params_fn(MomentumConfig),
        mesa_dict_to_config_fn=make_mesa_dict_to_config(
            MomentumConfig,
            TradeFilterConfig,
            "roc_period",
            "ema_slow",
            x_label="ROC Period",
            y_label="EMA Slow",
        ),
        export_config_fn=make_export_config(
            "momentum",
            MomentumConfig,
            TradeFilterConfig,
            "strategy.strategies.momentum.core",
            "strategy.strategies._base.signal_generator",
        ),
        live_config=LiveConfig(
            core_cls=MomentumSignalCore,
            update_columns=COLUMNS_CLOSE_HIGH_LOW_VOLUME,
            warmup_fn=lambda cfg: (
                max(
                    cfg.ema_trend,
                    cfg.roc_period,
                    cfg.atr_period + 1,
                    cfg.volume_sma_period,
                    cfg.adx_period * 2 + 1,
                )
                + 10
            ),
            enable_stale_guard=True,
            max_kline_age_s=120.0,
        ),
    
        parity_config=ParityTestConfig(
            custom_config_kwargs={
                "roc_period": 10,
                "roc_threshold": 0.01,
                "ema_fast": 5,
                "ema_slow": 15,
                "ema_trend": 34,
                "atr_period": 10,
                "atr_multiplier": 2.0,
                "volume_sma_period": 15,
                "volume_threshold": 1.0,
                "adx_period": 10,
                "adx_trend_threshold": 20.0,
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
