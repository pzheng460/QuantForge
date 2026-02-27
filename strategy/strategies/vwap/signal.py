"""
VWAP Mean Reversion Signal Generator.

Exchange-agnostic signal generator for the unified backtest framework.
Delegates to VWAPSignalCore for bar-by-bar signal generation,
ensuring 100% parity with live trading logic.
"""

import dataclasses
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from strategy.indicators.vwap import VWAPSignalCore
from strategy.strategies.vwap.core import VWAPConfig


@dataclass
class VWAPTradeFilterConfig:
    """Configuration for trade filtering (VWAP-specific)."""

    min_holding_bars: int = 4
    cooldown_bars: int = 2
    signal_confirmation: int = 1


# Fields that belong to VWAPConfig (for splitting params)
_CONFIG_FIELDS = {f.name for f in dataclasses.fields(VWAPConfig)}


class VWAPSignalGenerator:
    """Generate trading signals for vectorized backtest.

    Delegates to VWAPSignalCore for bar-by-bar processing.
    """

    def __init__(self, config: VWAPConfig, filter_config: VWAPTradeFilterConfig):
        self.config = config
        self.filter = filter_config

    def generate(self, data: pd.DataFrame, params: Optional[Dict] = None) -> np.ndarray:
        """Generate signals from OHLCV data.

        Args:
            data: OHLCV DataFrame with columns: open, high, low, close, volume.
                  Index should be DatetimeIndex for daily VWAP reset.
            params: Optional parameter overrides.

        Returns:
            Array of signal values (0=HOLD, 1=BUY, -1=SELL, 2=CLOSE).
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

        # Build effective filter config with overrides
        min_holding_bars = int(p.get("min_holding_bars", self.filter.min_holding_bars))
        cooldown_bars = int(p.get("cooldown_bars", self.filter.cooldown_bars))
        signal_confirmation = int(
            p.get("signal_confirmation", self.filter.signal_confirmation)
        )

        # Create core and process all bars
        core = VWAPSignalCore(
            config=effective_config,
            min_holding_bars=min_holding_bars,
            cooldown_bars=cooldown_bars,
            signal_confirmation=signal_confirmation,
        )

        n = len(data)
        signals = np.zeros(n)
        prices = data["close"].values
        highs = data["high"].values
        lows = data["low"].values
        volumes = data["volume"].values

        # Day boundary detection for VWAP reset
        timestamps = data.index if isinstance(data.index, pd.DatetimeIndex) else None

        for i in range(n):
            day = None
            if timestamps is not None:
                day = timestamps[i].date()

            signals[i] = core.update(
                close=prices[i],
                high=highs[i],
                low=lows[i],
                volume=volumes[i],
                day=day,
            )

        return signals
