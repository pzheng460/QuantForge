"""
Multi-Timeframe Momentum Signal Generator.

Exchange-agnostic signal generator for the backtest framework.
Delegates to MomentumSignalCore for bar-by-bar signal generation,
ensuring 100% parity with live trading logic.
"""

import dataclasses
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from strategy.indicators.momentum import MomentumSignalCore
from strategy.strategies.momentum.core import MomentumConfig


@dataclass
class MomentumTradeFilterConfig:
    """Configuration for trade filtering (Momentum-specific)."""

    min_holding_bars: int = 4
    cooldown_bars: int = 2
    signal_confirmation: int = 1


# Fields that belong to MomentumConfig (for splitting params)
_CONFIG_FIELDS = {f.name for f in dataclasses.fields(MomentumConfig)}


class MomentumSignalGenerator:
    """Generate trading signals for backtest.

    Entry (long, all must hold):
        1. ROC > roc_threshold
        2. EMA_fast > EMA_slow
        3. Price > EMA_trend
        4. Volume > volume_SMA * volume_threshold

    Entry (short) is symmetric:
        1. ROC < -roc_threshold
        2. EMA_fast < EMA_slow
        3. Price < EMA_trend
        4. Volume > volume_SMA * volume_threshold

    Exit (any one):
        1. ROC reversal (long: ROC < 0; short: ROC > 0)
        2. EMA crossover reversal
        3. ATR trailing stop  OR  hard stop_loss_pct
    """

    def __init__(
        self,
        config: MomentumConfig,
        filter_config: MomentumTradeFilterConfig,
    ):
        self.config = config
        self.filter = filter_config

    def generate(self, data: pd.DataFrame, params: Optional[Dict] = None) -> np.ndarray:
        """Generate signals from OHLCV data.

        Args:
            data: OHLCV DataFrame (open, high, low, close, volume).
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
        core = MomentumSignalCore(
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

        for i in range(n):
            signals[i] = core.update(
                close=prices[i],
                high=highs[i],
                low=lows[i],
                volume=volumes[i],
            )

        return signals
