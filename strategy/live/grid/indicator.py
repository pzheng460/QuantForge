"""
NexusTrader Indicator wrapper for Grid Trading strategy.

Delegates all indicator calculations to GridSignalCore, ensuring
100% parity with the backtest signal generator.
"""

from enum import Enum
from typing import Optional

import numpy as np

from nexustrader.constants import KlineInterval
from nexustrader.indicator import Indicator
from nexustrader.schema import BookL1, BookL2, Kline, Trade

from strategy.indicators.grid_trading import GridSignalCore
from strategy.strategies.grid_trading.core import GridConfig


class GridSignal(Enum):
    """Grid trading signals."""

    HOLD = "hold"
    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"


class GridIndicator(Indicator):
    """
    Grid Trading Indicator that calculates SMA, ATR and dynamic grid lines.

    All indicator calculations are delegated to GridSignalCore,
    which is shared with the backtest signal generator.
    """

    def __init__(
        self,
        grid_count: int = 3,
        atr_multiplier: float = 3.0,
        sma_period: int = 20,
        atr_period: int = 14,
        recalc_period: int = 24,
        entry_lines: int = 1,
        profit_lines: int = 2,
        stop_loss_pct: float = 0.05,
        kline_interval: KlineInterval = KlineInterval.HOUR_1,
    ):
        self._grid_count = grid_count
        self._entry_lines = entry_lines
        self._profit_lines = profit_lines

        # Calculate real warmup period
        self._real_warmup_period = max(sma_period, atr_period) + 10

        # Disable framework warmup
        super().__init__(
            params={
                "grid_count": grid_count,
                "atr_multiplier": atr_multiplier,
                "sma_period": sma_period,
                "atr_period": atr_period,
            },
            name="Grid",
            warmup_period=None,
            kline_interval=kline_interval,
        )

        # Create config for the core
        config = GridConfig(
            grid_count=grid_count,
            atr_multiplier=atr_multiplier,
            sma_period=sma_period,
            atr_period=atr_period,
            recalc_period=recalc_period,
            entry_lines=entry_lines,
            profit_lines=profit_lines,
            stop_loss_pct=stop_loss_pct,
        )

        # Shared core
        self._core = GridSignalCore(config)

        self._confirmed_bar_count: int = 0
        self._signal: GridSignal = GridSignal.HOLD
        self._last_price: Optional[float] = None
        self._preserve_peak_trough: bool = False

    @property
    def is_warmed_up(self) -> bool:
        return self._confirmed_bar_count >= self._real_warmup_period

    def handle_kline(self, kline: Kline) -> None:
        """Process new kline data using timestamp change detection for bar confirmation."""
        bar_start = int(kline.start)

        if not hasattr(self, "_current_bar_start"):
            self._current_bar_start = bar_start
            self._current_bar_kline = kline
            return

        if bar_start != self._current_bar_start:
            confirmed_kline = self._current_bar_kline
            self._confirmed_bar_count += 1
            self._process_kline_data(confirmed_kline)

            self._current_bar_start = bar_start
            self._current_bar_kline = kline
        else:
            self._current_bar_kline = kline

    def _process_kline_data(self, kline: Kline) -> None:
        close = float(kline.close)
        high = float(kline.high)
        low = float(kline.low)
        self._last_price = close

        self._core.update_indicators_only(close=close, high=high, low=low)

    def handle_bookl1(self, bookl1: BookL1) -> None:
        pass

    def handle_bookl2(self, bookl2: BookL2) -> None:
        pass

    def handle_trade(self, trade: Trade) -> None:
        pass

    # Properties
    @property
    def value(self) -> dict:
        return {
            "sma": self._core.sma_value,
            "atr": self._core.atr_value,
            "current_level": self._core.current_level,
            "peak_level": self._core.peak_level,
            "trough_level": self._core.trough_level,
            "signal": self._signal.value,
        }

    @property
    def sma(self) -> Optional[float]:
        return self._core.sma_value

    @property
    def atr(self) -> Optional[float]:
        return self._core.atr_value

    @property
    def grid_lines(self) -> Optional[np.ndarray]:
        return self._core.grid_lines

    @property
    def current_level(self) -> int:
        return self._core.current_level

    @property
    def peak_level(self) -> int:
        return self._core.peak_level

    @property
    def trough_level(self) -> int:
        return self._core.trough_level

    @property
    def grid_count(self) -> int:
        return self._grid_count

    @property
    def entry_lines(self) -> int:
        return self._entry_lines

    @property
    def profit_lines(self) -> int:
        return self._profit_lines

    def get_signal(self) -> GridSignal:
        return self._signal

    def reset(self) -> None:
        self._core.reset()
        self._signal = GridSignal.HOLD
        self._last_price = None
        self._confirmed_bar_count = 0
        if hasattr(self, "_current_bar_start"):
            del self._current_bar_start
        if hasattr(self, "_current_bar_kline"):
            del self._current_bar_kline
        self._preserve_peak_trough = False
