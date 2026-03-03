"""Register Funding Arb (delta-neutral) strategy with the backtest framework."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from nexustrader.constants import KlineInterval, OrderSide
from strategy.backtest.registry import (
    HeatmapConfig,
    LiveConfig,
    ParityTestConfig,
    StrategyRegistration,
    register_strategy,
)
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
from strategy.strategies._base.test_data import (
    generate_funding_ohlcv,
    generate_funding_rates,
)
from strategy.strategies.funding_arb.core import FundingArbConfig
from strategy.strategies.funding_arb.signal_core import FundingArbSignalCore


# ---------------------------------------------------------------------------
# Custom filter config (different defaults from base TradeFilterConfig)
# ---------------------------------------------------------------------------


@dataclass
class FundingArbFilterConfig(TradeFilterConfig):
    """Trade filter config with funding-arb-specific defaults."""

    min_holding_bars: int = 1
    cooldown_bars: int = 1


# ---------------------------------------------------------------------------
# Funding settlement helpers (shared with funding_rate)
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
        avg_funding[:] = 0.0
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


def _fa_pre_loop_hook(core, data, params, effective_config, generator, **_kw):
    """Build per-bar average funding rate array before the bar loop."""
    funding_lookback = int(
        params.get("funding_lookback", effective_config.funding_lookback)
    )
    fr_data = generator.funding_rates
    if fr_data is None and "_funding_rates" in params:
        fr_data = params["_funding_rates"]
    avg_funding = _build_funding_rate_series(data.index, fr_data, funding_lookback)
    generator._avg_funding = avg_funding


def _fa_bar_hook(bar_kwargs, core, data, index, generator, **_kw):
    """Inject funding rate and settlement timing into each bar."""
    core.set_funding_rate(generator._avg_funding[index])
    ts = data.index[index]
    return {
        "close": bar_kwargs["close"],
        "hours_to_next": _hours_until_next_settlement(ts),
        "hours_since_last": _hours_since_last_settlement(ts),
    }


# ---------------------------------------------------------------------------
# Parity test hooks
# ---------------------------------------------------------------------------


def _parity_fa_pre_generate_hook(generator, data, seed):
    """Inject synthetic funding rates before generator.generate() in parity tests."""
    generator.funding_rates = generate_funding_rates(data.index, seed=seed)


def _parity_fa_pre_core_hook(core, data, seed):
    """Build and store funding rate series for direct core loop in parity tests."""
    fr_data = generate_funding_rates(data.index, seed=seed)
    config = core._config
    avg_funding = _build_funding_rate_series(
        data.index, fr_data, config.funding_lookback
    )
    core._test_avg_funding = avg_funding


def _parity_fa_core_bar_hook(core, data, index):
    """Supply funding rate and timing args for direct core.update() calls."""
    core.set_funding_rate(core._test_avg_funding[index])
    ts = data.index[index]
    return {
        "hours_to_next": _hours_until_next_settlement(ts),
        "hours_since_last": _hours_since_last_settlement(ts),
    }


# ---------------------------------------------------------------------------
# Generator factory
# ---------------------------------------------------------------------------


def _make_generator(config, filter_config):
    return BaseSignalGenerator(
        config,
        filter_config,
        core_cls=FundingArbSignalCore,
        update_columns=COLUMNS_CLOSE,
        core_extra_filter_fields=(),
        pre_loop_hook=_fa_pre_loop_hook,
        bar_hook=_fa_bar_hook,
    )


# ---------------------------------------------------------------------------
# Heatmap filter config factory
# ---------------------------------------------------------------------------


def _fa_filter_config_factory(xv, yv, params):
    """Build FundingArbFilterConfig from heatmap params."""
    min_hold = int(params.get("min_holding_bars", 1))
    cooldown = max(1, int(params.get("cooldown_bars", 1)))
    return FundingArbFilterConfig(
        min_holding_bars=min_hold,
        cooldown_bars=cooldown,
        signal_confirmation=int(params.get("signal_confirmation", 1)),
    )


# ---------------------------------------------------------------------------
# Custom mesa min_hold formula
# ---------------------------------------------------------------------------


def _fa_min_hold_from_mesa(mesa, extra):
    return int(extra.get("min_holding_bars", 1))


# ---------------------------------------------------------------------------
# Live trading helpers (for GenericStrategy / GenericIndicator)
# ---------------------------------------------------------------------------


def _hours_until_next_settlement_utc(now_utc: datetime) -> float:
    """Calculate hours until next 8h funding settlement (datetime version)."""
    hour = now_utc.hour + now_utc.minute / 60.0
    for settle_h in FUNDING_SETTLEMENT_HOURS:
        if settle_h > hour:
            return settle_h - hour
    return 24.0 - hour


def _hours_since_last_settlement_utc(now_utc: datetime) -> float:
    """Calculate hours since most recent 8h funding settlement (datetime version)."""
    hour = now_utc.hour + now_utc.minute / 60.0
    for settle_h in reversed(FUNDING_SETTLEMENT_HOURS):
        if settle_h <= hour:
            return hour - settle_h
    return hour + 8.0


def _fa_live_pre_update_hook(core, kline):
    """Inject settlement timing kwargs for funding arb core methods."""
    now_utc = datetime.now(timezone.utc)
    return {
        "hours_to_next": _hours_until_next_settlement_utc(now_utc),
        "hours_since_last": _hours_since_last_settlement_utc(now_utc),
    }


def _fa_on_funding_rate(strategy, funding_rate):
    """Handle funding rate events: update the signal core's funding rate."""
    symbol = funding_rate.symbol
    indicator = strategy._indicators.get(symbol)
    if indicator:
        indicator.core.set_funding_rate(funding_rate.rate)
        strategy.log.info(
            f"{symbol} | Funding rate update: {funding_rate.rate * 100:.6f}% "
            f"(avg: {indicator.core.avg_funding_rate * 100:.6f}%)"
        )


