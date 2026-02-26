"""
Grid Trading Signal Generator — Adaptive for single-position backtest engine.

Strategy: Track price movement across grid lines.
- Accumulate "grid score" as price crosses lines downward (bullish pressure)
- When score reaches threshold → BUY
- When price bounces back across enough lines → CLOSE (take profit)
- Vice versa for shorts

This maps multi-layer grid logic onto a single-position framework by
using grid crossings as entry/exit triggers rather than per-grid trades.
"""

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pandas as pd

from nexustrader.backtest import Signal
from strategy.strategies.grid_trading.core import (
    GridConfig,
    calculate_atr,
    calculate_sma,
)


@dataclass
class GridTradeFilterConfig:
    """Configuration for trade filtering."""
    min_holding_bars: int = 1
    cooldown_bars: int = 0
    signal_confirmation: int = 1


class GridSignalGenerator:
    """Grid trading adapted for single-position backtest framework.
    
    Uses grid line crossings to time entries and exits:
    - BUY when price drops N grid lines from recent high level (mean reversion buy)
    - CLOSE long when price rises back M grid lines (take profit)
    - SELL when price rises N grid lines from recent low level
    - CLOSE short when price drops back M grid lines
    """

    def __init__(self, config: GridConfig, filter_config: GridTradeFilterConfig):
        self.config = config
        self.filter = filter_config

    def generate(self, data: pd.DataFrame, params: Optional[Dict] = None) -> np.ndarray:
        p = params or {}

        grid_count = int(p.get("grid_count", self.config.grid_count))
        atr_multiplier = float(p.get("atr_multiplier", self.config.atr_multiplier))
        sma_period = int(p.get("sma_period", self.config.sma_period))
        atr_period = int(p.get("atr_period", self.config.atr_period))
        recalc_period = int(p.get("recalc_period", self.config.recalc_period))
        stop_loss_pct = float(p.get("stop_loss_pct", self.config.stop_loss_pct))
        # Grid crossing thresholds (how many grid lines to trigger entry/exit)
        entry_lines = int(p.get("entry_lines", getattr(self.config, 'entry_lines', 2)))
        profit_lines = int(p.get("profit_lines", getattr(self.config, 'profit_lines', 1)))

        n = len(data)
        signals = np.zeros(n)
        prices = data["close"].values
        highs = data["high"].values
        lows = data["low"].values

        sma = calculate_sma(prices, sma_period)
        atr = calculate_atr(highs, lows, prices, atr_period)

        start_bar = max(sma_period, atr_period) + 1

        # State
        grid_lines = None
        current_level = 0
        peak_level = 0      # highest grid level since flat
        trough_level = 999   # lowest grid level since flat
        position = 0         # 1=long, -1=short, 0=flat
        entry_price = 0.0
        last_recalc = 0
        cooldown_until = 0

        for i in range(start_bar, n):
            price = prices[i]

            if np.isnan(sma[i]) or np.isnan(atr[i]) or atr[i] <= 0:
                continue

            # Recalculate grid periodically
            if grid_lines is None or (i - last_recalc >= recalc_period):
                center = sma[i]
                half_range = atr_multiplier * atr[i]
                upper = center + half_range
                lower = center - half_range
                if upper <= lower:
                    continue
                grid_lines = np.linspace(lower, upper, grid_count + 1)
                last_recalc = i
                # Reset tracking on grid recalc
                current_level = int(np.searchsorted(grid_lines, price))
                current_level = max(0, min(grid_count, current_level))
                peak_level = current_level
                trough_level = current_level

            if grid_lines is None:
                continue

            # Get current grid level
            new_level = int(np.searchsorted(grid_lines, price))
            new_level = max(0, min(grid_count, new_level))

            # Stop loss
            if position != 0 and entry_price > 0:
                if position == 1:
                    loss = (entry_price - price) / entry_price
                else:
                    loss = (price - entry_price) / entry_price
                if loss > stop_loss_pct:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    entry_price = 0.0
                    peak_level = new_level
                    trough_level = new_level
                    cooldown_until = i + self.filter.cooldown_bars
                    current_level = new_level
                    continue

            if i < cooldown_until:
                current_level = new_level
                continue

            # Track peak/trough since entry or last flat
            if new_level > peak_level:
                peak_level = new_level
            if new_level < trough_level:
                trough_level = new_level

            if position == 0:
                # FLAT: look for entry
                # Price dropped N lines from peak → BUY (mean reversion)
                if peak_level - new_level >= entry_lines and new_level <= grid_count // 2:
                    signals[i] = Signal.BUY.value
                    position = 1
                    entry_price = price
                    trough_level = new_level
                    peak_level = new_level

                # Price rose N lines from trough → SELL (mean reversion)
                elif new_level - trough_level >= entry_lines and new_level >= grid_count // 2:
                    signals[i] = Signal.SELL.value
                    position = -1
                    entry_price = price
                    peak_level = new_level
                    trough_level = new_level

            elif position == 1:
                # LONG: look for take-profit
                if new_level - trough_level >= profit_lines:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    entry_price = 0.0
                    peak_level = new_level
                    trough_level = new_level
                    cooldown_until = i + self.filter.cooldown_bars

            elif position == -1:
                # SHORT: look for take-profit
                if peak_level - new_level >= profit_lines:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    entry_price = 0.0
                    peak_level = new_level
                    trough_level = new_level
                    cooldown_until = i + self.filter.cooldown_bars

            current_level = new_level

        return signals
