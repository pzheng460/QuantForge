"""Register SMA Funding dual-leg strategy with the backtest framework."""

from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from nexustrader.constants import KlineInterval
from strategy.backtest.registry import (
    HeatmapConfig,
    LiveConfig,
    ParityTestConfig,
    StrategyRegistration,
    register_strategy,
)
from strategy.strategies._base.registration_helpers import (
    make_export_config,
    make_filter_config_factory,
    make_mesa_dict_to_config,
    make_split_params_fn,
)
from strategy.strategies._base.signal_generator import (
    COLUMNS_CLOSE_HIGH_LOW,
    BaseSignalGenerator,
    TradeFilterConfig,
)
from strategy.strategies._base.test_data import (
    generate_funding_rates,
    generate_trending_ohlcv,
)
from strategy.strategies.sma_funding.core import SMAFundingConfig
from strategy.strategies.sma_funding.signal_core import SMAFundingSignalCore


# ---------------------------------------------------------------------------
# Funding rate helper (shared with funding_arb pattern)
# ---------------------------------------------------------------------------

FUNDING_SETTLEMENT_HOURS = (0, 8, 16)


def _build_funding_rate_series(
    data_index: pd.DatetimeIndex,
    funding_rates: Optional[pd.DataFrame],
    lookback: int,
) -> np.ndarray:
    """Build per-bar average funding rate array from settlement data."""
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
# Hooks for BaseSignalGenerator: daily SMA resampling + funding rate
# ---------------------------------------------------------------------------


def _sf_pre_loop_hook(core, data, params, effective_config, generator, **_kw):
    """Pre-compute daily SMA, is_daily_close mask, and avg funding rate array.

    Resamples 1h closes to daily, computes rolling SMA, and forward-fills
    back to the 1h index. Also builds a boolean mask marking which 1h bars
    are the last bar of each calendar day (daily-close gating).

    Funding rates are smoothed to a per-bar average using the same approach
    as the funding_arb strategy.
    """
    sma_period = effective_config.sma_period
    funding_lookback = int(
        params.get("funding_lookback", effective_config.funding_lookback)
    )

    if data.index.tz is None:
        idx = data.index.tz_localize("UTC")
    else:
        idx = data.index

    # ---- Daily SMA ----
    daily_close = data["close"].copy()
    daily_close.index = idx
    daily_close = daily_close.resample("1D").last().dropna()
    daily_sma = daily_close.rolling(sma_period).mean()

    # Forward-fill daily SMA back to 1h index
    sma_at_1h = daily_sma.reindex(idx, method="ffill")
    generator._daily_sma = sma_at_1h.values

    # ---- Daily-close mask: True for last 1h bar of each calendar day ----
    dates = idx.date
    is_daily = np.zeros(len(idx), dtype=bool)
    for i in range(len(dates) - 1):
        if dates[i] != dates[i + 1]:
            is_daily[i] = True
    is_daily[-1] = True  # Last bar of dataset always counts as daily close
    generator._is_daily_close = is_daily

    # ---- Funding rate array ----
    fr_data = generator.funding_rates
    if fr_data is None and "_funding_rates" in params:
        fr_data = params["_funding_rates"]
    avg_funding = _build_funding_rate_series(data.index, fr_data, funding_lookback)
    generator._avg_funding = avg_funding


def _sf_bar_hook(bar_kwargs, core, data, index, generator, **_kw):
    """Inject daily SMA, daily-close flag, and funding rate per bar."""
    # Inject funding rate (side effect on core)
    core.set_funding_rate(generator._avg_funding[index])

    sma_val = generator._daily_sma[index]
    return {
        "close": bar_kwargs["close"],
        "high": bar_kwargs["high"],
        "low": bar_kwargs["low"],
        "sma_value": sma_val if not np.isnan(sma_val) else None,
        "is_daily_close": bool(generator._is_daily_close[index]),
    }


# ---------------------------------------------------------------------------
# Parity test hooks
# ---------------------------------------------------------------------------


