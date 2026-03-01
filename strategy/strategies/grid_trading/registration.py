"""Register Grid Trading strategy with the backtest framework."""

from datetime import datetime
from typing import Dict

from nexustrader.constants import KlineInterval
from strategy.backtest.config import StrategyConfig
from strategy.backtest.registry import (
    HeatmapConfig,
    LiveConfig,
    StrategyRegistration,
        ParityTestConfig,
    register_strategy,
)
from strategy.strategies.grid_trading.signal_core import GridSignalCore
from strategy.strategies._base.registration_helpers import make_split_params_fn
from strategy.strategies._base.signal_generator import (
    COLUMNS_CLOSE_HIGH_LOW,
    BaseSignalGenerator,
    TradeFilterConfig,
)
from strategy.strategies._base.test_data import generate_range_bound_ohlcv
from strategy.strategies.grid_trading.core import GridConfig


def _make_generator(config, filter_config):
    return BaseSignalGenerator(
        config,
        filter_config,
        core_cls=GridSignalCore,
        update_columns=COLUMNS_CLOSE_HIGH_LOW,
        core_extra_filter_fields=(),
    )


def _grid_filter_config_factory(xv, yv, params):
    """Build TradeFilterConfig from heatmap params."""
    min_hold = int(params.get("min_holding_bars", 1))
    cooldown = int(params.get("cooldown_bars", 0))

    return TradeFilterConfig(
        min_holding_bars=min_hold,
        cooldown_bars=cooldown,
        signal_confirmation=int(params.get("signal_confirmation", 1)),
    )