def _fa_process_signal(strategy, symbol, signal, price, current_bar):
    """Short-only signal processing: only SELL and CLOSE, no BUY."""
    from strategy.strategies._base.base_strategy import _CLOSE, _SELL

    position = strategy._positions.get(symbol)
    if not position:
        return

    if strategy._is_in_cooldown(symbol):
        return

    sig = signal.value

    if sig == _CLOSE:
        if position.side is not None and strategy._can_close_position(symbol):
            strategy._close_position(symbol, "Post-settlement close")
        return

    if sig == _SELL:
        if position.side is None:
            strategy._open_position(symbol, OrderSide.SELL, price, current_bar)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_mesa_dict_to_config = make_mesa_dict_to_config(
    FundingArbConfig,
    FundingArbFilterConfig,
    "hours_before_funding",
    "funding_lookback",
    x_label="HrsBefore",
    y_label="Lookback",
    min_hold_from_mesa=_fa_min_hold_from_mesa,
)

register_strategy(
    StrategyRegistration(
        name="funding_arb",
        display_name="Funding Rate Arb (Delta-Neutral)",
        signal_generator_cls=_make_generator,
        config_cls=FundingArbConfig,
        filter_config_cls=FundingArbFilterConfig,
        default_interval=KlineInterval.HOUR_1,
        default_grid={
            "hours_before_funding": [1, 2, 3, 4],
            "hours_after_funding": [1, 2, 3],
            "funding_lookback": [12, 24, 48],
            "min_funding_rate": [0.0002, 0.0003, 0.0005],
            "stop_loss_pct": [0.01, 0.02, 0.03],
        },
        heatmap_config=HeatmapConfig(
            x_param_name="hours_before_funding",
            y_param_name="funding_lookback",
            x_range=(1, 6),
            y_range=(6, 72),
            x_label="Hours Before Funding",
            y_label="Funding Lookback (8h periods)",
            third_param_choices={
                "min_funding_rate": [0.0002, 0.0003, 0.0005],
                "stop_loss_pct": [0.01, 0.02, 0.03],
            },
            fixed_params={
                "max_funding_rate": 0.01,
                "hours_after_funding": 1,
                "position_size_pct": 0.50,
                "daily_loss_limit": 0.02,
            },
            filter_config_factory=_fa_filter_config_factory,
        ),
        default_filter_kwargs={},
        split_params_fn=make_split_params_fn(FundingArbConfig),
        mesa_dict_to_config_fn=_mesa_dict_to_config,
        export_config_fn=make_export_config(
            "funding_arb",
            FundingArbConfig,
            FundingArbFilterConfig,
            "strategy.strategies.funding_arb.core",
            "strategy.strategies.funding_arb.registration",
        ),
        live_config=LiveConfig(
            core_cls=FundingArbSignalCore,
            update_columns=COLUMNS_CLOSE,
            warmup_fn=lambda cfg: 50,
            use_dual_mode=False,
            pre_update_hook=_fa_live_pre_update_hook,
            process_signal_fn=_fa_process_signal,
            enable_stale_guard=True,
            max_kline_age_s=120.0,
            subscribe_funding_rate=True,
            on_funding_rate_fn=_fa_on_funding_rate,
        ),
        parity_config=ParityTestConfig(
            data_generator=generate_funding_ohlcv,
            data_size=500,
            random_seeds=(1, 17, 99),
            core_filter_fields=(),
            pre_generate_hook=_parity_fa_pre_generate_hook,
            pre_core_hook=_parity_fa_pre_core_hook,
            core_bar_hook=_parity_fa_core_bar_hook,
        ),
    )
)
