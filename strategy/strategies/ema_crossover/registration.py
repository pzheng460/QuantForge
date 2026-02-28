"""Register EMA Crossover strategy with the backtest framework."""

from datetime import datetime
from typing import Dict

from strategy.backtest.config import StrategyConfig
from strategy.backtest.registry import (
    HeatmapConfig,
    LiveConfig,
    StrategyRegistration,
    register_strategy,
)
from strategy.indicators.ema_crossover import EMASignalCore
from strategy.strategies._base.registration_helpers import (
    make_filter_config_factory,
    make_split_params_fn,
)
from strategy.strategies._base.signal_generator import (
    COLUMNS_CLOSE,
    BaseSignalGenerator,
    TradeFilterConfig,
)
from strategy.strategies.ema_crossover.core import EMAConfig


def _make_generator(config, filter_config):
    return BaseSignalGenerator(
        config,
        filter_config,
        core_cls=EMASignalCore,
        update_columns=COLUMNS_CLOSE,
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
    filter_config = TradeFilterConfig(
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

from strategy.strategies.ema_crossover.core import EMAConfig
from strategy.strategies._base.signal_generator import TradeFilterConfig

OPTIMIZED_CONFIG = EMAConfig(
    symbols=["BTCUSDT-PERP{suffix}"],
    fast_period={int(params.get("fast_period", 12))},
    slow_period={int(params.get("slow_period", 26))},
    position_size_pct={float(params.get("position_size_pct", 0.10))},
    stop_loss_pct={float(params.get("stop_loss_pct", 0.05))},
    daily_loss_limit={float(params.get("daily_loss_limit", 0.03))},
)

OPTIMIZED_FILTER = TradeFilterConfig(
    min_holding_bars={min_hold},
    cooldown_bars={cooldown},
    signal_confirmation={int(params.get("signal_confirmation", 1))},
)
"""


register_strategy(
    StrategyRegistration(
        name="ema_crossover",
        display_name="EMA Crossover",
        signal_generator_cls=_make_generator,
        config_cls=EMAConfig,
        filter_config_cls=TradeFilterConfig,
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
            filter_config_factory=make_filter_config_factory(TradeFilterConfig),
        ),
        default_filter_kwargs={},
        split_params_fn=make_split_params_fn(EMAConfig),
        mesa_dict_to_config_fn=_mesa_dict_to_config,
        export_config_fn=_export_config,
        live_config=LiveConfig(
            core_cls=EMASignalCore,
            update_columns=COLUMNS_CLOSE,
            warmup_fn=lambda cfg: cfg.slow_period + 10,
            use_dual_mode=True,
        ),
    )
)
