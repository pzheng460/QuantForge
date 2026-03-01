"""Register Bollinger Band strategy with the backtest framework."""

from strategy.backtest.registry import (
    HeatmapConfig,
    LiveConfig,
    StrategyRegistration,
        ParityTestConfig,
    register_strategy,
)
from strategy.strategies.bollinger_band.signal_core import BBSignalCore
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
from strategy.strategies._base.test_data import generate_mean_reverting_ohlcv
from strategy.strategies.bollinger_band.core import BBConfig


def _make_generator(config, filter_config):
    return BaseSignalGenerator(
        config,
        filter_config,
        core_cls=BBSignalCore,
        update_columns=COLUMNS_CLOSE,
    )


_mesa_dict_to_config = make_mesa_dict_to_config(
    BBConfig,
    TradeFilterConfig,
    "bb_multiplier",
    "bb_period",
    x_label="Multiplier",
    y_label="Period",
)

register_strategy(
    StrategyRegistration(
        name="bollinger_band",
        display_name="Bollinger Band",
        signal_generator_cls=_make_generator,
        config_cls=BBConfig,
        filter_config_cls=TradeFilterConfig,
        default_grid={
            "bb_period": [15, 20, 30, 40],
            "bb_multiplier": [1.5, 2.0, 2.5, 3.0],
            "exit_threshold": [0.2, 0.3, 0.4],
            "stop_loss_pct": [0.03, 0.05, 0.07],
        },
        heatmap_config=HeatmapConfig(
            x_param_name="bb_multiplier",
            y_param_name="bb_period",
            x_range=(1.0, 4.0),
            y_range=(10, 50),
            x_label="BB Multiplier",
            y_label="BB Period",
            third_param_choices={
                "exit_threshold": [0.2, 0.3, 0.4],
                "trend_bias": [None, "long_only", "short_only"],
            },
            fixed_params={
                "exit_threshold": 0.3,
                "position_size_pct": 0.10,
                "stop_loss_pct": 0.05,
                "daily_loss_limit": 0.03,
            },
            filter_config_factory=make_filter_config_factory(TradeFilterConfig),
        ),
        default_filter_kwargs={},
        split_params_fn=make_split_params_fn(BBConfig),
        mesa_dict_to_config_fn=_mesa_dict_to_config,
        export_config_fn=make_export_config(
            "bollinger_band",
            BBConfig,
            TradeFilterConfig,
            "strategy.strategies.bollinger_band.core",
            "strategy.strategies._base.signal_generator",
        ),
        live_config=LiveConfig(
            core_cls=BBSignalCore,
            update_columns=COLUMNS_CLOSE,
            warmup_fn=lambda cfg: (
                max(cfg.bb_period, cfg.bb_period * cfg.trend_sma_multiplier) + 10
            ),
        ),
    
        parity_config=ParityTestConfig(
            data_generator=generate_mean_reverting_ohlcv,
            custom_config_kwargs={
                "bb_period": 15,
                "bb_multiplier": 1.5,
                "exit_threshold": 0.2,
                "stop_loss_pct": 0.03,
                "trend_bias": "auto",
            },
            custom_filter_kwargs={
                "min_holding_bars": 3,
                "cooldown_bars": 1,
                "signal_confirmation": 2,
            },
        ),
    )
)
