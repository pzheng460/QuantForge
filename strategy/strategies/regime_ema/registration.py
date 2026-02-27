"""Register EMA Crossover + Regime Filter strategy with the backtest framework."""

import dataclasses
from datetime import datetime
from typing import Dict, Optional, Tuple

from nexustrader.constants import KlineInterval
from strategy.backtest.registry import (
    HeatmapConfig,
    StrategyRegistration,
    register_strategy,
)
from strategy.strategies.regime_ema.core import RegimeEMAConfig
from strategy.strategies.regime_ema.signal import (
    RegimeEMASignalGenerator,
    RegimeEMATradeFilterConfig,
)
from strategy.backtest.config import StrategyConfig


_CONFIG_FIELDS = {f.name for f in dataclasses.fields(RegimeEMAConfig)}


def _split_params(params: Optional[Dict]) -> Tuple[Dict, Dict]:
    """Split mixed params dict into (config_kwargs, filter_kwargs)."""
    if not params:
        return {}, {}
    config_kw = {k: v for k, v in params.items() if k in _CONFIG_FIELDS}
    filter_kw = {k: v for k, v in params.items() if k not in _CONFIG_FIELDS}
    return config_kw, filter_kw


def _filter_config_factory(xv, yv, params):
    """Build RegimeEMATradeFilterConfig from heatmap params."""
    min_hold = int(params.get("min_holding_bars", max(2, int(yv) // 10)))
    cooldown = max(1, min_hold // 2)
    return RegimeEMATradeFilterConfig(
        min_holding_bars=min_hold,
        cooldown_bars=cooldown,
        signal_confirmation=int(params.get("signal_confirmation", 1)),
    )


def _mesa_dict_to_config(mesa: Dict, index: int) -> StrategyConfig:
    """Convert a mesa dict from heatmap_results.json to StrategyConfig."""
    extra = mesa.get("extra_params", {})

    fast_period = int(mesa.get("center_x", mesa.get("center_fast_period", 20)))
    slow_period = int(mesa.get("center_y", mesa.get("center_slow_period", 50)))

    regime_config = RegimeEMAConfig(
        fast_period=fast_period,
        slow_period=slow_period,
        atr_period=int(extra.get("atr_period", 14)),
        adx_period=int(extra.get("adx_period", 14)),
        regime_lookback=int(extra.get("regime_lookback", 50)),
        trend_atr_threshold=float(extra.get("trend_atr_threshold", 1.5)),
        ranging_atr_threshold=float(extra.get("ranging_atr_threshold", 0.8)),
        adx_trend_threshold=float(extra.get("adx_trend_threshold", 25.0)),
        position_size_pct=float(extra.get("position_size_pct", 0.20)),
        stop_loss_pct=float(extra.get("stop_loss_pct", 0.03)),
        daily_loss_limit=float(extra.get("daily_loss_limit", 0.03)),
    )

    min_hold = int(extra.get("min_holding_bars", 4))
    cooldown = max(1, min_hold // 2)
    filter_config = RegimeEMATradeFilterConfig(
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
        strategy_config=regime_config,
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

from strategy.strategies.regime_ema.core import RegimeEMAConfig
from strategy.strategies.regime_ema.signal import RegimeEMATradeFilterConfig

OPTIMIZED_CONFIG = RegimeEMAConfig(
    symbols=["BTCUSDT-PERP{suffix}"],
    fast_period={int(params.get("fast_period", 20))},
    slow_period={int(params.get("slow_period", 50))},
    atr_period={int(params.get("atr_period", 14))},
    adx_period={int(params.get("adx_period", 14))},
    regime_lookback={int(params.get("regime_lookback", 50))},
    trend_atr_threshold={float(params.get("trend_atr_threshold", 1.5))},
    ranging_atr_threshold={float(params.get("ranging_atr_threshold", 0.8))},
    adx_trend_threshold={float(params.get("adx_trend_threshold", 25.0))},
    position_size_pct={float(params.get("position_size_pct", 0.20))},
    stop_loss_pct={float(params.get("stop_loss_pct", 0.03))},
    daily_loss_limit={float(params.get("daily_loss_limit", 0.03))},
)

OPTIMIZED_FILTER = RegimeEMATradeFilterConfig(
    min_holding_bars={min_hold},
    cooldown_bars={cooldown},
    signal_confirmation={int(params.get("signal_confirmation", 1))},
)
"""


register_strategy(
    StrategyRegistration(
        name="regime_ema",
        display_name="EMA Crossover + Regime Filter",
        signal_generator_cls=RegimeEMASignalGenerator,
        config_cls=RegimeEMAConfig,
        filter_config_cls=RegimeEMATradeFilterConfig,
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
            filter_config_factory=_filter_config_factory,
        ),
        default_filter_kwargs={},
        split_params_fn=_split_params,
        mesa_dict_to_config_fn=_mesa_dict_to_config,
        export_config_fn=_export_config,
    )
)
