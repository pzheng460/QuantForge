"""Register Bollinger Band strategy with the backtest framework."""

import dataclasses
from datetime import datetime
from typing import Dict, Optional, Tuple

from strategy.backtest.registry import (
    HeatmapConfig,
    StrategyRegistration,
    register_strategy,
)
from strategy.strategies.bollinger_band.core import BBConfig
from strategy.strategies.bollinger_band.signal import (
    BBSignalGenerator,
    BBTradeFilterConfig,
)
from strategy.backtest.config import StrategyConfig


_BB_CONFIG_FIELDS = {f.name for f in dataclasses.fields(BBConfig)}


def _split_params(params: Optional[Dict]) -> Tuple[Dict, Dict]:
    """Split mixed params dict into (config_kwargs, filter_kwargs)."""
    if not params:
        return {}, {}
    config_kw = {k: v for k, v in params.items() if k in _BB_CONFIG_FIELDS}
    filter_kw = {k: v for k, v in params.items() if k not in _BB_CONFIG_FIELDS}
    return config_kw, filter_kw


def _bb_filter_config_factory(xv, yv, params):
    """Build BBTradeFilterConfig from heatmap params."""
    min_hold = int(params.get("min_holding_bars", max(2, int(yv) // 5)))
    cooldown = max(1, min_hold // 2)
    return BBTradeFilterConfig(
        min_holding_bars=min_hold,
        cooldown_bars=cooldown,
        signal_confirmation=int(params.get("signal_confirmation", 1)),
    )


def _mesa_dict_to_config(mesa: Dict, index: int) -> StrategyConfig:
    """Convert a mesa dict from heatmap_results.json to StrategyConfig."""
    extra = mesa.get("extra_params", {})

    bb_multiplier = float(mesa.get("center_x", mesa.get("center_bb_multiplier", 2.0)))
    bb_period = int(mesa.get("center_y", mesa.get("center_bb_period", 20)))

    trend_bias_raw = extra.get("trend_bias")
    trend_bias = trend_bias_raw if trend_bias_raw else None

    bb_config = BBConfig(
        bb_period=bb_period,
        bb_multiplier=bb_multiplier,
        exit_threshold=float(extra.get("exit_threshold", 0.3)),
        trend_bias=trend_bias,
        trend_sma_multiplier=int(extra.get("trend_sma_multiplier", 3)),
        position_size_pct=float(extra.get("position_size_pct", 0.10)),
        stop_loss_pct=float(extra.get("stop_loss_pct", 0.05)),
        daily_loss_limit=float(extra.get("daily_loss_limit", 0.03)),
    )

    min_hold = int(extra.get("min_holding_bars", 4))
    cooldown = max(1, min_hold // 2)
    filter_config = BBTradeFilterConfig(
        min_holding_bars=min_hold,
        cooldown_bars=cooldown,
        signal_confirmation=int(extra.get("signal_confirmation", 1)),
    )

    freq_label = mesa.get("frequency_label", "")
    avg_sharpe = mesa.get("avg_sharpe", 0)
    stability = mesa.get("stability", 0)

    x_range = mesa.get("x_range", mesa.get("bb_multiplier_range", [0, 0]))
    y_range = mesa.get("y_range", mesa.get("bb_period_range", [0, 0]))

    return StrategyConfig(
        name=f"Mesa #{index} ({freq_label})",
        description=(
            f"Auto-detected Mesa region. "
            f"Multiplier [{x_range[0]:.1f}, {x_range[1]:.1f}], "
            f"Period [{y_range[0]:.0f}, {y_range[1]:.0f}]"
        ),
        strategy_config=bb_config,
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


def _export_config(
    params: Dict, metrics: Dict, period: str = None, profile=None
) -> str:
    """Export optimized parameters as config code."""
    min_hold = int(params.get("min_holding_bars", 4))
    cooldown = max(1, min_hold // 2)
    suffix = profile.nexus_symbol_suffix if profile else ".BITGET"
    return f"""
# =============================================================================
# OPTIMIZED CONFIG (Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")})
# Period: {period or "N/A"}
# Performance: {metrics.get("total_return_pct", 0):.1f}% return, {metrics.get("sharpe_ratio", 0):.2f} Sharpe
# =============================================================================

from strategy.strategies.bollinger_band.core import BBConfig
from strategy.strategies.bollinger_band.signal import BBTradeFilterConfig

OPTIMIZED_CONFIG = BBConfig(
    symbols=["BTCUSDT-PERP{suffix}"],
    bb_period={int(params.get("bb_period", 20))},
    bb_multiplier={float(params.get("bb_multiplier", 2.0))},
    exit_threshold={float(params.get("exit_threshold", 0.3))},
    trend_bias={repr(params.get("trend_bias", None))},
    trend_sma_multiplier={int(params.get("trend_sma_multiplier", 3))},
    position_size_pct={float(params.get("position_size_pct", 0.10))},
    stop_loss_pct={float(params.get("stop_loss_pct", 0.05))},
    daily_loss_limit={float(params.get("daily_loss_limit", 0.03))},
)

OPTIMIZED_FILTER = BBTradeFilterConfig(
    min_holding_bars={min_hold},
    cooldown_bars={cooldown},
    signal_confirmation={int(params.get("signal_confirmation", 1))},
)
"""


register_strategy(
    StrategyRegistration(
        name="bollinger_band",
        display_name="Bollinger Band",
        signal_generator_cls=BBSignalGenerator,
        config_cls=BBConfig,
        filter_config_cls=BBTradeFilterConfig,
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
            filter_config_factory=_bb_filter_config_factory,
        ),
        default_filter_kwargs={},
        split_params_fn=_split_params,
        mesa_dict_to_config_fn=_mesa_dict_to_config,
        export_config_fn=_export_config,
    )
)