def _parity_sf_pre_generate_hook(generator, data, seed):
    """Inject synthetic funding rates into generator before generate()."""
    generator.funding_rates = generate_funding_rates(data.index, seed=seed)


def _parity_sf_pre_core_hook(core, data, seed):
    """Pre-compute daily SMA, is_daily_close mask, and funding array for core loop."""
    sma_period = core._config.sma_period
    funding_lookback = core._config.funding_lookback

    if data.index.tz is None:
        idx = data.index.tz_localize("UTC")
    else:
        idx = data.index

    # Daily SMA
    daily_close = data["close"].copy()
    daily_close.index = idx
    daily_close = daily_close.resample("1D").last().dropna()
    daily_sma = daily_close.rolling(sma_period).mean()
    sma_at_1h = daily_sma.reindex(idx, method="ffill")
    core._test_daily_sma = sma_at_1h.values

    # Daily-close mask
    dates = idx.date
    is_daily = np.zeros(len(idx), dtype=bool)
    for i in range(len(dates) - 1):
        if dates[i] != dates[i + 1]:
            is_daily[i] = True
    is_daily[-1] = True
    core._test_is_daily_close = is_daily

    # Funding rate array
    fr_data = generate_funding_rates(data.index, seed=seed)
    core._test_avg_funding = _build_funding_rate_series(
        data.index, fr_data, funding_lookback
    )


def _parity_sf_core_bar_hook(core, data, index):
    """Supply per-bar kwargs for direct core.update() in parity tests."""
    core.set_funding_rate(core._test_avg_funding[index])
    sma_val = core._test_daily_sma[index]
    return {
        "sma_value": sma_val if not np.isnan(sma_val) else None,
        "is_daily_close": bool(core._test_is_daily_close[index]),
    }


# ---------------------------------------------------------------------------
# Parity test data generator (hourly OHLCV with DatetimeIndex)
# ---------------------------------------------------------------------------


def _generate_sma_funding_ohlcv(n: int = 2000, seed: int = 42) -> pd.DataFrame:
    """Generate hourly OHLCV data with DatetimeIndex for SMA Funding testing."""
    df = generate_trending_ohlcv(n=n, seed=seed)
    timestamps = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    df.index = timestamps
    return df


# ---------------------------------------------------------------------------
# Generator factory
# ---------------------------------------------------------------------------


def _make_generator(config, filter_config):
    return BaseSignalGenerator(
        config,
        filter_config,
        core_cls=SMAFundingSignalCore,
        update_columns=COLUMNS_CLOSE_HIGH_LOW,
        core_extra_filter_fields=(),
        pre_loop_hook=_sf_pre_loop_hook,
        bar_hook=_sf_bar_hook,
    )


# ---------------------------------------------------------------------------
# Live trading helpers
# ---------------------------------------------------------------------------


def _sf_pre_update_hook(core, kline):
    """Inject is_daily_close from kline timestamp for GenericIndicator.

    For 1h klines, the bar starting at 23:00 UTC is the last bar of the day.
    """
    try:
        ts = datetime.fromtimestamp(kline.start / 1000, tz=timezone.utc)
        is_daily_close = ts.hour == 23
    except Exception:
        is_daily_close = False
    return {"is_daily_close": is_daily_close}


def _sf_on_funding_rate(strategy, funding_rate):
    """Handle funding rate events: update the signal core's funding rate.

    Injects the latest funding rate into the core so the arb leg can react.
    """
    symbol = funding_rate.symbol
    indicator = strategy._indicators.get(symbol)
    if indicator:
        indicator.core.set_funding_rate(funding_rate.rate)
        strategy.log.info(
            f"{symbol} | Funding rate: {funding_rate.rate * 100:.6f}% "
            f"| Avg: {indicator.core.avg_funding_rate * 100:.6f}% "
            f"| Leg1(trend)={indicator.core.leg1_pos} "
            f"| Leg2(arb)={indicator.core.leg2_pos}"
        )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_mesa_dict_to_config = make_mesa_dict_to_config(
    SMAFundingConfig,
    TradeFilterConfig,
    "sma_period",
    "min_funding_rate",
    x_label="SMA Period",
    y_label="Min Funding Rate",
)

