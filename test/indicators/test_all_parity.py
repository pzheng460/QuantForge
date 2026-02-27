"""Unified parity tests for all strategies.

Auto-generates parity test classes using the factory, ensuring each
strategy's BaseSignalGenerator produces identical results to its
SignalCore when called directly.
"""

import numpy as np
import pandas as pd

from strategy.indicators.bollinger_band import BBSignalCore
from strategy.indicators.dual_regime import DualRegimeSignalCore
from strategy.indicators.ema_crossover import EMASignalCore
from strategy.indicators.funding_rate import FundingRateSignalCore
from strategy.indicators.grid_trading import GridSignalCore
from strategy.indicators.hurst_kalman import HurstKalmanSignalCore
from strategy.indicators.momentum import MomentumSignalCore
from strategy.indicators.regime_ema import RegimeEMASignalCore
from strategy.indicators.vwap import VWAPSignalCore
from strategy.strategies._base.signal_generator import (
    COLUMNS_CLOSE,
    COLUMNS_CLOSE_HIGH_LOW,
    COLUMNS_CLOSE_HIGH_LOW_VOLUME,
    BaseSignalGenerator,
    TradeFilterConfig,
)
from strategy.strategies.bollinger_band.core import BBConfig
from strategy.strategies.dual_regime.core import DualRegimeConfig
from strategy.strategies.ema_crossover.core import EMAConfig
from strategy.strategies.funding_rate.core import FundingRateConfig
from strategy.strategies.funding_rate.registration import (
    FundingRateFilterConfig,
    _build_funding_rate_series,
    _fr_bar_hook,
    _fr_pre_loop_hook,
    _hours_since_last_settlement,
    _hours_until_next_settlement,
)
from strategy.strategies.grid_trading.core import GridConfig
from strategy.strategies.hurst_kalman.core import HurstKalmanConfig
from strategy.strategies.hurst_kalman.registration import HurstKalmanFilterConfig
from strategy.strategies.momentum.core import MomentumConfig
from strategy.strategies.regime_ema.core import RegimeEMAConfig
from strategy.strategies.vwap.core import VWAPConfig
from strategy.strategies.vwap.registration import _vwap_bar_hook
from test.indicators.parity_factory import (
    generate_dual_regime_ohlcv,
    generate_funding_ohlcv,
    generate_funding_rates,
    generate_mean_reverting_ohlcv,
    generate_range_bound_ohlcv,
    generate_trending_ohlcv,
    generate_vwap_ohlcv,
    make_parity_test_class,
)


def _generate_strong_mean_reverting_ohlcv(n: int = 2000, seed: int = 42) -> pd.DataFrame:
    """Generate strongly mean-reverting OHLCV data for Hurst-Kalman testing."""
    rng = np.random.RandomState(seed)
    close = np.zeros(n)
    close[0] = 100.0
    mean_level = 100.0
    for i in range(1, n):
        close[i] = (
            close[i - 1] + 0.03 * (mean_level - close[i - 1]) + rng.normal(0, 1.0)
        )
        if i % 400 == 0:
            mean_level += rng.normal(0, 3)
    spread = rng.uniform(0.005, 0.02, n) * np.abs(close)
    high = close + spread * rng.uniform(0.3, 1.0, n)
    low = close - spread * rng.uniform(0.3, 1.0, n)
    volume = rng.uniform(500, 5000, n) * (1 + rng.uniform(0, 2, n))
    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.002, n)),
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )

# ---------------------------------------------------------------------------
# 1. EMA Crossover
# ---------------------------------------------------------------------------

TestEmaCrossoverParity = make_parity_test_class(
    name="ema_crossover",
    core_cls=EMASignalCore,
    config_cls=EMAConfig,
    filter_config_cls=TradeFilterConfig,
    generator_factory=lambda c, f: BaseSignalGenerator(
        c, f, core_cls=EMASignalCore, update_columns=COLUMNS_CLOSE
    ),
    update_columns=("close",),
    custom_config_kwargs={"fast_period": 8, "slow_period": 21, "stop_loss_pct": 0.03},
    custom_filter_kwargs={
        "min_holding_bars": 3,
        "cooldown_bars": 1,
        "signal_confirmation": 2,
    },
    data_generator=generate_trending_ohlcv,
)

# ---------------------------------------------------------------------------
# 2. Bollinger Band
# ---------------------------------------------------------------------------

TestBollingerBandParity = make_parity_test_class(
    name="bollinger_band",
    core_cls=BBSignalCore,
    config_cls=BBConfig,
    filter_config_cls=TradeFilterConfig,
    generator_factory=lambda c, f: BaseSignalGenerator(
        c, f, core_cls=BBSignalCore, update_columns=COLUMNS_CLOSE
    ),
    update_columns=("close",),
    custom_config_kwargs={
        "bb_period": 15,
        "bb_multiplier": 1.5,
        "exit_threshold": 0.2,
        "stop_loss_pct": 0.03,
        "trend_bias": "auto",
    },
    custom_filter_kwargs={
        "min_holding_bars": 3,
        "cooldown_bars": 1,
        "signal_confirmation": 2,
    },
    data_generator=generate_mean_reverting_ohlcv,
)

