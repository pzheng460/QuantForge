"""
Funding Rate Arbitrage Signal Generator.

Generates trading signals for the vectorized backtest engine.

Key logic:
  1. At each bar, determine the hours until the next 8h funding settlement
     (00:00, 08:00, 16:00 UTC).
  2. If we are within `hours_before_funding` of settlement AND funding rate
     is positive and above threshold → SELL (open short to collect funding).
  3. If we are past `hours_after_funding` after the last settlement AND
     position is open → CLOSE (take profit / reduce exposure).
  4. SMA trend filter: if price is significantly above SMA, skip shorts
     to avoid fighting a strong uptrend.
  5. Stop-loss always active regardless of timing.

Funding rate data handling:
  - The backtest framework already supports funding_rates via
    VectorizedBacktest.run(funding_rates=...).  The funding payment is
    applied automatically by the engine at settlement hours.
  - This signal generator's job is just to decide WHEN to be positioned
    to capture that payment.
  - In backtest, if no real funding_rate column is available on `data`,
    we simulate it from the separately fetched funding_rates DataFrame.
"""

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from nexustrader.backtest import Signal

from strategy.strategies.funding_rate.core import FundingRateConfig, calculate_sma


# Funding settlement hours in UTC
FUNDING_SETTLEMENT_HOURS = (0, 8, 16)


@dataclass
class FundingRateFilterConfig:
    """Configuration for trade filtering (Funding Rate specific)."""

    # Minimum bars to hold a position (prevents rapid flip)
    min_holding_bars: int = 1
    # Cooldown bars after closing a position
    cooldown_bars: int = 1
    # How many consecutive signal confirmations needed
    signal_confirmation: int = 1


def _hours_until_next_settlement(ts: pd.Timestamp) -> float:
    """Calculate hours until the next 8h funding settlement.

    Args:
        ts: Current bar timestamp (assumed UTC or tz-naive).

    Returns:
        Hours (float) until next settlement in (00:00, 08:00, 16:00).
    """
    hour = ts.hour + ts.minute / 60.0
    for settle_h in FUNDING_SETTLEMENT_HOURS:
        if settle_h > hour:
            return settle_h - hour
    # Next settlement is 00:00 of the next day
    return 24.0 - hour


def _hours_since_last_settlement(ts: pd.Timestamp) -> float:
    """Calculate hours since the most recent 8h funding settlement.

    Args:
        ts: Current bar timestamp (assumed UTC or tz-naive).

    Returns:
        Hours (float) since last settlement.
    """
    hour = ts.hour + ts.minute / 60.0
    for settle_h in reversed(FUNDING_SETTLEMENT_HOURS):
        if settle_h <= hour:
            return hour - settle_h
    # Before 00:00 today means last settlement was 16:00 yesterday
    return hour + 8.0


def _build_funding_rate_series(
    data_index: pd.DatetimeIndex,
    funding_rates: Optional[pd.DataFrame],
    lookback: int,
) -> np.ndarray:
    """Build per-bar average funding rate array.

    For each bar in data, compute the average of the last `lookback`
    funding rate observations.  If no funding rate data is provided,
    return a constant positive rate (simulated BTC average ~0.0014% per 8h).

    Returns:
        Array of shape (len(data_index),) with average funding rates.
    """
    n = len(data_index)
    avg_funding = np.full(n, 0.00001)  # tiny default

    if funding_rates is None or funding_rates.empty:
        # Simulate: constant average BTC funding rate
        avg_funding[:] = 0.000014  # 0.0014% per 8h = 0.000014 as decimal
        return avg_funding

    # Build a map: date+hour -> funding_rate for quick lookup
    fr_values = []
    fr_timestamps = []
    for ts, row in funding_rates.iterrows():
        fr_values.append(row.get("funding_rate", 0.0))
        fr_timestamps.append(ts)

    fr_values = np.array(fr_values)
    fr_timestamps = pd.DatetimeIndex(fr_timestamps)

    # For each bar, find the most recent `lookback` funding rates and average
    for i in range(n):
        bar_ts = data_index[i]
        # Get funding rates before or at this bar's time
        mask = fr_timestamps <= bar_ts
        recent = fr_values[mask]
        if len(recent) > 0:
            window = recent[-lookback:] if len(recent) >= lookback else recent
            avg_funding[i] = np.mean(window)

    return avg_funding


