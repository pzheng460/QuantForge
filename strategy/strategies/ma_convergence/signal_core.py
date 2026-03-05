"""Shared MA Convergence signal core for backtest and live trading.

This module contains MAConvergenceSignalCore — a streaming, bar-by-bar signal
generator that encapsulates ALL indicator + entry/exit + position management
logic. Both the backtest signal generator and the live indicator wrapper
delegate to this class, guaranteeing 100% code parity.

Strategy uses 6 MAs (SMA 20/60/120 + EMA 20/60/120) to detect consolidation
zones. Two entry methods:
  1. MA Convergence Breakout: price breaks out of the tight convergence zone
     and confirms direction with a pullback/bounce
  2. First MA20 Retest: after MAs diverge, trade the first retest of MA20

Exit methods (configurable): risk_reward, prev_convergence, fibonacci
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, List, Optional, Tuple

from strategy.strategies._base.streaming import StreamingATR, StreamingEMA, StreamingSMA

if TYPE_CHECKING:
    from strategy.strategies.ma_convergence.core import MAConvergenceConfig


from strategy.strategies._base.signal_core_base import BaseSignalCore, HOLD, BUY, SELL, CLOSE

# State machine for entry Method 1 (convergence breakout)
_STATE_IDLE = 0  # no convergence detected
_STATE_CONVERGED = 1  # convergence zone active, watching for breakout
_STATE_BROKE_UP = 2  # price broke above zone, waiting for pullback confirmation
_STATE_BROKE_DOWN = 3  # price broke below zone, waiting for bounce confirmation

# State machine for entry Method 2 (MA20 retest)
_RETEST_IDLE = 0  # waiting for divergence
_RETEST_READY = 1  # diverged, watching for first retest of MA20


class MAConvergenceSignalCore(BaseSignalCore):
    """Shared signal logic for MA Convergence backtest and live trading.

    Processes OHLCV bars one at a time and returns a trading signal.
    Contains all indicator calculations, entry/exit conditions, and position
    management in a single class — the single source of truth.

    Usage (backtest):
        core = MAConvergenceSignalCore(config, filter_config)
        for i in range(n):
            signal = core.update(close[i], high[i], low[i])

    Usage (live indicator):
        core = MAConvergenceSignalCore(config, filter_config)
        # On each confirmed kline:
        core.update_indicators_only(close, high, low)
        # Read indicator values via properties
    """

    def __init__(
        self,
        config: MAConvergenceConfig,
        min_holding_bars: int = 4,
        cooldown_bars: int = 2,
        signal_confirmation: int = 1,
    ):
        self._config = config

        # Filter params
        self._min_holding_bars = min_holding_bars
        self._cooldown_bars = cooldown_bars
        self._signal_confirmation = signal_confirmation

        p1 = config.ma_period_1  # 20
        p2 = config.ma_period_2  # 60
        p3 = config.ma_period_3  # 120

        # 6 streaming MAs
        self._sma1 = StreamingSMA(p1)
        self._sma2 = StreamingSMA(p2)
        self._sma3 = StreamingSMA(p3)
        self._ema1 = StreamingEMA(p1)
        self._ema2 = StreamingEMA(p2)
        self._ema3 = StreamingEMA(p3)

        # ATR for convergence measurement
        self._atr = StreamingATR(config.atr_period)

        # History of convergence zone centers (for prev_convergence exit)
        # Each entry is (bar_index, zone_center)
        self._convergence_zones: List[Tuple[int, float]] = []

        # Method 1 state machine
        self._m1_state: int = _STATE_IDLE
        self._m1_zone_center: float = 0.0
        self._m1_zone_bar: int = 0
        self._m1_breakout_bar: int = 0
        self._m1_breakout_extreme: float = (
            0.0  # lowest low after up-break, or highest high after down-break
        )
        # High of the deepest-pullback candle (BROKE_UP) or
        # low of the highest-bounce candle (BROKE_DOWN).
        # Entry fires when close crosses this level.
        self._m1_pullback_candle_extreme: float = 0.0

        # Track recent convergence for minimum bar requirement before trading
        self._converged_bars: int = 0  # consecutive bars in convergence
        _MIN_CONVERGED_BARS = 3  # need at least N bars of convergence before watching
        self._MIN_CONVERGED_BARS = _MIN_CONVERGED_BARS

        # Method 2 state machine
        self._m2_state: int = _RETEST_IDLE
        self._m2_direction: int = (
            0  # 1=bullish divergence (price above MAs), -1=bearish
        )
        self._m2_diverge_bar: int = 0
        self._m2_retest_used: bool = False  # only use first retest per divergence event

        # Entry method tracking for correct stop loss calculation
        # 1 = Method 1 (breakout), 2 = Method 2 (MA20 retest)
        self._pending_entry_method: int = 1
        self._m2_retest_low: float = 0.0  # low of the MA20 retest candle (long stop)
        self._m2_retest_high: float = float("inf")  # high of the MA20 retest candle (short stop)

        # Swing tracking for fibonacci exit
        self._swing_high: float = 0.0
        self._swing_low: float = float("inf")
        self._swing_high_bar: int = 0
        self._swing_low_bar: int = 0
        # Rolling high/low window for swing detection (lookback bars)
        self._swing_lookback: int = 20
        self._high_window: deque = deque(maxlen=self._swing_lookback)
        self._low_window: deque = deque(maxlen=self._swing_lookback)

        # Position management state
        self.position = 0  # 0=flat, 1=long, -1=short
        self.entry_bar = 0
        self.entry_price = 0.0
        self.stop_price = 0.0  # stop loss price level
        self.take_profit = 0.0  # take profit price level
        self.cooldown_until = 0
        self.signal_count = {BUY: 0, SELL: 0}
        self.bar_index = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_ma_values(
        self,
    ) -> Optional[Tuple[float, float, float, float, float, float]]:
        """Return all 6 MA values, or None if any is not ready."""
        s1 = self._sma1.value
        s2 = self._sma2.value
        s3 = self._sma3.value
        e1 = self._ema1.value
        e2 = self._ema2.value
        e3 = self._ema3.value
        if any(v is None for v in (s1, s2, s3, e1, e2, e3)):
            return None
        return s1, s2, s3, e1, e2, e3

    def _update_all_indicators(
        self, close: float, high: float, low: float
    ) -> Tuple[
        Optional[Tuple[float, float, float, float, float, float]],
        Optional[float],
    ]:
        """Update all indicators and return (ma_values, atr). None if not ready."""
        self._sma1.update(close)
        self._sma2.update(close)
        self._sma3.update(close)
        self._ema1.update(close)
        self._ema2.update(close)
        self._ema3.update(close)
        atr = self._atr.update(high, low, close)
        mas = self._get_ma_values()
        return mas, atr

    def _is_converged(
        self,
        mas: Tuple[float, float, float, float, float, float],
        atr: float,
    ) -> Tuple[bool, float]:
        """Check if all 6 MAs are tightly converged.

        Returns (is_converged, zone_center).
        Convergence condition: (max_ma - min_ma) / ATR < convergence_threshold
        """
        ma_max = max(mas)
        ma_min = min(mas)
        zone_center = sum(mas) / 6.0
        if atr > 0:
            spread = (ma_max - ma_min) / atr
        else:
            spread = float("inf")
        return spread < self._config.convergence_threshold, zone_center

    def _calc_take_profit(
        self,
        direction: int,  # 1=long, -1=short
        entry: float,
        stop: float,
        zone_center: float,
        bar_index: int,
    ) -> float:
        """Calculate take profit based on exit_method config."""
        risk = abs(entry - stop)
        if risk <= 0:
            risk = entry * self._config.stop_loss_buffer_pct

        method = self._config.exit_method

        if method == "risk_reward":
            if direction == 1:
                return entry + risk * self._config.reward_ratio
            else:
                return entry - risk * self._config.reward_ratio

        elif method == "prev_convergence":
            # Find nearest previous convergence zone in the direction of profit
            best = None
            for _, z_center in reversed(self._convergence_zones):
                if direction == 1 and z_center > entry:
                    if best is None or z_center < best:
                        best = z_center
                elif direction == -1 and z_center < entry:
                    if best is None or z_center > best:
                        best = z_center
            if best is not None:
                return best
            # Fallback to risk_reward
            if direction == 1:
                return entry + risk * self._config.reward_ratio
            else:
                return entry - risk * self._config.reward_ratio

        elif method == "fibonacci":
            # Use recent swing high/low for fib extension
            sh = max(self._high_window) if self._high_window else entry
            sl = min(self._low_window) if self._low_window else entry
            fib = self._config.fib_level
            if direction == 1:
                # Fib extension of low→high range projected upward
                swing_range = sh - sl
                if swing_range > 0:
                    return sh + swing_range * (fib - 1.0)
                return entry + risk * self._config.reward_ratio
            else:
                swing_range = sh - sl
                if swing_range > 0:
                    return sl - swing_range * (fib - 1.0)
                return entry - risk * self._config.reward_ratio

        # Default: risk_reward
        if direction == 1:
            return entry + risk * self._config.reward_ratio
        else:
            return entry - risk * self._config.reward_ratio

    def _check_exit(self, close: float, high: float, low: float) -> bool:
        """Check if current position should be closed. Returns True if exit triggered."""
        if self.position == 0 or self.entry_price <= 0:
            return False

        # Take profit check (if set)
        if self.take_profit > 0:
            if self.position == 1 and high >= self.take_profit:
                return True
            if self.position == -1 and low <= self.take_profit:
                return True

        # Stop loss check
        if self.stop_price > 0:
            if self.position == 1 and low <= self.stop_price:
                return True
            if self.position == -1 and high >= self.stop_price:
                return True

        # Hard stop loss (pct-based fallback)
        if self.position == 1:
            pnl_pct = (close - self.entry_price) / self.entry_price
        else:
            pnl_pct = (self.entry_price - close) / self.entry_price

        if pnl_pct < -self._config.stop_loss_pct:
            return True

        return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_indicators_only(self, close: float, high: float, low: float) -> None:
        """Update all indicators without generating a trading signal.

        Used by the live indicator wrapper where position management
        is handled separately by the strategy class.
        """
        self._update_all_indicators(close, high, low)
        self._high_window.append(high)
        self._low_window.append(low)
        self.bar_index += 1

    def update(self, close: float, high: float, low: float) -> int:
        """Process one bar and return a trading signal.

        Updates all indicators, then runs the full signal logic
        (exits, entries, position management).

        Returns:
            Signal value: HOLD(0), BUY(1), SELL(-1), or CLOSE(2).
        """
        mas, atr = self._update_all_indicators(close, high, low)
        self._high_window.append(high)
        self._low_window.append(low)

        self.bar_index += 1
        i = self.bar_index

        # Skip if any indicator not ready
        if mas is None or atr is None:
            return HOLD

        # Unpack MAs
        sma1, sma2, sma3, ema1, ema2, ema3 = mas
        ma20_avg = (sma1 + ema1) / 2.0  # average of both MA20s

        # ---- 0. Check convergence state ----
        is_conv, zone_center = self._is_converged(mas, atr)

        if is_conv:
            self._converged_bars += 1
            # Record current zone center for prev_convergence exit tracking
        else:
            if self._converged_bars >= self._MIN_CONVERGED_BARS:
                # Transition: convergence → divergence
                # Save this zone for future reference
                self._convergence_zones.append((i, zone_center))
                # Keep only last 50 zones to avoid unbounded memory
                if len(self._convergence_zones) > 50:
                    self._convergence_zones.pop(0)

                # Trigger Method 2 retest state machine
                # Determine if price is above or below MAs at divergence
                if close > zone_center:
                    self._m2_state = _RETEST_READY
                    self._m2_direction = 1  # bullish divergence
                    self._m2_diverge_bar = i
                    self._m2_retest_used = False
                elif close < zone_center:
                    self._m2_state = _RETEST_READY
                    self._m2_direction = -1  # bearish divergence
                    self._m2_diverge_bar = i
                    self._m2_retest_used = False

                # Reset Method 1 state
                self._m1_state = _STATE_IDLE

            self._converged_bars = 0

        # Update Method 1 convergence zone tracking
        if is_conv and self._converged_bars >= self._MIN_CONVERGED_BARS:
            self._m1_state = _STATE_CONVERGED
            self._m1_zone_center = zone_center
            self._m1_zone_bar = i

        # ---- 1. Exit check ----
        if self.position != 0:
            if i - self.entry_bar >= self._min_holding_bars and self._check_exit(
                close, high, low
            ):
                self.position = 0
                self.entry_price = 0.0
                self.stop_price = 0.0
                self.take_profit = 0.0
                self.cooldown_until = i + self._cooldown_bars
                return CLOSE

        # ---- 2. Cooldown check ----
        if i < self.cooldown_until:
            return HOLD

        # Only enter new positions when flat
        if self.position != 0:
            return HOLD

        # ---- 3. Method 1: Convergence Breakout Entry ----
        raw_signal = HOLD

        if self._m1_state == _STATE_CONVERGED:
            # Check for breakout from zone
            zone = self._m1_zone_center

            if close > zone * (1 + self._config.stop_loss_buffer_pct):
                # Price broke above — monitor for pullback confirmation
                self._m1_state = _STATE_BROKE_UP
                self._m1_breakout_bar = i
                self._m1_breakout_extreme = low  # track pullback low
                # Track the high of the initial breakout candle as first pullback candle
                self._m1_pullback_candle_extreme = high

            elif close < zone * (1 - self._config.stop_loss_buffer_pct):
                # Price broke below — monitor for bounce confirmation
                self._m1_state = _STATE_BROKE_DOWN
                self._m1_breakout_bar = i
                self._m1_breakout_extreme = high  # track bounce high
                # Track the low of the initial breakout candle as first bounce candle
                self._m1_pullback_candle_extreme = low

        elif self._m1_state == _STATE_BROKE_UP:
            zone = self._m1_zone_center
            # Update pullback tracking: find the deepest pullback candle.
            # _m1_breakout_extreme tracks the lowest low seen after breakout.
            # _m1_pullback_candle_extreme tracks the HIGH of that deepest candle.
            if low < self._m1_breakout_extreme:
                self._m1_breakout_extreme = low
                self._m1_pullback_candle_extreme = high  # high of the deepest pullback candle

            # Confirm uptrend:
            # - At least 2 bars after breakout (allow pullback to develop)
            # - Close still above zone (no close below zone)
            # - Close breaks above the HIGH of the deepest pullback candle (momentum confirmed)
            if (
                i - self._m1_breakout_bar >= 2
                and close > zone
                and self._m1_pullback_candle_extreme > 0
                and close > self._m1_pullback_candle_extreme
            ):
                raw_signal = BUY
                self._pending_entry_method = 1
                # Reset so we don't retrigger the same zone
                self._m1_state = _STATE_IDLE

            # Invalidate if price closes well below zone
            elif close < zone * (1 - self._config.stop_loss_buffer_pct * 2):
                self._m1_state = _STATE_IDLE

        elif self._m1_state == _STATE_BROKE_DOWN:
            zone = self._m1_zone_center
            # Update bounce tracking: find the highest bounce candle.
            # _m1_breakout_extreme tracks the highest high seen after breakout.
            # _m1_pullback_candle_extreme tracks the LOW of that highest candle.
            if high > self._m1_breakout_extreme:
                self._m1_breakout_extreme = high
                self._m1_pullback_candle_extreme = low  # low of the highest bounce candle

            # Confirm downtrend:
            # - At least 2 bars after breakout (allow bounce to develop)
            # - Close still below zone (no close above zone)
            # - Close breaks below the LOW of the highest bounce candle (momentum confirmed)
            if (
                i - self._m1_breakout_bar >= 2
                and close < zone
                and self._m1_pullback_candle_extreme > 0
                and close < self._m1_pullback_candle_extreme
            ):
                raw_signal = SELL
                self._pending_entry_method = 1
                self._m1_state = _STATE_IDLE

            # Invalidate if price closes well above zone
            elif close > zone * (1 + self._config.stop_loss_buffer_pct * 2):
                self._m1_state = _STATE_IDLE

        # ---- 4. Method 2: MA20 Retest Entry ----
        # (only if Method 1 didn't fire)
        if (
            raw_signal == HOLD
            and self._m2_state == _RETEST_READY
            and not self._m2_retest_used
        ):
            # Expire method 2 if too many bars have passed since divergence
            max_retest_bars = max(self._config.ma_period_1 * 2, 50)
            if i - self._m2_diverge_bar > max_retest_bars:
                self._m2_state = _RETEST_IDLE
            else:
                touch_threshold = atr * 0.3  # within 0.3 ATR = "touching MA20"

                if self._m2_direction == 1:
                    # Bullish divergence: price should be above MAs
                    # Retest: price dips to touch MA20 but doesn't close below it
                    touching_ma20 = abs(close - ma20_avg) <= touch_threshold
                    if touching_ma20 and close > ma20_avg:
                        # Price touched MA20 from above and is holding → BUY
                        raw_signal = BUY
                        self._pending_entry_method = 2
                        self._m2_retest_low = low    # stop: low of the retest candle
                        self._m2_retest_high = high  # stored for symmetry
                        self._m2_retest_used = True

                elif self._m2_direction == -1:
                    # Bearish divergence: price should be below MAs
                    # Retest: price bounces to touch MA20 but doesn't close above it
                    touching_ma20 = abs(close - ma20_avg) <= touch_threshold
                    if touching_ma20 and close < ma20_avg:
                        # Price touched MA20 from below and is holding → SELL
                        raw_signal = SELL
                        self._pending_entry_method = 2
                        self._m2_retest_low = low    # stored for symmetry
                        self._m2_retest_high = high  # stop: high of the retest candle
                        self._m2_retest_used = True

        # ---- 5. Signal confirmation ----
        if raw_signal == BUY:
            self.signal_count[BUY] += 1
            self.signal_count[SELL] = 0
        elif raw_signal == SELL:
            self.signal_count[SELL] += 1
            self.signal_count[BUY] = 0
        else:
            self.signal_count[BUY] = 0
            self.signal_count[SELL] = 0

        confirmed_signal = HOLD
        if self.signal_count[BUY] >= self._signal_confirmation:
            confirmed_signal = BUY
        elif self.signal_count[SELL] >= self._signal_confirmation:
            confirmed_signal = SELL

        # ---- 6. Position management ----
        if confirmed_signal == BUY:
            entry = close
            if self._pending_entry_method == 2:
                # Method 2 (MA20 retest): stop at retest candle low or below MA20
                stop_from_candle = self._m2_retest_low
                stop_from_ma = ma20_avg * (1 - self._config.stop_loss_buffer_pct)
                stop = min(stop_from_candle, stop_from_ma)
            else:
                # Method 1 (breakout): stop below convergence zone
                stop = (
                    self._m1_zone_center * (1 - self._config.stop_loss_buffer_pct)
                    if self._m1_zone_center > 0
                    else entry * (1 - self._config.stop_loss_pct)
                )
            # Fall back to ATR-based stop if computed stop is invalid
            if stop <= 0 or stop >= entry:
                stop = entry - atr * 1.5
            tp = self._calc_take_profit(1, entry, stop, self._m1_zone_center, i)
            self.position = 1
            self.entry_bar = i
            self.entry_price = entry
            self.stop_price = stop
            self.take_profit = tp
            return BUY

        elif confirmed_signal == SELL:
            entry = close
            if self._pending_entry_method == 2:
                # Method 2 (MA20 retest): stop at retest candle high or above MA20
                stop_from_candle = self._m2_retest_high
                stop_from_ma = ma20_avg * (1 + self._config.stop_loss_buffer_pct)
                stop = max(stop_from_candle, stop_from_ma)
            else:
                # Method 1 (breakout): stop above convergence zone
                stop = (
                    self._m1_zone_center * (1 + self._config.stop_loss_buffer_pct)
                    if self._m1_zone_center > 0
                    else entry * (1 + self._config.stop_loss_pct)
                )
            # Fall back to ATR-based stop if computed stop is invalid
            if stop <= 0 or stop <= entry:
                stop = entry + atr * 1.5
            tp = self._calc_take_profit(-1, entry, stop, self._m1_zone_center, i)
            self.position = -1
            self.entry_bar = i
            self.entry_price = entry
            self.stop_price = stop
            self.take_profit = tp
            return SELL

        return HOLD

    def reset(self):
        """Reset all state (indicators + position management)."""
        self._sma1.reset()
        self._sma2.reset()
        self._sma3.reset()
        self._ema1.reset()
        self._ema2.reset()
        self._ema3.reset()
        self._atr.reset()

        self._convergence_zones.clear()

        self._m1_state = _STATE_IDLE
        self._m1_zone_center = 0.0
        self._m1_zone_bar = 0
        self._m1_breakout_bar = 0
        self._m1_breakout_extreme = 0.0
        self._m1_pullback_candle_extreme = 0.0
        self._converged_bars = 0

        self._m2_state = _RETEST_IDLE
        self._m2_direction = 0
        self._m2_diverge_bar = 0
        self._m2_retest_used = False
        self._pending_entry_method = 1
        self._m2_retest_low = 0.0
        self._m2_retest_high = float("inf")

        self._high_window.clear()
        self._low_window.clear()

        self.position = 0
        self.entry_bar = 0
        self.entry_price = 0.0
        self.stop_price = 0.0
        self.take_profit = 0.0
        self.cooldown_until = 0
        self.signal_count = {BUY: 0, SELL: 0}
        self.bar_index = 0

    def sync_position(self, pos_int: int, entry_price: float = 0.0) -> None:
        """Sync position state from external source (rollback or startup sync)."""
        self.position = pos_int
        self.entry_price = entry_price if pos_int != 0 else 0.0
        if pos_int == 0:
            self.stop_price = 0.0
            self.take_profit = 0.0

    def get_raw_signal(self, close: float, high: float, low: float) -> int:
        """Compute raw signal from current indicator values (no position management).

        Used by the live indicator wrapper to expose signal without
        modifying position state.
        """
        mas = self._get_ma_values()
        atr = self._atr.value
        if mas is None or atr is None:
            return HOLD

        sma1, sma2, sma3, ema1, ema2, ema3 = mas
        is_conv, zone_center = self._is_converged(mas, atr)
        zone = self._m1_zone_center

        # Method 1 state check
        # BROKE_UP: need close above zone AND above the high of the deepest pullback candle
        if (
            self._m1_state == _STATE_BROKE_UP
            and zone > 0
            and close > zone
            and self._m1_pullback_candle_extreme > 0
            and close > self._m1_pullback_candle_extreme
        ):
            return BUY
        # BROKE_DOWN: need close below zone AND below the low of the highest bounce candle
        if (
            self._m1_state == _STATE_BROKE_DOWN
            and zone > 0
            and close < zone
            and self._m1_pullback_candle_extreme > 0
            and close < self._m1_pullback_candle_extreme
        ):
            return SELL

        # Method 2 state check
        ma20_avg = (sma1 + ema1) / 2.0
        touch_threshold = atr * 0.3
        if self._m2_state == _RETEST_READY and not self._m2_retest_used:
            if self._m2_direction == 1:
                if abs(close - ma20_avg) <= touch_threshold and close > ma20_avg:
                    return BUY
            elif self._m2_direction == -1:
                if abs(close - ma20_avg) <= touch_threshold and close < ma20_avg:
                    return SELL

        return HOLD

    @staticmethod
    def calc_position_size(
        equity: float,
        entry_price: float,
        stop_price: float,
        risk_pct: float = 0.20,
    ) -> float:
        """Calculate dynamic position size based on risk per trade.

        Formula: position_size = (equity * risk_pct) / abs(entry_price - stop_price)

        Tight stop → larger position.  Wide stop → smaller position.

        Args:
            equity: Total account equity
            entry_price: Entry price for the position
            stop_price: Stop loss price level
            risk_pct: Maximum risk as fraction of equity (default: 20%)

        Returns:
            Position size in base currency units, or 0.0 if stop is invalid.
        """
        max_loss = equity * risk_pct
        risk_per_unit = abs(entry_price - stop_price)
        if risk_per_unit <= 0:
            return 0.0
        return max_loss / risk_per_unit

    # ---- Indicator value properties (for live indicator wrapper) ----

    @property
    def sma1_value(self) -> Optional[float]:
        return self._sma1.value

    @property
    def sma2_value(self) -> Optional[float]:
        return self._sma2.value

    @property
    def sma3_value(self) -> Optional[float]:
        return self._sma3.value

    @property
    def ema1_value(self) -> Optional[float]:
        return self._ema1.value

    @property
    def ema2_value(self) -> Optional[float]:
        return self._ema2.value

    @property
    def ema3_value(self) -> Optional[float]:
        return self._ema3.value

    @property
    def atr_value(self) -> Optional[float]:
        return self._atr.value

    @property
    def zone_center(self) -> float:
        return self._m1_zone_center
