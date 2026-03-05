"""Shared SMA Trend signal core for backtest and live trading.

This module contains SMATrendSignalCore — a streaming, bar-by-bar signal
generator for a long-only daily SMA trend-following strategy. Both the
backtest signal generator and the live indicator wrapper delegate to this
class, guaranteeing 100% code parity.

Backtest mode: Daily SMA values are pre-computed via pre_loop_hook and
injected per bar via bar_hook (sma_value kwarg).  Signal evaluation only
occurs on daily-close bars (is_daily_close=True) to avoid intraday noise.

Live mode: Uses StreamingSMA on daily kline closes to compute SMA in
real-time, then maps 1h bars against the latest daily SMA.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from strategy.strategies._base.streaming import StreamingSMA

if TYPE_CHECKING:
    from strategy.strategies.sma_trend.core import SMATrendConfig


from strategy.strategies._base.signal_core_base import BaseSignalCore, HOLD, BUY, SELL, CLOSE


class SMATrendSignalCore(BaseSignalCore):
    """Shared signal logic for SMA Trend (long-only).

    Long when daily close > daily SMA, flat otherwise.
    No shorting, no stop loss. Signal only evaluated on daily-close bars
    to prevent intraday noise from generating false crossovers.
    """

    def __init__(
        self,
        config: SMATrendConfig,
        min_holding_bars: int = 0,
        cooldown_bars: int = 0,
    ):
        self._config = config

        # Filter params (unused for simplicity, kept for interface compat)
        self._min_holding_bars = min_holding_bars
        self._cooldown_bars = cooldown_bars

        # Streaming SMA (for live mode with daily klines)
        self._sma = StreamingSMA(config.sma_period)

        # Position management state
        self.position = 0  # 0=flat, 1=long (never short)
        self.entry_bar = 0
        self.entry_price = 0.0
        self.bar_index = 0

    def update_indicators_only(
        self, close: float, sma_value: float = None, is_daily_close: bool = True
    ) -> None:
        """Update indicators without generating a signal (warmup mode)."""
        if sma_value is None:
            self._sma.update(close)
        self.bar_index += 1

    def update(
        self, close: float, sma_value: float = None, is_daily_close: bool = True
    ) -> int:
        """Process one bar and return a trading signal.

        Args:
            close: Close price of the current bar.
            sma_value: Pre-computed daily SMA value (backtest mode).
                If None, uses the internal StreamingSMA (live mode).
            is_daily_close: Whether this bar is a daily close (end of day).
                Signal evaluation only happens on daily-close bars to avoid
                intraday noise generating false crossovers.

        Returns:
            Signal value: HOLD(0), BUY(1), or CLOSE(2).
        """
        # Determine SMA value
        if sma_value is not None:
            sma = sma_value
        else:
            sma = self._sma.update(close)

        self.bar_index += 1
        i = self.bar_index

        # Need SMA to be valid
        if sma is None:
            return HOLD

        # Only evaluate signal on daily-close bars
        if not is_daily_close:
            return HOLD

        price = close

        # ---- Position management (long-only, no filters) ----
        if price > sma and self.position == 0:
            self.position = 1
            self.entry_bar = i
            self.entry_price = price
            return BUY

        elif price < sma and self.position == 1:
            self.position = 0
            self.entry_price = 0.0
            return CLOSE

        return HOLD

    def reset(self):
        """Reset all state (indicators + position management)."""
        self._sma.reset()
        self.position = 0
        self.entry_bar = 0
        self.entry_price = 0.0
        self.cooldown_until = 0
        self.bar_index = 0


    # ---- Indicator value properties ----

    @property
    def sma_value(self) -> Optional[float]:
        return self._sma.value

    def get_raw_signal(self) -> int:
        """Compute raw signal from current indicator values (no position management).

        Used by the live indicator wrapper in warmup mode.
        """
        sma = self._sma.value
        if sma is None:
            return HOLD

        # Get last close from SMA window
        if not self._sma._window:
            return HOLD
        price = list(self._sma._window)[-1]

        if price > sma:
            return BUY
        elif price < sma:
            return CLOSE
        return HOLD
