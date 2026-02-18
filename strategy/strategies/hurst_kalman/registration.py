"""Register Hurst-Kalman strategy with the backtest framework."""

import dataclasses
from datetime import datetime
from typing import Dict, Optional, Tuple

from strategy.backtest.registry import (
    HeatmapConfig,
    StrategyRegistration,
    register_strategy,
)
from strategy.strategies.hurst_kalman.core import HurstKalmanConfig
from strategy.strategies.hurst_kalman.signal import (
    HurstKalmanSignalGenerator,
    TradeFilterConfig,
)
from strategy.backtest.config import StrategyConfig


def _split_params(params: Optional[Dict]) -> Tuple[Dict, Dict]:
    """Split mixed params dict into (config_kwargs, filter_kwargs)."""
    if not params:
        return {}, {}
    config_fields = {f.name for f in dataclasses.fields(HurstKalmanConfig)}
    config_kw = {k: v for k, v in params.items() if k in config_fields}
    filter_kw = {k: v for k, v in params.items() if k not in config_fields}
    return config_kw, filter_kw


def _mesa_dict_to_config(mesa: Dict, index: int) -> StrategyConfig:
    """Convert a mesa dict from heatmap_results.json to StrategyConfig."""
    extra = mesa.get("extra_params", {})

    zscore_entry = float(mesa.get("center_x", mesa.get("center_zscore_entry", 2.0)))
    hurst_window = int(mesa.get("center_y", mesa.get("center_hurst_window", 100)))

    hk_config = HurstKalmanConfig(
        hurst_window=hurst_window,
        zscore_entry=zscore_entry,
        kalman_R=float(extra.get("kalman_R", 0.2)),
        kalman_Q=float(extra.get("kalman_Q", 5e-05)),
        zscore_window=int(extra.get("zscore_window", 60)),
        mean_reversion_threshold=float(extra.get("mean_reversion_threshold", 0.48)),
        trend_threshold=float(extra.get("trend_threshold", 0.60)),
        position_size_pct=float(extra.get("position_size_pct", 0.10)),
        stop_loss_pct=float(extra.get("stop_loss_pct", 0.03)),
        daily_loss_limit=float(extra.get("daily_loss_limit", 0.03)),
        zscore_stop=float(extra.get("zscore_stop", 3.5)),
    )

    min_hold = max(2, hurst_window // 12)
    cooldown = max(1, min_hold // 2)
    only_mr = bool(extra.get("only_mean_reversion", True))

    filter_config = TradeFilterConfig(
        min_holding_bars=min_hold,
        cooldown_bars=cooldown,
        signal_confirmation=int(extra.get("signal_confirmation", 1)),
        only_mean_reversion=only_mr,
    )

    freq_label = mesa.get("frequency_label", "")
    avg_sharpe = mesa.get("avg_sharpe", 0)
    stability = mesa.get("stability", 0)

    x_range = mesa.get("x_range", mesa.get("zscore_entry_range", [0, 0]))
    y_range = mesa.get("y_range", mesa.get("hurst_window_range", [0, 0]))

    return StrategyConfig(
        name=f"Mesa #{index} ({freq_label})",
        description=(
            f"Auto-detected Mesa region. "
            f"Z-Score [{x_range[0]:.2f}, {x_range[1]:.2f}], "
            f"Hurst [{y_range[0]:.0f}, {y_range[1]:.0f}]"
        ),
        strategy_config=hk_config,
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
    hw = params.get("hurst_window", 100)
    min_hold = max(2, hw // 12)
    cooldown = max(1, min_hold // 2)
    suffix = profile.nexus_symbol_suffix if profile else ".BITGET"
    code = f"""
# =============================================================================
# OPTIMIZED CONFIG (Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")})
# Period: {period or "N/A"}
# Performance: {metrics.get("total_return_pct", 0):.1f}% return, {metrics.get("sharpe_ratio", 0):.2f} Sharpe
# =============================================================================

from strategy.strategies.hurst_kalman.core import HurstKalmanConfig
from strategy.strategies.hurst_kalman.signal import TradeFilterConfig
from strategy.backtest.config import StrategyConfig

OPTIMIZED_CONFIG = StrategyConfig(
    name="Optimized (Grid Search)",
    description="Auto-optimized parameters from grid search",
    strategy_config=HurstKalmanConfig(
        symbols=["BTCUSDT-PERP{suffix}"],
        hurst_window={hw},
        zscore_window=60,
        zscore_entry={params.get("zscore_entry", 2.0)},
        mean_reversion_threshold={params.get("mean_reversion_threshold", 0.48)},
        trend_threshold=0.60,
        kalman_R={params.get("kalman_R", 0.2)},
        kalman_Q=5e-05,
        position_size_pct=0.10,
        stop_loss_pct=0.03,
        daily_loss_limit=0.03,
    ),
    filter_config=TradeFilterConfig(
        min_holding_bars={min_hold},
        cooldown_bars={cooldown},
        signal_confirmation=1,
        only_mean_reversion=True,
    ),
)
"""
    return code


register_strategy(
    StrategyRegistration(
        name="hurst_kalman",
        display_name="Hurst-Kalman",
        signal_generator_cls=HurstKalmanSignalGenerator,
        config_cls=HurstKalmanConfig,
        filter_config_cls=TradeFilterConfig,
        default_grid={
            "hurst_window": [80, 100, 120],
            "zscore_entry": [1.5, 2.0, 2.5, 3.0],
            "mean_reversion_threshold": [0.40, 0.45, 0.48, 0.50],
            "kalman_R": [0.1, 0.2, 0.3],
        },
        heatmap_config=HeatmapConfig(
            x_param_name="zscore_entry",
            y_param_name="hurst_window",
            x_range=(1.5, 5.0),
            y_range=(20, 200),
            x_label="Z-Score Entry",
            y_label="Hurst Window",
            third_param_choices={
                "mean_reversion_threshold": [0.40, 0.45, 0.48, 0.50],
                "kalman_R": [0.1, 0.2, 0.3],
            },
            fixed_params={
                "kalman_R": 0.2,
                "zscore_window": 60,
                "kalman_Q": 5e-05,
                "mean_reversion_threshold": 0.48,
                "trend_threshold": 0.60,
                "stop_loss_pct": 0.03,
                "zscore_stop": 3.5,
                "position_size_pct": 0.10,
                "daily_loss_limit": 0.03,
                "only_mean_reversion": True,
            },
        ),
        default_filter_kwargs={"only_mean_reversion": True},
        split_params_fn=_split_params,
        mesa_dict_to_config_fn=_mesa_dict_to_config,
        export_config_fn=_export_config,
    )
)
