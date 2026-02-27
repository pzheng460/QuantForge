"""
Grid Trading Signal Generator — Adaptive for single-position backtest engine.

Exchange-agnostic signal generator for the backtest framework.
Delegates to GridSignalCore for bar-by-bar signal generation,
ensuring 100% parity with live trading logic.
"""

import dataclasses
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from strategy.indicators.grid_trading import GridSignalCore
from strategy.strategies.grid_trading.core import GridConfig


@dataclass
class GridTradeFilterConfig:
    """Configuration for trade filtering."""

    min_holding_bars: int = 1
    cooldown_bars: int = 0
    signal_confirmation: int = 1


# Fields that belong to GridConfig (for splitting params)
_CONFIG_FIELDS = {f.name for f in dataclasses.fields(GridConfig)}


class GridSignalGenerator:
    """Grid trading adapted for single-position backtest framework.

    Delegates to GridSignalCore for bar-by-bar processing.
    """

    def __init__(self, config: GridConfig, filter_config: GridTradeFilterConfig):
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

        min_holding_bars = int(p.get("min_holding_bars", self.filter.min_holding_bars))
        cooldown_bars = int(p.get("cooldown_bars", self.filter.cooldown_bars))

        # Create core and process all bars
        core = GridSignalCore(
            config=effective_config,
            min_holding_bars=min_holding_bars,
            cooldown_bars=cooldown_bars,
        )

        n = len(data)
        signals = np.zeros(n)
        prices = data["close"].values
        highs = data["high"].values
        lows = data["low"].values

        for i in range(n):
            signals[i] = core.update(
                close=prices[i],
                high=highs[i],
                low=lows[i],
            )

        return signals
