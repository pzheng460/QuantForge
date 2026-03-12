"""Register Dynamic Grid strategy with the backtest framework."""

from quantforge.constants import KlineInterval
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
    COLUMNS_CLOSE_HIGH_LOW,
    BaseSignalGenerator,
    TradeFilterConfig,
)
from strategy.strategies._base.test_data import generate_range_bound_ohlcv
from strategy.strategies.dynamic_grid.core import DynamicGridConfig
from strategy.strategies.dynamic_grid.signal_core import DynamicGridSignalCore


# ---------------------------------------------------------------------------
# Generator factory
# ---------------------------------------------------------------------------


def _make_generator(config, filter_config):
    return BaseSignalGenerator(
        config,
        filter_config,
        core_cls=DynamicGridSignalCore,
        update_columns=COLUMNS_CLOSE_HIGH_LOW,
        core_extra_filter_fields=(),
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_mesa_dict_to_config = make_mesa_dict_to_config(
    DynamicGridConfig,
    TradeFilterConfig,
    "grid_count",
    "atr_multiplier",
    x_label="Grid Count",
    y_label="ATR Multiplier",
)

register_strategy(
    StrategyRegistration(
        name="dynamic_grid",
        display_name="Dynamic Grid (Volatility-Adaptive Leverage)",
        signal_generator_cls=_make_generator,
        config_cls=DynamicGridConfig,
        filter_config_cls=TradeFilterConfig,
        default_interval=KlineInterval.HOUR_1,
        default_grid={
            "grid_count": [5, 8, 10],
            "atr_multiplier": [2.0, 3.0, 4.0],
            "low_vol_mult": [3.0, 4.0, 5.0],
            "adx_trend_threshold": [25.0, 30.0, 35.0],
        },
        heatmap_config=HeatmapConfig(
            x_param_name="grid_count",
            y_param_name="atr_multiplier",
            x_range=(3, 15),
            y_range=(1.5, 5.0),
            x_label="Grid Count",
            y_label="ATR Multiplier",
            third_param_choices={
                "low_vol_mult": [3.0, 4.0, 5.0],
                "adx_trend_threshold": [25.0, 30.0, 35.0],
                "stop_loss_pct": [0.03, 0.05, 0.08],
            },
            fixed_params={
                "sma_period": 50,
                "atr_period": 14,
                "recalc_period": 24,
                "entry_lines": 2,
                "profit_lines": 1,
                "position_size_pct": 0.20,
                "base_leverage_mult": 2.0,
                "vol_low_threshold": 0.01,
                "vol_med_threshold": 0.02,
                "vol_high_threshold": 0.03,
                "med_vol_mult": 2.0,
                "normal_vol_mult": 1.0,
                "high_vol_mult": 0.5,
                "adx_period": 14,
                "trend_leverage_reduction": 0.5,
                "atr_sma_period": 20,
                "vol_expanding_reduction": 0.25,
                "daily_loss_limit": 0.02,
            },
            filter_config_factory=make_filter_config_factory(TradeFilterConfig),
        ),
        default_filter_kwargs={},
        split_params_fn=make_split_params_fn(DynamicGridConfig),
        mesa_dict_to_config_fn=_mesa_dict_to_config,
        export_config_fn=make_export_config(
            "dynamic_grid",
            DynamicGridConfig,
            TradeFilterConfig,
            "strategy.strategies.dynamic_grid.core",
            "strategy.strategies._base.signal_generator",
        ),
        live_config=LiveConfig(
            core_cls=DynamicGridSignalCore,
            update_columns=COLUMNS_CLOSE_HIGH_LOW,
            warmup_fn=lambda cfg: (
                max(cfg.sma_period, cfg.atr_period, cfg.adx_period, cfg.atr_sma_period)
                + 10
            ),
            use_dual_mode=True,
            enable_stale_guard=True,
            max_kline_age_s=120.0,
        ),
        parity_config=ParityTestConfig(
            data_generator=generate_range_bound_ohlcv,
            data_size=1500,
            random_seeds=(1, 17, 99),
            core_filter_fields=(),
            custom_config_kwargs={
                "grid_count": 8,
                "atr_multiplier": 3.0,
                "sma_period": 20,
                "atr_period": 5,
                "adx_period": 5,
                "atr_sma_period": 10,
                "recalc_period": 24,
                "entry_lines": 2,
                "profit_lines": 1,
                "stop_loss_pct": 0.05,
                "vol_low_threshold": 0.01,
                "vol_med_threshold": 0.02,
                "vol_high_threshold": 0.03,
                "low_vol_mult": 4.0,
                "med_vol_mult": 2.0,
                "normal_vol_mult": 1.0,
                "high_vol_mult": 0.5,
                "adx_trend_threshold": 30.0,
                "trend_leverage_reduction": 0.5,
                "vol_expanding_reduction": 0.25,
            },
            custom_filter_kwargs={"min_holding_bars": 2, "cooldown_bars": 1},
            trades_config_kwargs={
                "grid_count": 5,
                "atr_multiplier": 4.0,
                "sma_period": 10,
                "atr_period": 5,
                "adx_period": 5,
                "atr_sma_period": 5,
                "recalc_period": 12,
                "entry_lines": 1,
                "profit_lines": 1,
                "stop_loss_pct": 0.10,
                "vol_low_threshold": 0.05,  # generous threshold → more low-vol regime
                "low_vol_mult": 5.0,
                "med_vol_mult": 3.0,
                "normal_vol_mult": 2.0,
                "high_vol_mult": 1.0,
                "adx_trend_threshold": 50.0,  # high threshold → trend filter rarely triggers
                "trend_leverage_reduction": 0.1,
                "vol_expanding_reduction": 0.1,
            },
            trades_filter_kwargs={"min_holding_bars": 0, "cooldown_bars": 0},
            trades_data_size=2000,
        ),
    )
)
