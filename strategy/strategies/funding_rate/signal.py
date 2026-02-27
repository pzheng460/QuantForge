"""
Funding Rate Arbitrage Signal Generator.

Exchange-agnostic signal generator for the backtest framework.
Delegates to FundingRateSignalCore for bar-by-bar signal generation,
ensuring 100% parity with live trading logic.
"""

import dataclasses
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from strategy.indicators.funding_rate import FundingRateSignalCore
from strategy.strategies.funding_rate.core import FundingRateConfig


# Funding settlement hours in UTC
FUNDING_SETTLEMENT_HOURS = (0, 8, 16)


@dataclass
class FundingRateFilterConfig:
    """Configuration for trade filtering (Funding Rate specific)."""

    min_holding_bars: int = 1
    cooldown_bars: int = 1
    signal_confirmation: int = 1


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


# Fields that belong to FundingRateConfig (for splitting params)
_CONFIG_FIELDS = {f.name for f in dataclasses.fields(FundingRateConfig)}


class FundingRateSignalGenerator:
    """Generate trading signals for funding rate arbitrage.

    Delegates to FundingRateSignalCore for bar-by-bar processing.
    """

    def __init__(
        self,
        config: FundingRateConfig,
        filter_config: FundingRateFilterConfig,
    ):
        self.config = config
        self.filter = filter_config
        self.funding_rates: Optional[pd.DataFrame] = None

    def generate(
        self,
        data: pd.DataFrame,
        params: Optional[Dict] = None,
    ) -> np.ndarray:
        """Generate signals from OHLCV data.

        Args:
            data: OHLCV DataFrame with DatetimeIndex.
            params: Optional parameter overrides.

        Returns:
            Array of signal values (0=HOLD, -1=SELL, 2=CLOSE).
        """
        p = params or {}

        # Build effective config with parameter overrides
        config_overrides = {}
        for field_name in _CONFIG_FIELDS:
            if field_name in p:
                config_overrides[field_name] = type(getattr(self.config, field_name))(
                    p[field_name]
                )
        effective_config = (
            dataclasses.replace(self.config, **config_overrides)
            if config_overrides
            else self.config
        )

        min_holding = int(p.get("min_holding_bars", self.filter.min_holding_bars))
        cooldown = int(p.get("cooldown_bars", self.filter.cooldown_bars))

        # Create core
        core = FundingRateSignalCore(
            config=effective_config,
            min_holding_bars=min_holding,
            cooldown_bars=cooldown,
        )

        n = len(data)
        signals = np.zeros(n)
        prices = data["close"].values

        # Build per-bar funding rate
        funding_lookback = int(
            p.get("funding_lookback", effective_config.funding_lookback)
        )
        fr_data = self.funding_rates
        if fr_data is None and params and "_funding_rates" in params:
            fr_data = params["_funding_rates"]
        avg_funding = _build_funding_rate_series(data.index, fr_data, funding_lookback)

        for i in range(n):
            # Set funding rate for current bar
            core.set_funding_rate(avg_funding[i])

            ts = data.index[i]
            hours_to_next = _hours_until_next_settlement(ts)
            hours_since_last = _hours_since_last_settlement(ts)

            signals[i] = core.update(
                close=prices[i],
                hours_to_next=hours_to_next,
                hours_since_last=hours_since_last,
            )

        return signals
