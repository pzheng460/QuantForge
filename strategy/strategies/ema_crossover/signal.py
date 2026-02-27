"""
EMA Crossover Signal Generator.

Exchange-agnostic signal generator for the backtest framework.
Delegates to EMASignalCore for bar-by-bar signal generation,
ensuring 100% parity with live trading logic.
"""

import dataclasses
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from strategy.indicators.ema_crossover import EMASignalCore
from strategy.strategies.ema_crossover.core import EMAConfig


@dataclass
class EMATradeFilterConfig:
    """Configuration for trade filtering (EMA-specific)."""

    min_holding_bars: int = 4
    cooldown_bars: int = 2
    signal_confirmation: int = 1


# Fields that belong to EMAConfig (for splitting params)
_CONFIG_FIELDS = {f.name for f in dataclasses.fields(EMAConfig)}


class EMASignalGenerator:
    """Generate trading signals for vectorized backtest.

    Replicates the logic from EMACrossoverIndicator for backtesting.
    Delegates to EMASignalCore for bar-by-bar signal generation.
    """

    def __init__(self, config: EMAConfig, filter_config: EMATradeFilterConfig):
        self.config = config
        self.filter = filter_config

    def generate(self, data: pd.DataFrame, params: Optional[Dict] = None) -> np.ndarray:
        """Generate signals from OHLCV data.

        Args:
            data: OHLCV DataFrame.
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
        core = EMASignalCore(
            config=effective_config,
            min_holding_bars=min_holding_bars,
            cooldown_bars=cooldown_bars,
            signal_confirmation=signal_confirmation,
        )

        n = len(data)
        signals = np.zeros(n)
        prices = data["close"].values

        for i in range(n):
            signals[i] = core.update(close=prices[i])

        return signals