register_strategy(
    StrategyRegistration(
        name="sma_funding",
        display_name="SMA Funding (Trend + Arb Dual-Leg)",
        signal_generator_cls=_make_generator,
        config_cls=SMAFundingConfig,
        filter_config_cls=TradeFilterConfig,
        default_interval=KlineInterval.HOUR_1,
        default_grid={
            "sma_period": [50, 100, 200],
            "min_funding_rate": [0.0001, 0.0003, 0.0005],
            "atr_trail_mult": [1.5, 2.0, 2.5],
            "trend_stop_loss_pct": [0.02, 0.03, 0.05],
        },
        heatmap_config=HeatmapConfig(
            x_param_name="sma_period",
            y_param_name="min_funding_rate",
            x_range=(20, 250),
            y_range=(0.0001, 0.001),
            x_label="SMA Period (days)",
            y_label="Min Funding Rate",
            third_param_choices={
                "atr_trail_mult": [1.5, 2.0, 2.5],
                "trend_stop_loss_pct": [0.02, 0.03, 0.05],
            },
            fixed_params={
                "atr_period": 14,
                "arb_stop_loss_pct": 0.02,
                "funding_lookback": 24,
                "position_size_pct": 0.80,
                "trend_size_pct": 0.80,
                "arb_size_pct": 0.20,
                "daily_loss_limit": 0.03,
            },
            filter_config_factory=make_filter_config_factory(TradeFilterConfig),
        ),
        default_filter_kwargs={},
        split_params_fn=make_split_params_fn(SMAFundingConfig),
        mesa_dict_to_config_fn=_mesa_dict_to_config,
        export_config_fn=make_export_config(
            "sma_funding",
            SMAFundingConfig,
            TradeFilterConfig,
            "strategy.strategies.sma_funding.core",
            "strategy.strategies._base.signal_generator",
        ),
        live_config=LiveConfig(
            core_cls=SMAFundingSignalCore,
            update_columns=COLUMNS_CLOSE_HIGH_LOW,
            warmup_fn=lambda cfg: cfg.sma_period * 24 + 10,  # 100 daily × 24h + buffer
            use_dual_mode=True,
            pre_update_hook=_sf_pre_update_hook,
            enable_stale_guard=True,
            max_kline_age_s=120.0,
            subscribe_funding_rate=True,
            on_funding_rate_fn=_sf_on_funding_rate,
        ),
        parity_config=ParityTestConfig(
            data_generator=_generate_sma_funding_ohlcv,
            data_size=2000,
            random_seeds=(1, 17, 99),
            core_filter_fields=(),
            custom_config_kwargs={
                "sma_period": 5,
                "atr_period": 5,
                "atr_trail_mult": 2.0,
                "min_funding_rate": 0.0003,
                "funding_lookback": 3,
                "trend_stop_loss_pct": 0.05,
                "arb_stop_loss_pct": 0.05,
            },
            custom_filter_kwargs={
                "min_holding_bars": 0,
                "cooldown_bars": 0,
            },
            trades_config_kwargs={
                "sma_period": 3,
                "atr_period": 3,
                "atr_trail_mult": 2.0,
                "min_funding_rate": 0.0001,
                "funding_lookback": 2,
                "trend_stop_loss_pct": 0.10,
                "arb_stop_loss_pct": 0.10,
            },
            trades_filter_kwargs={
                "min_holding_bars": 0,
                "cooldown_bars": 0,
            },
            trades_data_size=2000,
            pre_generate_hook=_parity_sf_pre_generate_hook,
            pre_core_hook=_parity_sf_pre_core_hook,
            core_bar_hook=_parity_sf_core_bar_hook,
        ),
    )
)