# ---------------------------------------------------------------------------
# 3. Regime EMA
# ---------------------------------------------------------------------------

TestRegimeEmaParity = make_parity_test_class(
    name="regime_ema",
    core_cls=RegimeEMASignalCore,
    config_cls=RegimeEMAConfig,
    filter_config_cls=TradeFilterConfig,
    generator_factory=lambda c, f: BaseSignalGenerator(
        c, f, core_cls=RegimeEMASignalCore, update_columns=COLUMNS_CLOSE_HIGH_LOW
    ),
    update_columns=("close", "high", "low"),
    custom_config_kwargs={
        "fast_period": 10,
        "slow_period": 30,
        "atr_period": 10,
        "adx_period": 10,
        "adx_trend_threshold": 20.0,
        "stop_loss_pct": 0.05,
    },
    custom_filter_kwargs={
        "min_holding_bars": 3,
        "cooldown_bars": 1,
        "signal_confirmation": 2,
    },
    trades_config_kwargs={
        "fast_period": 10,
        "slow_period": 25,
        "adx_period": 10,
        "adx_trend_threshold": 15.0,
        "trend_atr_threshold": 0.8,
    },
    trades_data_size=3000,
    data_generator=generate_trending_ohlcv,
)

# ---------------------------------------------------------------------------
# 4. Grid Trading
# ---------------------------------------------------------------------------

TestGridTradingParity = make_parity_test_class(
    name="grid_trading",
    core_cls=GridSignalCore,
    config_cls=GridConfig,
    filter_config_cls=TradeFilterConfig,
    generator_factory=lambda c, f: BaseSignalGenerator(
        c,
        f,
        core_cls=GridSignalCore,
        update_columns=COLUMNS_CLOSE_HIGH_LOW,
        core_extra_filter_fields=(),
    ),
    update_columns=("close", "high", "low"),
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
    data_generator=generate_range_bound_ohlcv,
)

# ---------------------------------------------------------------------------
# 5. Momentum
# ---------------------------------------------------------------------------

TestMomentumParity = make_parity_test_class(
    name="momentum",
    core_cls=MomentumSignalCore,
    config_cls=MomentumConfig,
    filter_config_cls=TradeFilterConfig,
    generator_factory=lambda c, f: BaseSignalGenerator(
        c, f, core_cls=MomentumSignalCore, update_columns=COLUMNS_CLOSE_HIGH_LOW_VOLUME
    ),
    update_columns=("close", "high", "low", "volume"),
    custom_config_kwargs={
        "roc_period": 10,
        "roc_threshold": 0.01,
        "ema_fast": 5,
        "ema_slow": 15,
        "ema_trend": 34,
        "atr_period": 10,
        "atr_multiplier": 2.0,
        "volume_sma_period": 15,
        "volume_threshold": 1.0,
        "adx_period": 10,
        "adx_trend_threshold": 20.0,
        "stop_loss_pct": 0.05,
    },
    custom_filter_kwargs={
        "min_holding_bars": 3,
        "cooldown_bars": 1,
        "signal_confirmation": 2,
    },
    data_generator=generate_trending_ohlcv,
)

# ---------------------------------------------------------------------------
# 6. Dual Regime
# ---------------------------------------------------------------------------

TestDualRegimeParity = make_parity_test_class(
    name="dual_regime",
    core_cls=DualRegimeSignalCore,
    config_cls=DualRegimeConfig,
    filter_config_cls=TradeFilterConfig,
    generator_factory=lambda c, f: BaseSignalGenerator(
        c,
        f,
        core_cls=DualRegimeSignalCore,
        update_columns=COLUMNS_CLOSE_HIGH_LOW_VOLUME,
    ),
    update_columns=("close", "high", "low", "volume"),
    custom_config_kwargs={
        "adx_period": 10,
        "adx_trend_threshold": 20.0,
        "roc_period": 8,
        "roc_threshold": 0.01,
        "ema_fast": 5,
        "ema_slow": 20,
        "ema_trend": 40,
        "bb_period": 15,
        "bb_std": 1.5,
        "stop_loss_pct": 0.05,
    },
    custom_filter_kwargs={
        "min_holding_bars": 3,
        "cooldown_bars": 1,
        "signal_confirmation": 2,
    },
    data_generator=generate_dual_regime_ohlcv,
)

# ---------------------------------------------------------------------------
# 7. Hurst-Kalman
# ---------------------------------------------------------------------------

