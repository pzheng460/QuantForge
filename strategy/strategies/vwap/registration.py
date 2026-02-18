"""Register VWAP Mean Reversion strategy with the backtest framework."""

import dataclasses
from typing import Dict, Optional, Tuple

from nexustrader.constants import KlineInterval
from strategy.backtest.registry import (
    HeatmapConfig,
    StrategyRegistration,
    register_strategy,
)
from strategy.strategies.vwap.core import VWAPConfig
from strategy.strategies.vwap.signal import (
    VWAPSignalGenerator,
    VWAPTradeFilterConfig,
)
from strategy.backtest.config import StrategyConfig


_VWAP_CONFIG_FIELDS = {f.name for f in dataclasses.fields(VWAPConfig)}


def _split_params(params: Optional[Dict]) -> Tuple[Dict, Dict]:
    """Split mixed params dict into (config_kwargs, filter_kwargs)."""
    if not params:
        return {}, {}
    config_kw = {k: v for k, v in params.items() if k in _VWAP_CONFIG_FIELDS}
    filter_kw = {k: v for k, v in params.items() if k not in _VWAP_CONFIG_FIELDS}
    return config_kw, filter_kw


def _vwap_filter_config_factory(xv, yv, params):
    """Build VWAPTradeFilterConfig from heatmap params."""
    min_hold = int(params.get("min_holding_bars", max(2, int(yv) // 40)))
    cooldown = max(1, min_hold // 2)
    return VWAPTradeFilterConfig(
        min_holding_bars=min_hold,
        cooldown_bars=cooldown,
        signal_confirmation=int(params.get("signal_confirmation", 1)),
    )


def _mesa_dict_to_config(mesa: Dict, index: int) -> StrategyConfig:
    """Convert a mesa dict from heatmap_results.json to StrategyConfig."""
    extra = mesa.get("extra_params", {})

    zscore_entry = float(mesa.get("center_x", mesa.get("center_zscore_entry", 2.0)))
    std_window = int(mesa.get("center_y", mesa.get("center_std_window", 200)))

    vwap_config = VWAPConfig(
        std_window=std_window,
        zscore_entry=zscore_entry,
        rsi_period=int(extra.get("rsi_period", 14)),
        rsi_oversold=float(extra.get("rsi_oversold", 30.0)),
        rsi_overbought=float(extra.get("rsi_overbought", 70.0)),
        zscore_exit=float(extra.get("zscore_exit", 0.0)),
        zscore_stop=float(extra.get("zscore_stop", 3.5)),
        position_size_pct=float(extra.get("position_size_pct", 0.20)),
        stop_loss_pct=float(extra.get("stop_loss_pct", 0.03)),
        daily_loss_limit=float(extra.get("daily_loss_limit", 0.03)),
    )

    min_hold = int(extra.get("min_holding_bars", 4))
    cooldown = max(1, min_hold // 2)
    filter_config = VWAPTradeFilterConfig(
        min_holding_bars=min_hold,
        cooldown_bars=cooldown,
        signal_confirmation=int(extra.get("signal_confirmation", 1)),
    )

    freq_label = mesa.get("frequency_label", "")
    avg_sharpe = mesa.get("avg_sharpe", 0)
    stability = mesa.get("stability", 0)

    x_range = mesa.get("x_range", mesa.get("zscore_entry_range", [0, 0]))
    y_range = mesa.get("y_range", mesa.get("std_window_range", [0, 0]))

    return StrategyConfig(
        name=f"Mesa #{index} ({freq_label})",
        description=(
            f"Auto-detected Mesa region. "
            f"Z-Entry [{x_range[0]:.1f}, {x_range[1]:.1f}], "
            f"StdWin [{y_range[0]:.0f}, {y_range[1]:.0f}]"
        ),
        strategy_config=vwap_config,
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


def _export_config(config: StrategyConfig) -> str:
    """Generate Python config code for paper trading."""
    sc = config.strategy_config
    fc = config.filter_config
    return (
        f"VWAPConfig(\n"
        f"    std_window={sc.std_window},\n"
        f"    rsi_period={sc.rsi_period},\n"
        f"    zscore_entry={sc.zscore_entry},\n"
        f"    zscore_exit={sc.zscore_exit},\n"
        f"    zscore_stop={sc.zscore_stop},\n"
        f"    rsi_oversold={sc.rsi_oversold},\n"
        f"    rsi_overbought={sc.rsi_overbought},\n"
        f"    position_size_pct={sc.position_size_pct},\n"
        f"    stop_loss_pct={sc.stop_loss_pct},\n"
        f"    daily_loss_limit={sc.daily_loss_limit},\n"
        f")\n\n"
        f"VWAPTradeFilterConfig(\n"
        f"    min_holding_bars={fc.min_holding_bars},\n"
        f"    cooldown_bars={fc.cooldown_bars},\n"
        f"    signal_confirmation={fc.signal_confirmation},\n"
        f")"
    )


register_strategy(
    StrategyRegistration(
        name="vwap",
        display_name="VWAP Mean Reversion",
        signal_generator_cls=VWAPSignalGenerator,
        config_cls=VWAPConfig,
        filter_config_cls=VWAPTradeFilterConfig,
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
        split_params_fn=_split_params,
        mesa_dict_to_config_fn=_mesa_dict_to_config,
        export_config_fn=_export_config,
    )
)