class FundingRateSignalGenerator:
    """Generate trading signals for funding rate arbitrage.

    The backtest engine handles actual funding payment accounting.
    This generator decides when to enter/exit positions to capture
    funding rate payments.

    Funding rate data can be provided in two ways:
      1. Set `self.funding_rates` before calling generate() — used by the
         backtest runner / heatmap scanner.
      2. Pass via `params["_funding_rates"]` — alternative injection path.
    If neither is available, simulated constant rates are used.
    """

    def __init__(
        self,
        config: FundingRateConfig,
        filter_config: FundingRateFilterConfig,
    ):
        self.config = config
        self.filter = filter_config
        # Can be set externally before generate() is called
        self.funding_rates: Optional[pd.DataFrame] = None

    def generate(
        self,
        data: pd.DataFrame,
        params: Optional[Dict] = None,
    ) -> np.ndarray:
        """Generate signals from OHLCV data.

        Args:
            data: OHLCV DataFrame with columns: open, high, low, close, volume.
                  Index should be DatetimeIndex.
            params: Optional parameter overrides for grid search / heatmap.

        Returns:
            Array of signal values (0=HOLD, 1=BUY, -1=SELL, 2=CLOSE).
        """
        # Resolve parameters (allow overrides from grid search)
        min_fr = float(
            params.get("min_funding_rate", self.config.min_funding_rate)
            if params else self.config.min_funding_rate
        )
        max_fr = float(
            params.get("max_funding_rate", self.config.max_funding_rate)
            if params else self.config.max_funding_rate
        )
        sma_period = int(
            params.get("price_sma_period", self.config.price_sma_period)
            if params else self.config.price_sma_period
        )
        stop_loss_pct = float(
            params.get("stop_loss_pct", self.config.stop_loss_pct)
            if params else self.config.stop_loss_pct
        )
        hours_before = int(
            params.get("hours_before_funding", self.config.hours_before_funding)
            if params else self.config.hours_before_funding
        )
        hours_after = int(
            params.get("hours_after_funding", self.config.hours_after_funding)
            if params else self.config.hours_after_funding
        )
        max_adverse = float(
            params.get("max_adverse_move_pct", self.config.max_adverse_move_pct)
            if params else self.config.max_adverse_move_pct
        )
        funding_lookback = int(
            params.get("funding_lookback", self.config.funding_lookback)
            if params else self.config.funding_lookback
        )

        n = len(data)
        signals = np.zeros(n)
        prices = data["close"].values

        # Calculate indicators
        sma = calculate_sma(prices, sma_period)

        # Build per-bar funding rate average
        # Use injected funding_rates from self, or from params, or simulate
        fr_data = self.funding_rates
        if fr_data is None and params and "_funding_rates" in params:
            fr_data = params["_funding_rates"]
        avg_funding = _build_funding_rate_series(
            data.index, fr_data, funding_lookback
        )

        # State tracking
        position = 0  # 0=flat, -1=short
        entry_bar = 0
        entry_price = 0.0
        cooldown_until = 0

        min_holding = self.filter.min_holding_bars
        cooldown = self.filter.cooldown_bars

        start_bar = sma_period  # Need SMA to be valid

        for i in range(start_bar, n):
            price = prices[i]
            ts = data.index[i]

            if np.isnan(sma[i]):
                continue

            # ---- 1. Stop-loss check (always, regardless of timing) ----
            if position == -1 and entry_price > 0:
                # For short position, adverse move = price going UP
                adverse_pct = (price - entry_price) / entry_price
                if adverse_pct > stop_loss_pct:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    entry_price = 0.0
                    cooldown_until = i + cooldown
                    continue

            # ---- 2. Timing: compute hours to/from settlement ----
            hours_to_next = _hours_until_next_settlement(ts)
            hours_since_last = _hours_since_last_settlement(ts)

            # ---- 3. Exit logic: close after funding settlement ----
            if position == -1:
                bars_held = i - entry_bar
                # Close if: past the settlement window AND held long enough
                if (hours_since_last <= hours_after and hours_since_last > 0
                        and bars_held >= min_holding):
                    # We've just passed a settlement — close the position
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    entry_price = 0.0
                    cooldown_until = i + cooldown
                    continue

                # Also close if adverse move exceeds tolerance
                if entry_price > 0:
                    adverse_pct = (price - entry_price) / entry_price
                    if adverse_pct > max_adverse:
                        signals[i] = Signal.CLOSE.value
                        position = 0
                        entry_price = 0.0
                        cooldown_until = i + cooldown
                        continue

            # ---- 4. Entry logic: open short before settlement ----
            if i < cooldown_until:
                continue

            if position == 0:
                # Check timing: within entry window before settlement
                in_entry_window = hours_to_next <= hours_before

                # Check funding rate conditions
                fr = avg_funding[i]
                funding_ok = min_fr <= fr <= max_fr

                # Trend filter: don't short if price is significantly above SMA
                # (strong uptrend). Allow if price <= SMA * (1 + max_adverse)
                trend_ok = price <= sma[i] * (1.0 + max_adverse)

                if in_entry_window and funding_ok and trend_ok:
                    signals[i] = Signal.SELL.value  # Open short
                    position = -1
                    entry_bar = i
                    entry_price = price

        return signals
