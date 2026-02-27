"""Register Dual Regime strategy with the backtest framework."""

import dataclasses
from datetime import datetime
from typing import Dict, Optional, Tuple

from nexustrader.constants import KlineInterval
from strategy.backtest.registry import (
    HeatmapConfig,
    StrategyRegistration,
    register_strategy,
)
from strategy.strategies.dual_regime.core import DualRegimeConfig
from strategy.strategies.dual_regime.signal import (
    DualRegimeSignalGenerator,
    DualRegimeTradeFilterConfig,
)
from strategy.backtest.config import StrategyConfig


_DUAL_REGIME_CONFIG_FIELDS = {f.name for f in dataclasses.fields(DualRegimeConfig)}


def _split_params(params: Optional[Dict]) -> Tuple[Dict, Dict]:
    """Split mixed params dict into (config_kwargs, filter_kwargs)."""
    if not params:
        return {}, {}
    config_kw = {k: v for k, v in params.items() if k in _DUAL_REGIME_CONFIG_FIELDS}
    filter_kw = {k: v for k, v in params.items() if k not in _DUAL_REGIME_CONFIG_FIELDS}
    return config_kw, filter_kw


def _dual_regime_filter_config_factory(xv, yv, params):
    """Build DualRegimeTradeFilterConfig from heatmap params."""
    min_hold = int(params.get("min_holding_bars", max(2, int(yv) // 5)))
    cooldown = max(1, min_hold // 2)
    return DualRegimeTradeFilterConfig(
        min_holding_bars=min_hold,
        cooldown_bars=cooldown,
        signal_confirmation=int(params.get("signal_confirmation", 1)),
    )


def _mesa_dict_to_config(mesa: Dict, index: int) -> StrategyConfig:
    """Convert a mesa dict from heatmap_results.json to StrategyConfig."""
    extra = mesa.get("extra_params", {})

    bb_period = int(mesa.get("center_x", mesa.get("center_bb_period", 20)))
    bb_std = float(mesa.get("center_y", mesa.get("center_bb_std", 2.0)))

    config = DualRegimeConfig(
        # Regime detection
        adx_period=int(extra.get("adx_period", 14)),
        adx_trend_threshold=float(extra.get("adx_trend_threshold", 25.0)),
        
        # Momentum parameters (Mesa #3 validated)
        roc_period=int(extra.get("roc_period", 5)),
        roc_threshold=float(extra.get("roc_threshold", 0.02)),
        ema_fast=int(extra.get("ema_fast", 8)),
        ema_slow=int(extra.get("ema_slow", 15)),
        ema_trend=int(extra.get("ema_trend", 50)),
        atr_period=int(extra.get("atr_period", 14)),
        atr_multiplier=float(extra.get("atr_multiplier", 1.5)),
        volume_sma_period=int(extra.get("volume_sma_period", 20)),
        volume_threshold=float(extra.get("volume_threshold", 1.2)),
        
        # Bollinger Band parameters (optimized)
        bb_period=bb_period,
        bb_std=bb_std,
        
        # Risk management
        position_size_pct=float(extra.get("position_size_pct", 0.20)),
        stop_loss_pct=float(extra.get("stop_loss_pct", 0.03)),
        daily_loss_limit=float(extra.get("daily_loss_limit", 0.03)),
    )

    min_hold = int(extra.get("min_holding_bars", 4))
    cooldown = max(1, min_hold // 2)
    filter_config = DualRegimeTradeFilterConfig(
        min_holding_bars=min_hold,
        cooldown_bars=cooldown,
        signal_confirmation=int(extra.get("signal_confirmation", 1)),
    )

    freq_label = mesa.get("frequency_label", "")
    avg_sharpe = mesa.get("avg_sharpe", 0)
    stability = mesa.get("stability", 0)

    x_range = mesa.get("x_range", mesa.get("bb_period_range", [0, 0]))
    y_range = mesa.get("y_range", mesa.get("bb_std_range", [0, 0]))

    return StrategyConfig(
        name=f"Dual Regime Mesa #{index} ({freq_label})",
        description=(
            f"Auto-detected Mesa region. "
            f"BB period [{x_range[0]:.0f}, {x_range[1]:.0f}], "
            f"BB std [{y_range[0]:.1f}, {y_range[1]:.1f}]"
        ),
        strategy_config=config,
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

from strategy.strategies.dual_regime.core import DualRegimeConfig
from strategy.strategies.dual_regime.signal import DualRegimeTradeFilterConfig

OPTIMIZED_CONFIG = DualRegimeConfig(
    symbols=["BTCUSDT-PERP{suffix}"],
    # Regime Detection
    adx_period={int(params.get("adx_period", 14))},
    adx_trend_threshold={float(params.get("adx_trend_threshold", 25.0))},
    # Momentum Parameters
    roc_period={int(params.get("roc_period", 5))},
    roc_threshold={float(params.get("roc_threshold", 0.02))},
    ema_fast={int(params.get("ema_fast", 8))},
    ema_slow={int(params.get("ema_slow", 15))},
    ema_trend={int(params.get("ema_trend", 50))},
    atr_period={int(params.get("atr_period", 14))},
    atr_multiplier={float(params.get("atr_multiplier", 1.5))},
    volume_sma_period={int(params.get("volume_sma_period", 20))},
    volume_threshold={float(params.get("volume_threshold", 1.2))},
    # Bollinger Band Parameters
    bb_period={int(params.get("bb_period", 20))},
    bb_std={float(params.get("bb_std", 2.0))},
    # Risk Management
    position_size_pct={float(params.get("position_size_pct", 0.20))},
    stop_loss_pct={float(params.get("stop_loss_pct", 0.03))},
    daily_loss_limit={float(params.get("daily_loss_limit", 0.03))},
)

OPTIMIZED_FILTER = DualRegimeTradeFilterConfig(
    min_holding_bars={min_hold},
    cooldown_bars={cooldown},
    signal_confirmation={int(params.get("signal_confirmation", 1))},
)
"""


register_strategy(
    StrategyRegistration(
        name="dual_regime",
        display_name="Dual Regime (Momentum + Bollinger Band)",
        signal_generator_cls=DualRegimeSignalGenerator,
        config_cls=DualRegimeConfig,
        filter_config_cls=DualRegimeTradeFilterConfig,
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
            filter_config_factory=_dual_regime_filter_config_factory,
        ),
        default_filter_kwargs={},
        split_params_fn=_split_params,
        mesa_dict_to_config_fn=_mesa_dict_to_config,
        export_config_fn=_export_config,
    )
)