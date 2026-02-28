"""Register EMA Crossover + Regime Filter strategy with the backtest framework."""

from nexustrader.constants import KlineInterval
from strategy.backtest.registry import (
    HeatmapConfig,
    StrategyRegistration,
    register_strategy,
)
from strategy.indicators.regime_ema import RegimeEMASignalCore
from strategy.strategies._base.registration_helpers import (
    make_export_config,
    make_filter_config_factory,
    make_mesa_dict_to_config,
    make_split_params_fn,
)
from strategy.strategies._base.signal_generator import (
    COLUMNS_CLOSE_HIGH_LOW,
    BaseSignalGenerator,
    TradeFilterConfig,
)
from strategy.strategies.regime_ema.core import RegimeEMAConfig


def _make_generator(config, filter_config):
    return BaseSignalGenerator(
        config,
        filter_config,
        core_cls=RegimeEMASignalCore,
        update_columns=COLUMNS_CLOSE_HIGH_LOW,
    )


_mesa_dict_to_config = make_mesa_dict_to_config(
    RegimeEMAConfig,
    TradeFilterConfig,
    "fast_period",
    "slow_period",
    x_label="Fast",
    y_label="Slow",
)

register_strategy(
    StrategyRegistration(
        name="regime_ema",
        display_name="EMA Crossover + Regime Filter",
        signal_generator_cls=_make_generator,
        config_cls=RegimeEMAConfig,
        filter_config_cls=TradeFilterConfig,
        default_interval=KlineInterval.HOUR_1,
        default_grid={
            "fast_period": [10, 15, 20, 25],
            "slow_period": [30, 40, 50, 60],
            "adx_trend_threshold": [20, 25, 30],
            "trend_atr_threshold": [1.0, 1.5, 2.0],
            "stop_loss_pct": [0.02, 0.03, 0.05],
        },
        heatmap_config=HeatmapConfig(
            x_param_name="fast_period",
            y_param_name="slow_period",
            x_range=(5, 30),
            y_range=(20, 80),
            x_label="Fast Period",
            y_label="Slow Period",
            third_param_choices={
                "adx_trend_threshold": [20.0, 25.0, 30.0],
                "trend_atr_threshold": [1.0, 1.5, 2.0],
            },
            fixed_params={
                "atr_period": 14,
                "adx_period": 14,
                "regime_lookback": 50,
                "ranging_atr_threshold": 0.8,
                "position_size_pct": 0.20,
                "stop_loss_pct": 0.03,
                "daily_loss_limit": 0.03,
            },
            filter_config_factory=make_filter_config_factory(
                TradeFilterConfig,
                min_hold_formula=lambda xv, yv, params: int(
                    params.get("min_holding_bars", max(2, int(yv) // 10))
                ),
            ),
        ),
        default_filter_kwargs={},
        split_params_fn=make_split_params_fn(RegimeEMAConfig),
        mesa_dict_to_config_fn=_mesa_dict_to_config,
        export_config_fn=make_export_config(
            "regime_ema",
            RegimeEMAConfig,
            TradeFilterConfig,
            "strategy.strategies.regime_ema.core",
            "strategy.strategies._base.signal_generator",
        ),
    )
)
