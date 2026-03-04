"""Shared SMA Funding signal core for backtest and live trading.

Two-leg strategy: SMA Trend (dominant, 80%) + Funding Rate Arb (secondary, 20%).
Both legs run simultaneously on the same symbol. Internal state tracks each
leg independently; the net position determines the emitted signal.

Leg 1 (SMA Trend, 80%):
  - Long when price > daily SMA100, flat otherwise.
  - ATR trailing stop (2.0 × ATR14) + hard stop loss (3%).
  - Signal gated to daily-close bars to avoid intraday noise.
  - In live mode, uses StreamingSMA on 1h bars with is_daily_close gating.

Leg 2 (Funding Arb, 20%):
  - Short perp when avg_funding_rate ≥ min_funding_rate AND trend leg is flat.
  - Closes when funding turns adverse or hard stop hits (2%).
  - Only operates during downtrends (when trend leg is inactive).

Net signal logic:
  - Trend long → net LONG (BUY)                              [80%]
  - Arb short only (trend flat) → net SHORT (SELL)           [20%]
  - Transition LONG→FLAT or SHORT→FLAT → CLOSE
  - LONG→SHORT or SHORT→LONG → CLOSE (enter opposite next bar)

Position integer encoding:
  +1  = trend long dominant
  -1  = arb short only
   0  = both flat

Three-method API (matches all other SignalCore classes):
  update(close, high, low, sma_value, is_daily_close) → int
  update_indicators_only(close, high, low, sma_value, is_daily_close) → None
  get_raw_signal() → int
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Optional

import numpy as np

from strategy.strategies._base.streaming import StreamingATR, StreamingSMA

if TYPE_CHECKING:
    from strategy.strategies.sma_funding.core import SMAFundingConfig


# Signal constants
HOLD = 0
BUY = 1
SELL = -1
CLOSE = 2


class SMAFundingSignalCore:
    """Dual-leg signal core: SMA Trend + Funding Rate Arb.

    Tracks two independent legs and emits a net signal based on
    their combined state. The trend leg dominates: when it is active,
    the net is LONG regardless of the arb leg state. The arb leg only
    activates when the trend leg is flat (downtrend context).

    Usage (backtest):
        core = SMAFundingSignalCore(config)
        core.set_funding_rate(fr)       # before each bar if available
        for i in range(n):
            signal = core.update(close[i], high[i], low[i],
                                 sma_value=sma[i], is_daily_close=flag[i])

    Usage (live indicator):
        core = SMAFundingSignalCore(config, ...)
        # Funding rate injected via set_funding_rate() on each funding event
        # During warmup:
        core.update_indicators_only(close, high, low, sma_value, is_daily_close)
        raw = core.get_raw_signal()
        # After enable_live_mode():
        signal = core.update(close, high, low, sma_value, is_daily_close)
    """

    def __init__(
        self,
        config: SMAFundingConfig,
        min_holding_bars: int = 0,
        cooldown_bars: int = 0,
    ):
        self._config = config

        # Filter params (stored for interface compatibility)
        self._min_holding_bars = min_holding_bars
        self._cooldown_bars = cooldown_bars

        # Streaming indicators
        self._sma = StreamingSMA(config.sma_period)
        self._atr = StreamingATR(config.atr_period)

        # ---- Leg 1 (SMA Trend) state ----
        self._leg1_pos: int = 0  # 0=flat, 1=long
        self._leg1_entry_price: float = 0.0
        self._leg1_peak: float = 0.0  # highest close since entry (trailing stop)

        # ---- Leg 2 (Funding Arb) state ----
        self._leg2_pos: int = 0  # 0=flat, -1=short
        self._leg2_entry_price: float = 0.0
        self._funding_rates: deque[float] = deque(maxlen=config.funding_lookback)
        self._avg_funding_rate: float = 0.0

        # Saved values for get_raw_signal()
        self._last_close: float = 0.0
        self._last_is_daily_close: bool = False
        self._last_atr: Optional[float] = None

        # Net position state (external API — matches other signal cores)
        self.position: int = 0  # +1 = long, -1 = short, 0 = flat
        self.entry_price: float = 0.0
        self.bar_index: int = 0

    # ------------------------------------------------------------------ #
    # Funding rate injection                                               #
    # ------------------------------------------------------------------ #

    def set_funding_rate(self, rate: float) -> None:
        """Update current funding rate from an external source.

        Called by bar_hook (backtest) or on_funding_rate_fn (live).
        """
        self._funding_rates.append(rate)
        if self._funding_rates:
            self._avg_funding_rate = float(np.mean(list(self._funding_rates)))

    # ------------------------------------------------------------------ #
    # Warmup mode — indicators only, no position management               #
    # ------------------------------------------------------------------ #

    def update_indicators_only(
        self,
        close: float,
        high: float,
        low: float,
        sma_value: Optional[float] = None,
        is_daily_close: bool = True,
    ) -> None:
        """Update all indicators without generating a trading signal.

        Called by the GenericIndicator wrapper during the warmup phase.
        Saves close and is_daily_close for get_raw_signal().
        """
        if sma_value is None:
            self._sma.update(close)
        atr = self._atr.update(high, low, close)
        self._last_close = close
        self._last_is_daily_close = is_daily_close
        self._last_atr = atr
        self.bar_index += 1

    # ------------------------------------------------------------------ #
    # Live mode — full update with position management                    #
    # ------------------------------------------------------------------ #

    def update(
        self,
        close: float,
        high: float,
        low: float,
        sma_value: Optional[float] = None,
        is_daily_close: bool = True,
    ) -> int:
        """Process one bar and return a net trading signal.

        Args:
            close: Close price of the confirmed bar.
            high: High price of the confirmed bar.
            low: Low price of the confirmed bar.
            sma_value: Pre-computed daily SMA (backtest mode).
                       If None, uses internal StreamingSMA (live mode).
            is_daily_close: True when bar is the last hourly bar of the day.
                            SMA entry/exit signals are gated to daily closes
                            to prevent intraday noise.

        Returns:
            Signal constant: BUY(1), SELL(-1), CLOSE(2), or HOLD(0).
        """
        # Update indicators
        sma = self._sma.update(close) if sma_value is None else sma_value
        current_atr = self._atr.update(high, low, close)
        self._last_close = close
        self._last_is_daily_close = is_daily_close
        self._last_atr = current_atr
        self.bar_index += 1

        # ---- Update each leg's internal state ----
        self._update_trend_leg(close, sma, current_atr, is_daily_close)
        self._update_arb_leg(close)

        # ---- Compute net direction and emit transition signal ----
        return self._emit_net_signal(close)

    # ------------------------------------------------------------------ #
    # Internal per-leg state update                                       #
    # ------------------------------------------------------------------ #

    def _update_trend_leg(
        self,
        close: float,
        sma: Optional[float],
        current_atr: Optional[float],
        is_daily_close: bool,
    ) -> None:
        """Update leg 1 (SMA trend) internal state."""
        if self._leg1_pos == 1 and self._leg1_entry_price > 0:
            # Track peak for trailing stop
            self._leg1_peak = max(self._leg1_peak, close)

            # Hard stop loss
            if close < self._leg1_entry_price * (1 - self._config.trend_stop_loss_pct):
                self._leg1_pos = 0
                self._leg1_entry_price = 0.0
                self._leg1_peak = 0.0
                return

            # ATR trailing stop from peak
            if current_atr is not None:
                trail = self._leg1_peak - self._config.atr_trail_mult * current_atr
                if close < trail:
                    self._leg1_pos = 0
                    self._leg1_entry_price = 0.0
                    self._leg1_peak = 0.0
                    return

        # SMA entry / exit (gated to daily closes)
        if sma is not None and is_daily_close:
            if close > sma and self._leg1_pos == 0:
                self._leg1_pos = 1
                self._leg1_entry_price = close
                self._leg1_peak = close
            elif close < sma and self._leg1_pos == 1:
                self._leg1_pos = 0
                self._leg1_entry_price = 0.0
                self._leg1_peak = 0.0

    def _update_arb_leg(self, close: float) -> None:
        """Update leg 2 (funding arb) internal state.

        Only enters short when trend leg is flat (downtrend context).
        Exits on adverse funding or hard stop.
        """
        fr = self._avg_funding_rate

        if self._leg2_pos == -1:
            # Hard stop: price rose against short position
            if close > self._leg2_entry_price * (1 + self._config.arb_stop_loss_pct):
                self._leg2_pos = 0
                self._leg2_entry_price = 0.0
                return
            # Funding turned adverse
            if fr < self._config.min_funding_rate:
                self._leg2_pos = 0
                self._leg2_entry_price = 0.0
                return

        # Enter short arb only when trend leg is flat
        if self._leg2_pos == 0 and self._leg1_pos == 0:
            if fr >= self._config.min_funding_rate:
                self._leg2_pos = -1
                self._leg2_entry_price = close

    def _emit_net_signal(self, close: float) -> int:
        """Compute desired net direction and emit a transition signal.

        The trend leg dominates: when active, net is LONG regardless of leg2.
        The arb leg only contributes when trend leg is flat.

        Transitions:
          0  →  +1  : BUY   (trend enters)
          0  →  -1  : SELL  (arb enters, downtrend)
          +1 →   0  : CLOSE (trend exits, arb also flat)
          -1 →   0  : CLOSE (arb exits)
          +1 →  -1  : CLOSE (trend exits, arb active; SELL emitted next bar)
          -1 →  +1  : CLOSE (arb active, trend enters; BUY emitted next bar)
        """
        # Desired net direction
        if self._leg1_pos == 1:
            desired = 1  # trend dominates
        elif self._leg2_pos == -1:
            desired = -1  # arb short only
        else:
            desired = 0  # both flat

        prev_pos = self.position

        if desired > prev_pos:
            # Want more positive than current
            if prev_pos < 0:
                # Was short → close first; BUY will be emitted next bar
                self.position = 0
                self.entry_price = 0.0
                return CLOSE
            # Was flat → enter long
            self.position = desired
            self.entry_price = close
            return BUY

        elif desired < prev_pos:
            # Want more negative than current
            if prev_pos > 0:
                # Was long → close first; SELL will be emitted next bar if arb active
                self.position = 0
                self.entry_price = 0.0
                return CLOSE
            # Was flat → enter short (arb only mode)
            self.position = desired
            self.entry_price = close
            return SELL

        # desired == prev_pos → no net direction change
        return HOLD

    # ------------------------------------------------------------------ #
    # Warmup signal — stateless from current indicator values             #
    # ------------------------------------------------------------------ #

    def get_raw_signal(self) -> int:
        """Compute signal from current indicator values (no position management).

        Used by GenericIndicator in warmup mode. During warmup self.position=0,
        so only BUY/SELL entry checks are relevant.
        """
        sma = self._sma.value
        close = self._last_close
        is_daily_close = self._last_is_daily_close
        fr = self._avg_funding_rate

        if close == 0.0:
            return HOLD

        # What leg1 would want
        leg1_desired = 0
        if sma is not None and is_daily_close:
            if close > sma:
                leg1_desired = 1

        # What leg2 would want (only when leg1 flat)
        leg2_desired = 0
        if leg1_desired == 0 and fr >= self._config.min_funding_rate:
            leg2_desired = -1

        # Net desired
        if leg1_desired == 1:
            desired = 1
        elif leg2_desired == -1:
            desired = -1
        else:
            desired = 0

        # During warmup, self.position is always 0
        prev_pos = self.position
        if desired > prev_pos:
            return BUY
        elif desired < prev_pos:
            return SELL if desired == -1 else CLOSE
        return HOLD

    # ------------------------------------------------------------------ #
    # State management                                                     #
    # ------------------------------------------------------------------ #

    def reset(self) -> None:
        """Reset all state (indicators + position management)."""
        self._sma.reset()
        self._atr.reset()
        self._leg1_pos = 0
        self._leg1_entry_price = 0.0
        self._leg1_peak = 0.0
        self._leg2_pos = 0
        self._leg2_entry_price = 0.0
        self._funding_rates.clear()
        self._avg_funding_rate = 0.0
        self._last_close = 0.0
        self._last_is_daily_close = False
        self._last_atr = None
        self.position = 0
        self.entry_price = 0.0
        self.bar_index = 0

    def sync_position(self, pos_int: int, entry_price: float = 0.0) -> None:
        """Sync position state from external source (rollback or startup sync).

        Reconstructs the internal leg states to be consistent with the
        provided net position integer.
        """
        self.position = pos_int
        self.entry_price = entry_price if pos_int != 0 else 0.0

        if pos_int == 1:
            # Net long → trend leg is active
            self._leg1_pos = 1
            self._leg1_entry_price = entry_price
            self._leg1_peak = entry_price
            self._leg2_pos = 0
            self._leg2_entry_price = 0.0
        elif pos_int == -1:
            # Net short → arb leg is active, trend is flat
            self._leg1_pos = 0
            self._leg1_entry_price = 0.0
            self._leg1_peak = 0.0
            self._leg2_pos = -1
            self._leg2_entry_price = entry_price
        else:
            # Both flat
            self._leg1_pos = 0
            self._leg1_entry_price = 0.0
            self._leg1_peak = 0.0
            self._leg2_pos = 0
            self._leg2_entry_price = 0.0

    # ------------------------------------------------------------------ #
    # Indicator value properties                                           #
    # ------------------------------------------------------------------ #

    @property
    def sma_value(self) -> Optional[float]:
        return self._sma.value

    @property
    def atr_value(self) -> Optional[float]:
        return self._atr.value

    @property
    def avg_funding_rate(self) -> float:
        return self._avg_funding_rate

    @property
    def leg1_pos(self) -> int:
        """Current state of the trend leg (0=flat, 1=long)."""
        return self._leg1_pos

    @property
    def leg2_pos(self) -> int:
        """Current state of the arb leg (0=flat, -1=short)."""
        return self._leg2_pos
