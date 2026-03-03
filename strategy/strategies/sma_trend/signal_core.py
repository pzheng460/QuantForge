"""Shared SMA Trend signal core for backtest and live trading.

This module contains SMATrendSignalCore — a streaming, bar-by-bar signal
generator for a long-only daily SMA trend-following strategy. Both the
backtest signal generator and the live indicator wrapper delegate to this
class, guaranteeing 100% code parity.

Backtest mode: Daily SMA values are pre-computed via pre_loop_hook and
injected per bar via bar_hook (sma_value kwarg).

Live mode: Uses StreamingSMA on daily kline closes to compute SMA in
real-time, then maps 1h bars against the latest daily SMA.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from strategy.strategies._base.streaming import StreamingSMA

if TYPE_CHECKING:
    from strategy.strategies.sma_trend.core import SMATrendConfig


# Signal constants
HOLD = 0
BUY = 1
CLOSE = 2


class SMATrendSignalCore:
    """Shared signal logic for SMA Trend (long-only).

    Long when close > daily SMA, flat otherwise.
    No shorting, no stop loss, no signal confirmation.
    Raw signal directly drives position changes.
    """

    def __init__(
        self,
        config: SMATrendConfig,
        min_holding_bars: int = 1,
        cooldown_bars: int = 0,
    ):
        self._config = config

        # Filter params
        self._min_holding_bars = min_holding_bars
        self._cooldown_bars = cooldown_bars

        # Streaming SMA (for live mode with daily klines)
        self._sma = StreamingSMA(config.sma_period)

        # Position management state
        self.position = 0  # 0=flat, 1=long (never short)
        self.entry_bar = 0
        self.entry_price = 0.0
        self.cooldown_until = 0
        self.bar_index = 0

    def update_indicators_only(self, close: float, sma_value: float = None) -> None:
        """Update indicators without generating a signal (warmup mode)."""
        if sma_value is None:
            self._sma.update(close)
        self.bar_index += 1

    def update(self, close: float, sma_value: float = None) -> int:
        """Process one bar and return a trading signal.

        Args:
            close: Close price of the current bar.
            sma_value: Pre-computed daily SMA value (backtest mode).
                If None, uses the internal StreamingSMA (live mode).

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

        price = close

        # ---- 1. Cooldown check ----
        if i < self.cooldown_until:
            return HOLD

        # ---- 2. Position management (long-only, no confirmation) ----
        if price > sma:
            if self.position == 0:
                self.position = 1
                self.entry_bar = i
                self.entry_price = price
                return BUY

        elif price < sma:
            if self.position == 1 and i - self.entry_bar >= self._min_holding_bars:
                self.position = 0
                self.entry_price = 0.0
                self.cooldown_until = i + self._cooldown_bars
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

    def sync_position(self, pos_int: int, entry_price: float = 0.0) -> None:
        """Sync position state from external source (rollback or startup sync)."""
        self.position = pos_int
        self.entry_price = entry_price if pos_int != 0 else 0.0

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
