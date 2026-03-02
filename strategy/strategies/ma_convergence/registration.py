"""Register MA Convergence (均线密集) strategy with the backtest framework."""

from decimal import Decimal

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
    COLUMNS_CLOSE_HIGH_LOW,
    BaseSignalGenerator,
    TradeFilterConfig,
)
from strategy.strategies.ma_convergence.core import MAConvergenceConfig
from strategy.strategies.ma_convergence.signal_core import MAConvergenceSignalCore


def _make_generator(config, filter_config):
    return BaseSignalGenerator(
        config,
        filter_config,
        core_cls=MAConvergenceSignalCore,
        update_columns=COLUMNS_CLOSE_HIGH_LOW,
    )


def _ma_convergence_calc_position_size(strategy, symbol: str, price: float) -> Decimal:
    """Dynamic position sizing: risk a fixed % of equity per trade.

    position_size = (equity * risk_pct) / abs(entry_price - stop_price)

    Tight stop → bigger position.  Wide stop → smaller position.
    Falls back to fixed % of equity if stop price is not yet available.
    """
    balance = strategy._get_account_balance()
    risk_pct = strategy._config.position_size_pct  # treated as max risk fraction

    indicator = strategy._indicators.get(symbol)
    stop_price = 0.0
    if indicator is not None and hasattr(indicator, "core"):
        stop_price = getattr(indicator.core, "stop_price", 0.0)

    if stop_price > 0 and abs(price - stop_price) > 0:
        position_size = MAConvergenceSignalCore.calc_position_size(
            float(balance), price, stop_price, risk_pct
        )
    else:
        # Fallback: fixed % of equity as position value
        position_size = float(balance) * risk_pct / price

    return strategy.amount_to_precision(symbol, Decimal(str(position_size)))


register_strategy(
    StrategyRegistration(
        name="ma_convergence",
        display_name="MA Convergence (均线密集)",
        signal_generator_cls=_make_generator,
        config_cls=MAConvergenceConfig,
        filter_config_cls=TradeFilterConfig,
        default_interval=KlineInterval.MINUTE_15,
        default_grid={
            "convergence_threshold": [1.0, 1.5, 2.0, 2.5],
            "reward_ratio": [3.0, 5.0, 7.0],
            "atr_period": [14, 20],
            "stop_loss_pct": [0.03, 0.05, 0.07],
            "min_holding_bars": [4, 8, 16],
        },
        heatmap_config=HeatmapConfig(
            x_param_name="convergence_threshold",
            y_param_name="reward_ratio",
            x_range=(0.5, 4.0),
            y_range=(2.0, 10.0),
            x_label="Convergence Threshold",
            y_label="Reward Ratio",
            third_param_choices={
                "stop_loss_pct": [0.03, 0.05, 0.07],
                "atr_period": [14, 20],
                "min_holding_bars": [4, 8, 16],
            },
            fixed_params={
                "position_size_pct": 0.20,
                "daily_loss_limit": 0.03,
                "exit_method": "risk_reward",
                "ma_period_1": 20,
                "ma_period_2": 60,
                "ma_period_3": 120,
            },
            filter_config_factory=make_filter_config_factory(TradeFilterConfig),
        ),
        default_filter_kwargs={},
        split_params_fn=make_split_params_fn(MAConvergenceConfig),
        mesa_dict_to_config_fn=make_mesa_dict_to_config(
            MAConvergenceConfig,
            TradeFilterConfig,
            "convergence_threshold",
            "reward_ratio",
            x_label="Convergence Threshold",
            y_label="Reward Ratio",
        ),
        export_config_fn=make_export_config(
            "ma_convergence",
            MAConvergenceConfig,
            TradeFilterConfig,
            "strategy.strategies.ma_convergence.core",
            "strategy.strategies._base.signal_generator",
        ),
        live_config=LiveConfig(
            core_cls=MAConvergenceSignalCore,
            update_columns=COLUMNS_CLOSE_HIGH_LOW,
            warmup_fn=lambda cfg: cfg.ma_period_3 + cfg.atr_period + 10,
            use_dual_mode=True,
            calculate_position_size_fn=_ma_convergence_calc_position_size,
        ),
        parity_config=ParityTestConfig(
            custom_config_kwargs={
                "convergence_threshold": 1.5,
                "reward_ratio": 5.0,
                "atr_period": 14,
                "stop_loss_pct": 0.05,
                "ma_period_1": 20,
                "ma_period_2": 60,
                "ma_period_3": 120,
            },
            custom_filter_kwargs={
                "min_holding_bars": 3,
                "cooldown_bars": 1,
                "signal_confirmation": 1,
            },
        ),
    )
)