TestHurstKalmanParity = make_parity_test_class(
    name="hurst_kalman",
    core_cls=HurstKalmanSignalCore,
    config_cls=HurstKalmanConfig,
    filter_config_cls=HurstKalmanFilterConfig,
    generator_factory=lambda c, f: BaseSignalGenerator(
        c,
        f,
        core_cls=HurstKalmanSignalCore,
        update_columns=COLUMNS_CLOSE,
        core_extra_filter_fields=("signal_confirmation", "only_mean_reversion"),
    ),
    update_columns=("close",),
    core_filter_fields=("signal_confirmation", "only_mean_reversion"),
    core_extra_kwargs={"only_mean_reversion": False},
    custom_config_kwargs={
        "hurst_window": 80,
        "kalman_R": 0.3,
        "kalman_Q": 1e-4,
        "zscore_window": 40,
        "zscore_entry": 1.5,
        "zscore_stop": 3.0,
        "stop_loss_pct": 0.05,
    },
    custom_filter_kwargs={
        "min_holding_bars": 4,
        "cooldown_bars": 2,
        "only_mean_reversion": False,
    },
    trades_config_kwargs={
        "hurst_window": 60,
        "zscore_window": 30,
        "zscore_entry": 1.5,
    },
    trades_filter_kwargs={
        "min_holding_bars": 4,
        "cooldown_bars": 2,
        "only_mean_reversion": False,
    },
    data_generator=_generate_strong_mean_reverting_ohlcv,
)

# ---------------------------------------------------------------------------
# 8. VWAP
# ---------------------------------------------------------------------------


def _vwap_generator_factory(config, filter_config):
    return BaseSignalGenerator(
        config,
        filter_config,
        core_cls=VWAPSignalCore,
        update_columns=COLUMNS_CLOSE_HIGH_LOW_VOLUME,
        bar_hook=_vwap_bar_hook,
    )


def _vwap_core_bar_hook(core, data, index):
    """Supply the day argument for direct core.update() calls."""
    timestamps = data.index if isinstance(data.index, pd.DatetimeIndex) else None
    if timestamps is not None:
        return {"day": timestamps[index].date()}
    return {"day": None}


TestVwapParity = make_parity_test_class(
    name="vwap",
    core_cls=VWAPSignalCore,
    config_cls=VWAPConfig,
    filter_config_cls=TradeFilterConfig,
    generator_factory=_vwap_generator_factory,
    update_columns=("close", "high", "low", "volume"),
    core_bar_hook=_vwap_core_bar_hook,
    custom_config_kwargs={
        "std_window": 100,
        "rsi_period": 10,
        "zscore_entry": 1.5,
        "zscore_exit": 0.5,
        "zscore_stop": 3.0,
        "rsi_oversold": 25.0,
        "rsi_overbought": 75.0,
        "stop_loss_pct": 0.05,
    },
    custom_filter_kwargs={
        "min_holding_bars": 3,
        "cooldown_bars": 1,
        "signal_confirmation": 2,
    },
    data_generator=generate_vwap_ohlcv,
    random_seeds=(1, 17, 99),
)

# ---------------------------------------------------------------------------
# 9. Funding Rate
# ---------------------------------------------------------------------------


def _funding_generator_factory(config, filter_config):
    return BaseSignalGenerator(
        config,
        filter_config,
        core_cls=FundingRateSignalCore,
        update_columns=COLUMNS_CLOSE,
        core_extra_filter_fields=(),
        pre_loop_hook=_fr_pre_loop_hook,
        bar_hook=_fr_bar_hook,
    )


def _funding_pre_generate_hook(generator, data, seed):
    """Inject funding rate data before generator.generate()."""
    generator.funding_rates = generate_funding_rates(data.index, seed=seed)


def _funding_pre_core_hook(core, data, seed):
    """Build and store funding rate series for direct core loop."""
    fr_data = generate_funding_rates(data.index, seed=seed)
    config = core._config
    avg_funding = _build_funding_rate_series(
        data.index, fr_data, config.funding_lookback
    )
    core._test_avg_funding = avg_funding


def _funding_core_bar_hook(core, data, index):
    """Supply funding rate and timing args for direct core.update() calls."""
    core.set_funding_rate(core._test_avg_funding[index])
    ts = data.index[index]
    return {
        "hours_to_next": _hours_until_next_settlement(ts),
        "hours_since_last": _hours_since_last_settlement(ts),
    }


TestFundingRateParity = make_parity_test_class(
    name="funding_rate",
    core_cls=FundingRateSignalCore,
    config_cls=FundingRateConfig,
    filter_config_cls=FundingRateFilterConfig,
    generator_factory=_funding_generator_factory,
    update_columns=("close",),
    core_filter_fields=(),
    pre_generate_hook=_funding_pre_generate_hook,
    pre_core_hook=_funding_pre_core_hook,
    core_bar_hook=_funding_core_bar_hook,
    data_generator=generate_funding_ohlcv,
    data_size=500,
    random_seeds=(1, 17, 99),
)
