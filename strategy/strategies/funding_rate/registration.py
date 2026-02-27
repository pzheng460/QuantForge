"""Register Funding Rate Arbitrage strategy with the backtest framework."""

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from nexustrader.constants import KlineInterval
from strategy.backtest.registry import (
    HeatmapConfig,
    StrategyRegistration,
    register_strategy,
)
from strategy.indicators.funding_rate import FundingRateSignalCore
from strategy.strategies._base.registration_helpers import (
    make_export_config,
    make_mesa_dict_to_config,
    make_split_params_fn,
)
from strategy.strategies._base.signal_generator import (
    COLUMNS_CLOSE,
    BaseSignalGenerator,
    TradeFilterConfig,
)
from strategy.strategies.funding_rate.core import FundingRateConfig


# ---------------------------------------------------------------------------
# Custom filter config (different defaults from base TradeFilterConfig)
# ---------------------------------------------------------------------------


@dataclass
class FundingRateFilterConfig(TradeFilterConfig):
    """Trade filter config with funding-rate-specific defaults."""

    min_holding_bars: int = 1
    cooldown_bars: int = 1


# ---------------------------------------------------------------------------
# Funding settlement helpers (moved from signal.py)
# ---------------------------------------------------------------------------

FUNDING_SETTLEMENT_HOURS = (0, 8, 16)


def _hours_until_next_settlement(ts: pd.Timestamp) -> float:
    """Calculate hours until the next 8h funding settlement."""
    hour = ts.hour + ts.minute / 60.0
    for settle_h in FUNDING_SETTLEMENT_HOURS:
        if settle_h > hour:
            return settle_h - hour
    return 24.0 - hour


def _hours_since_last_settlement(ts: pd.Timestamp) -> float:
    """Calculate hours since the most recent 8h funding settlement."""
    hour = ts.hour + ts.minute / 60.0
    for settle_h in reversed(FUNDING_SETTLEMENT_HOURS):
        if settle_h <= hour:
            return hour - settle_h
    return hour + 8.0


def _build_funding_rate_series(
    data_index: pd.DatetimeIndex,
    funding_rates: Optional[pd.DataFrame],
    lookback: int,
) -> np.ndarray:
    """Build per-bar average funding rate array."""
    n = len(data_index)
    avg_funding = np.full(n, 0.00001)

    if funding_rates is None or funding_rates.empty:
        avg_funding[:] = 0.000014
        return avg_funding

    fr_values = []
    fr_timestamps = []
    for ts, row in funding_rates.iterrows():
        fr_values.append(row.get("funding_rate", 0.0))
        fr_timestamps.append(ts)

    fr_values = np.array(fr_values)
    fr_timestamps = pd.DatetimeIndex(fr_timestamps)

    for i in range(n):
        bar_ts = data_index[i]
        mask = fr_timestamps <= bar_ts
        recent = fr_values[mask]
        if len(recent) > 0:
            window = recent[-lookback:] if len(recent) >= lookback else recent
            avg_funding[i] = np.mean(window)

    return avg_funding


# ---------------------------------------------------------------------------
# Hooks for BaseSignalGenerator
# ---------------------------------------------------------------------------


def _fr_pre_loop_hook(core, data, params, effective_config, generator, **_kw):
    """Build per-bar average funding rate array before the bar loop."""
    funding_lookback = int(
        params.get("funding_lookback", effective_config.funding_lookback)
    )
    fr_data = generator.funding_rates
    if fr_data is None and "_funding_rates" in params:
        fr_data = params["_funding_rates"]
    avg_funding = _build_funding_rate_series(data.index, fr_data, funding_lookback)
    generator._avg_funding = avg_funding


def _fr_bar_hook(bar_kwargs, core, data, index, generator, **_kw):
    """Inject funding rate and settlement timing into each bar."""
    core.set_funding_rate(generator._avg_funding[index])
    ts = data.index[index]
    return {
        "close": bar_kwargs["close"],
        "hours_to_next": _hours_until_next_settlement(ts),
        "hours_since_last": _hours_since_last_settlement(ts),
    }


def _make_generator(config, filter_config):
    return BaseSignalGenerator(
        config,
        filter_config,
        core_cls=FundingRateSignalCore,
        update_columns=COLUMNS_CLOSE,
        core_extra_filter_fields=(),
        pre_loop_hook=_fr_pre_loop_hook,
        bar_hook=_fr_bar_hook,
    )


# ---------------------------------------------------------------------------
# Heatmap filter config factory
# ---------------------------------------------------------------------------


def _fr_filter_config_factory(xv, yv, params):
    """Build FundingRateFilterConfig from heatmap params."""
    min_hold = int(params.get("min_holding_bars", 1))
    cooldown = max(1, int(params.get("cooldown_bars", 1)))
    return FundingRateFilterConfig(
        min_holding_bars=min_hold,
        cooldown_bars=cooldown,
        signal_confirmation=int(params.get("signal_confirmation", 1)),
    )


# ---------------------------------------------------------------------------
# Custom mesa min_hold formula
# ---------------------------------------------------------------------------


def _fr_min_hold_from_mesa(mesa, extra):
    return int(extra.get("min_holding_bars", 1))


register_strategy(
    StrategyRegistration(
        name="funding_rate",
        display_name="Funding Rate Arbitrage",
        signal_generator_cls=_make_generator,
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
        split_params_fn=make_split_params_fn(FundingRateConfig),
        mesa_dict_to_config_fn=make_mesa_dict_to_config(
            FundingRateConfig,
            FundingRateFilterConfig,
            "hours_before_funding",
            "price_sma_period",
            x_label="HrsBefore",
            y_label="SMA",
            min_hold_from_mesa=_fr_min_hold_from_mesa,
        ),
        export_config_fn=make_export_config(
            "funding_rate",
            FundingRateConfig,
            FundingRateFilterConfig,
            "strategy.strategies.funding_rate.core",
            "strategy.strategies.funding_rate.registration",
        ),
    )
)
