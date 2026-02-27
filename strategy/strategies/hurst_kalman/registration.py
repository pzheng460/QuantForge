"""Register Hurst-Kalman strategy with the backtest framework."""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict

from strategy.backtest.registry import (
    HeatmapConfig,
    StrategyRegistration,
    register_strategy,
)
from strategy.indicators.hurst_kalman import HurstKalmanSignalCore
from strategy.strategies._base.registration_helpers import (
    make_filter_config_factory,
    make_mesa_dict_to_config,
    make_split_params_fn,
)
from strategy.strategies._base.signal_generator import (
    COLUMNS_CLOSE,
    BaseSignalGenerator,
    TradeFilterConfig,
)
from strategy.strategies.hurst_kalman.core import HurstKalmanConfig


@dataclass
class HurstKalmanFilterConfig(TradeFilterConfig):
    """Trade filter config with Hurst-Kalman specific fields."""

    min_holding_bars: int = 8
    cooldown_bars: int = 4
    only_mean_reversion: bool = True


def _make_generator(config, filter_config):
    return BaseSignalGenerator(
        config,
        filter_config,
        core_cls=HurstKalmanSignalCore,
        update_columns=COLUMNS_CLOSE,
        core_extra_filter_fields=("signal_confirmation", "only_mean_reversion"),
    )


def _hk_min_hold(mesa, extra):
    """Compute min_holding_bars from hurst_window: max(2, hurst_window // 12)."""
    hurst_window = int(mesa.get("center_y", mesa.get("center_hurst_window", 100)))
    return max(2, hurst_window // 12)


def _hk_min_hold_heatmap(xv, yv, params):
    """Compute min_holding_bars for heatmap: y-axis is hurst_window."""
    return max(2, int(yv) // 12)


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
from strategy.strategies.hurst_kalman.registration import HurstKalmanFilterConfig
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
    filter_config=HurstKalmanFilterConfig(
        min_holding_bars={min_hold},
        cooldown_bars={cooldown},
        signal_confirmation=1,
        only_mean_reversion=True,
    ),
)
"""
    return code


# Pre-build helpers for use in registration and external imports (e.g. configs.py)
_mesa_dict_to_config = make_mesa_dict_to_config(
    HurstKalmanConfig,
    HurstKalmanFilterConfig,
    "zscore_entry",
    "hurst_window",
    x_label="Z-Score",
    y_label="Hurst Window",
    min_hold_from_mesa=_hk_min_hold,
)

register_strategy(
    StrategyRegistration(
        name="hurst_kalman",
        display_name="Hurst-Kalman",
        signal_generator_cls=_make_generator,
        config_cls=HurstKalmanConfig,
        filter_config_cls=HurstKalmanFilterConfig,
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
            filter_config_factory=make_filter_config_factory(
                HurstKalmanFilterConfig,
                min_hold_formula=_hk_min_hold_heatmap,
            ),
        ),
        default_filter_kwargs={"only_mean_reversion": True},
        split_params_fn=make_split_params_fn(HurstKalmanConfig),
        mesa_dict_to_config_fn=_mesa_dict_to_config,
        export_config_fn=_export_config,
    )
)
