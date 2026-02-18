"""Register EMA Crossover strategy with the backtest framework."""

import dataclasses
from typing import Dict, Optional, Tuple

from strategy.backtest.registry import (
    HeatmapConfig,
    StrategyRegistration,
    register_strategy,
)
from strategy.strategies.ema_crossover.core import EMAConfig
from strategy.strategies.ema_crossover.signal import (
    EMASignalGenerator,
    EMATradeFilterConfig,
)
from strategy.backtest.config import StrategyConfig


_EMA_CONFIG_FIELDS = {f.name for f in dataclasses.fields(EMAConfig)}


def _split_params(params: Optional[Dict]) -> Tuple[Dict, Dict]:
    """Split mixed params dict into (config_kwargs, filter_kwargs)."""
    if not params:
        return {}, {}
    config_kw = {k: v for k, v in params.items() if k in _EMA_CONFIG_FIELDS}
    filter_kw = {k: v for k, v in params.items() if k not in _EMA_CONFIG_FIELDS}
    return config_kw, filter_kw


def _ema_filter_config_factory(xv, yv, params):
    """Build EMATradeFilterConfig from heatmap params."""
    min_hold = int(params.get("min_holding_bars", max(2, int(yv) // 5)))
    cooldown = max(1, min_hold // 2)
    return EMATradeFilterConfig(
        min_holding_bars=min_hold,
        cooldown_bars=cooldown,
        signal_confirmation=int(params.get("signal_confirmation", 1)),
    )


def _mesa_dict_to_config(mesa: Dict, index: int) -> StrategyConfig:
    """Convert a mesa dict from heatmap_results.json to StrategyConfig."""
    extra = mesa.get("extra_params", {})

    fast_period = int(mesa.get("center_x", mesa.get("center_fast_period", 12)))
    slow_period = int(mesa.get("center_y", mesa.get("center_slow_period", 26)))

    ema_config = EMAConfig(
        fast_period=fast_period,
        slow_period=slow_period,
        position_size_pct=float(extra.get("position_size_pct", 0.10)),
        stop_loss_pct=float(extra.get("stop_loss_pct", 0.05)),
        daily_loss_limit=float(extra.get("daily_loss_limit", 0.03)),
    )

    min_hold = int(extra.get("min_holding_bars", 4))
    cooldown = max(1, min_hold // 2)
    filter_config = EMATradeFilterConfig(
        min_holding_bars=min_hold,
        cooldown_bars=cooldown,
        signal_confirmation=int(extra.get("signal_confirmation", 1)),
    )

    freq_label = mesa.get("frequency_label", "")
    avg_sharpe = mesa.get("avg_sharpe", 0)
    stability = mesa.get("stability", 0)

    x_range = mesa.get("x_range", mesa.get("fast_period_range", [0, 0]))
    y_range = mesa.get("y_range", mesa.get("slow_period_range", [0, 0]))

    return StrategyConfig(
        name=f"Mesa #{index} ({freq_label})",
        description=(
            f"Auto-detected Mesa region. "
            f"Fast [{x_range[0]:.0f}, {x_range[1]:.0f}], "
            f"Slow [{y_range[0]:.0f}, {y_range[1]:.0f}]"
        ),
        strategy_config=ema_config,
        filter_config=filter_config,
        recommended=(index == 0),
        mesa_index=index,
        frequency_label=freq_label,
        avg_sharpe=avg_sharpe,
        stability=stability,
        notes=(
            f"Avg return: {mesa.get('avg_return_pct', 0):+.1f}%/yr, "
            f"MaxDD: {mesa.get('avg_max_dd_pct', 0):.1f}%, "
            f"Trades: {mesa.get('avg_trades_yr', 0):.0f}/yr"
        ),
    )


register_strategy(
    StrategyRegistration(
        name="ema_crossover",
        display_name="EMA Crossover",
        signal_generator_cls=EMASignalGenerator,
        config_cls=EMAConfig,
        filter_config_cls=EMATradeFilterConfig,
        default_grid={
            "fast_period": [8, 12, 16, 20],
            "slow_period": [20, 26, 35, 50],
            "min_holding_bars": [8, 16, 24],
            "stop_loss_pct": [0.03, 0.05, 0.07],
        },
        heatmap_config=HeatmapConfig(
            x_param_name="fast_period",
            y_param_name="slow_period",
            x_range=(5, 30),
            y_range=(15, 60),
            x_label="Fast Period",
            y_label="Slow Period",
            third_param_choices={
                "min_holding_bars": [4, 8, 16],
                "stop_loss_pct": [0.03, 0.05, 0.07],
            },
            fixed_params={
                "position_size_pct": 0.10,
                "stop_loss_pct": 0.05,
                "daily_loss_limit": 0.03,
            },
            filter_config_factory=_ema_filter_config_factory,
        ),
        default_filter_kwargs={},
        split_params_fn=_split_params,
        mesa_dict_to_config_fn=_mesa_dict_to_config,
    )
)