def _mesa_dict_to_config(mesa: Dict, index: int) -> StrategyConfig:
    """Convert a mesa dict from heatmap_results.json to StrategyConfig."""
    extra = mesa.get("extra_params", {})

    grid_count = int(mesa.get("center_x", mesa.get("center_grid_count", 20)))
    atr_multiplier = float(mesa.get("center_y", mesa.get("center_atr_multiplier", 2.0)))

    config = GridConfig(
        grid_count=grid_count,
        atr_multiplier=atr_multiplier,
        sma_period=int(extra.get("sma_period", 50)),
        atr_period=int(extra.get("atr_period", 14)),
        recalc_period=int(extra.get("recalc_period", 24)),
        use_bollinger=bool(extra.get("use_bollinger", False)),
        bb_period=int(extra.get("bb_period", 20)),
        bb_std_dev=float(extra.get("bb_std_dev", 2.0)),
        position_size_pct=float(extra.get("position_size_pct", 0.05)),
        max_position_pct=float(extra.get("max_position_pct", 0.80)),
        leverage=float(extra.get("leverage", 5.0)),
        stop_loss_pct=float(extra.get("stop_loss_pct", 0.05)),
        grid_deviation_limit=float(extra.get("grid_deviation_limit", 0.10)),
        daily_loss_limit=float(extra.get("daily_loss_limit", 0.03)),
        min_grid_spacing_pct=float(extra.get("min_grid_spacing_pct", 0.002)),
        grid_rebalance_threshold=float(extra.get("grid_rebalance_threshold", 0.20)),
    )

    min_hold = int(extra.get("min_holding_bars", 1))
    cooldown = int(extra.get("cooldown_bars", 0))
    filter_config = TradeFilterConfig(
        min_holding_bars=min_hold,
        cooldown_bars=cooldown,
        signal_confirmation=int(extra.get("signal_confirmation", 1)),
    )

    freq_label = mesa.get("frequency_label", "")
    avg_sharpe = mesa.get("avg_sharpe", 0)
    stability = mesa.get("stability", 0)

    x_range = mesa.get("x_range", mesa.get("grid_count_range", [0, 0]))
    y_range = mesa.get("y_range", mesa.get("atr_multiplier_range", [0, 0]))

    return StrategyConfig(
        name=f"Grid Mesa #{index} ({freq_label})",
        description=(
            f"Auto-detected Grid Trading Mesa region. "
            f"Grid count [{x_range[0]:.0f}, {x_range[1]:.0f}], "
            f"ATR multiplier [{y_range[0]:.1f}, {y_range[1]:.1f}]"
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
    min_hold = int(params.get("min_holding_bars", 1))
    cooldown = int(params.get("cooldown_bars", 0))
    suffix = profile.nexus_symbol_suffix if profile else ".BITGET"
    return f"""
# =============================================================================
# OPTIMIZED CONFIG (Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")})
# Period: {period or "N/A"}
# Performance: {metrics.get("total_return_pct", 0):.1f}% return, {metrics.get("sharpe_ratio", 0):.2f} Sharpe
# =============================================================================

from strategy.strategies.grid_trading.core import GridConfig
from strategy.strategies._base.signal_generator import TradeFilterConfig

OPTIMIZED_CONFIG = GridConfig(
    symbols=["BTCUSDT-PERP{suffix}"],
    grid_count={int(params.get("grid_count", 20))},
    atr_multiplier={float(params.get("atr_multiplier", 2.0))},
    sma_period={int(params.get("sma_period", 50))},
    atr_period={int(params.get("atr_period", 14))},
    recalc_period={int(params.get("recalc_period", 24))},
    position_size_pct={float(params.get("position_size_pct", 0.05))},
    max_position_pct={float(params.get("max_position_pct", 0.80))},
    leverage={float(params.get("leverage", 5.0))},
    stop_loss_pct={float(params.get("stop_loss_pct", 0.05))},
    grid_deviation_limit={float(params.get("grid_deviation_limit", 0.10))},
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
        name="grid_trading",
        display_name="Grid Trading",
        signal_generator_cls=_make_generator,
        config_cls=GridConfig,
        filter_config_cls=TradeFilterConfig,
        default_interval=KlineInterval.HOUR_1,
        default_grid={
            "grid_count": [5, 8, 10, 12, 15],
            "atr_multiplier": [2.0, 2.5, 3.0, 3.5, 4.0],
            "entry_lines": [1, 2, 3],
            "profit_lines": [1, 2],
            "sma_period": [20, 50],
            "atr_period": [14, 20],
            "recalc_period": [24, 48],
        },
        heatmap_config=HeatmapConfig(
            x_param_name="grid_count",
            y_param_name="atr_multiplier",
            x_range=(3, 10),
            y_range=(2.0, 5.0),
            x_label="Grid Count",
            y_label="ATR Multiplier",
            third_param_choices={
                "stop_loss_pct": [0.03, 0.05, 0.08],
            },
            fixed_params={
                "entry_lines": 1,
                "profit_lines": 2,
                "sma_period": 20,
                "atr_period": 14,
                "recalc_period": 24,
                "position_size_pct": 0.20,
                "max_position_pct": 0.80,
                "leverage": 5.0,
                "stop_loss_pct": 0.03,
                "grid_deviation_limit": 0.10,
                "daily_loss_limit": 0.03,
                "use_bollinger": False,
                "bb_period": 20,
                "bb_std_dev": 2.0,
                "min_grid_spacing_pct": 0.002,
                "grid_rebalance_threshold": 0.20,
            },
            filter_config_factory=_grid_filter_config_factory,
        ),
        default_filter_kwargs={},
        split_params_fn=make_split_params_fn(GridConfig),
        mesa_dict_to_config_fn=_mesa_dict_to_config,
        export_config_fn=_export_config,
        live_config=LiveConfig(
            core_cls=GridSignalCore,
            update_columns=COLUMNS_CLOSE_HIGH_LOW,
            warmup_fn=lambda cfg: max(cfg.sma_period, cfg.atr_period) + 10,
            use_dual_mode=True,
            enable_stale_guard=True,
        ),
    
        parity_config=ParityTestConfig(
            data_generator=generate_range_bound_ohlcv,
            core_filter_fields=(),
            custom_config_kwargs={
                "grid_count": 10,
                "atr_multiplier": 3.0,
                "sma_period": 30,
                "atr_period": 10,
                "recalc_period": 48,
                "entry_lines": 1,
                "profit_lines": 2,
                "stop_loss_pct": 0.03,
            },
            custom_filter_kwargs={"min_holding_bars": 2, "cooldown_bars": 1},
        ),
    )
)
