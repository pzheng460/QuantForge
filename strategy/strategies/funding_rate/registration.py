"""Register Funding Rate Arbitrage strategy with the backtest framework."""

import dataclasses
from datetime import datetime
from typing import Dict, Optional, Tuple

from nexustrader.constants import KlineInterval
from strategy.backtest.registry import (
    HeatmapConfig,
    StrategyRegistration,
    register_strategy,
)
from strategy.strategies.funding_rate.core import FundingRateConfig
from strategy.strategies.funding_rate.signal import (
    FundingRateFilterConfig,
    FundingRateSignalGenerator,
)
from strategy.backtest.config import StrategyConfig


_FR_CONFIG_FIELDS = {f.name for f in dataclasses.fields(FundingRateConfig)}


def _split_params(params: Optional[Dict]) -> Tuple[Dict, Dict]:
    """Split mixed params dict into (config_kwargs, filter_kwargs)."""
    if not params:
        return {}, {}
    config_kw = {k: v for k, v in params.items() if k in _FR_CONFIG_FIELDS}
    filter_kw = {k: v for k, v in params.items() if k not in _FR_CONFIG_FIELDS}
    return config_kw, filter_kw


def _fr_filter_config_factory(xv, yv, params):
    """Build FundingRateFilterConfig from heatmap params."""
    min_hold = int(params.get("min_holding_bars", 1))
    cooldown = max(1, int(params.get("cooldown_bars", 1)))
    return FundingRateFilterConfig(
        min_holding_bars=min_hold,
        cooldown_bars=cooldown,
        signal_confirmation=int(params.get("signal_confirmation", 1)),
    )


def _mesa_dict_to_config(mesa: Dict, index: int) -> StrategyConfig:
    """Convert a mesa dict from heatmap_results.json to StrategyConfig."""
    extra = mesa.get("extra_params", {})

    hours_before = int(mesa.get("center_x", mesa.get("center_hours_before_funding", 2)))
    price_sma_period = int(mesa.get("center_y", mesa.get("center_price_sma_period", 50)))

    fr_config = FundingRateConfig(
        hours_before_funding=hours_before,
        price_sma_period=price_sma_period,
        min_funding_rate=float(extra.get("min_funding_rate", 0.0005)),
        max_funding_rate=float(extra.get("max_funding_rate", 0.01)),
        funding_lookback=int(extra.get("funding_lookback", 24)),
        hours_after_funding=int(extra.get("hours_after_funding", 1)),
        max_adverse_move_pct=float(extra.get("max_adverse_move_pct", 0.02)),
        position_size_pct=float(extra.get("position_size_pct", 0.30)),
        stop_loss_pct=float(extra.get("stop_loss_pct", 0.03)),
        daily_loss_limit=float(extra.get("daily_loss_limit", 0.02)),
    )

    min_hold = int(extra.get("min_holding_bars", 1))
    cooldown = max(1, int(extra.get("cooldown_bars", 1)))
    filter_config = FundingRateFilterConfig(
        min_holding_bars=min_hold,
        cooldown_bars=cooldown,
        signal_confirmation=int(extra.get("signal_confirmation", 1)),
    )

    freq_label = mesa.get("frequency_label", "")
    avg_sharpe = mesa.get("avg_sharpe", 0)
    stability = mesa.get("stability", 0)

    x_range = mesa.get("x_range", [0, 0])
    y_range = mesa.get("y_range", [0, 0])

    return StrategyConfig(
        name=f"Mesa #{index} ({freq_label})",
        description=(
            f"Auto-detected Mesa region. "
            f"HrsBefore [{x_range[0]:.0f}, {x_range[1]:.0f}], "
            f"SMA [{y_range[0]:.0f}, {y_range[1]:.0f}]"
        ),
        strategy_config=fr_config,
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

from strategy.strategies.funding_rate.core import FundingRateConfig
from strategy.strategies.funding_rate.signal import FundingRateFilterConfig

OPTIMIZED_CONFIG = FundingRateConfig(
    symbols=["BTCUSDT-PERP{suffix}"],
    min_funding_rate={float(params.get("min_funding_rate", 0.0001))},
    max_funding_rate={float(params.get("max_funding_rate", 0.01))},
    funding_lookback={int(params.get("funding_lookback", 3))},
    price_sma_period={int(params.get("price_sma_period", 20))},
    max_adverse_move_pct={float(params.get("max_adverse_move_pct", 0.005))},
    position_size_pct={float(params.get("position_size_pct", 0.10))},
    stop_loss_pct={float(params.get("stop_loss_pct", 0.02))},
    daily_loss_limit={float(params.get("daily_loss_limit", 0.03))},
    hours_before_funding={float(params.get("hours_before_funding", 2.0))},
    hours_after_funding={float(params.get("hours_after_funding", 1.0))},
)

OPTIMIZED_FILTER = FundingRateFilterConfig(
    min_holding_bars={min_hold},
    cooldown_bars={cooldown},
    signal_confirmation={int(params.get("signal_confirmation", 1))},
)
"""


register_strategy(
    StrategyRegistration(
        name="funding_rate",
        display_name="Funding Rate Arbitrage",
        signal_generator_cls=FundingRateSignalGenerator,
        config_cls=FundingRateConfig,
        filter_config_cls=FundingRateFilterConfig,
        default_interval=KlineInterval.HOUR_1,
        default_grid={
            "hours_before_funding": [1, 2, 3, 4],
            "hours_after_funding": [1, 2, 3],
            "price_sma_period": [20, 50, 100],
            "min_funding_rate": [0.0003, 0.0005, 0.001],
            "stop_loss_pct": [0.02, 0.03, 0.05],
        },
        heatmap_config=HeatmapConfig(
            x_param_name="hours_before_funding",
            y_param_name="price_sma_period",
            x_range=(1, 6),
            y_range=(10, 100),
            x_label="Hours Before Funding",
            y_label="Price SMA Period",
            third_param_choices={
                "min_funding_rate": [0.0003, 0.0005, 0.001],
                "stop_loss_pct": [0.02, 0.03, 0.05],
            },
            fixed_params={
                "max_funding_rate": 0.01,
                "funding_lookback": 24,
                "hours_after_funding": 1,
                "max_adverse_move_pct": 0.02,
                "position_size_pct": 0.30,
                "daily_loss_limit": 0.02,
            },
            filter_config_factory=_fr_filter_config_factory,
        ),
        default_filter_kwargs={},
        split_params_fn=_split_params,
        mesa_dict_to_config_fn=_mesa_dict_to_config,
        export_config_fn=_export_config,
    )
)
