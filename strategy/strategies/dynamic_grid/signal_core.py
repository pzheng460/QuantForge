"""Shared Dynamic Grid signal core for backtest and live trading.

Extends the grid trading logic with a volatility-adaptive leverage system:

  Leverage multiplier computation (each bar):
    1. Base leverage from ATR/price volatility regime (4.0x → 0.5x)
    2. Reduced by 50% if ADX > adx_trend_threshold (trending = bad for grids)
    3. Reduced by 25% if ATR > SMA(ATR) (expanding volatility = more directional risk)

  Leverage effect on signal generation:
    High leverage (≥ 3.0x) → effective_entry_lines = max(1, entry_lines - 1)
      (Enter sooner — tight range, mean reversion likely)
    Normal leverage (0.6x – 3.0x) → effective_entry_lines = entry_lines
    Low leverage (≤ 0.6x) → effective_entry_lines = entry_lines + 1
      (Require more confirmation — wide swings, directional risk)

  Daily circuit breaker:
    When stop loss fires → 24-bar cooldown (approximates "rest of day" pause).

Three-method API:
  update(close, high, low) → int
  update_indicators_only(close, high, low) → None
  (No get_raw_signal: grid state is complex; GenericIndicator returns HOLD
   during warmup and switches to update() after enable_live_mode().)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import numpy as np

from strategy.strategies._base.streaming import StreamingADX, StreamingATR, StreamingSMA

if TYPE_CHECKING:
    from strategy.strategies.dynamic_grid.core import DynamicGridConfig


from strategy.strategies._base.signal_core_base import BaseSignalCore, HOLD, BUY, SELL, CLOSE

# Bars in one day (1h interval) — used for daily circuit breaker cooldown
_BARS_PER_DAY = 24


class DynamicGridSignalCore(BaseSignalCore):
    """Dynamic Grid signal core with volatility-adaptive leverage.

    Grid mechanics (SMA + ATR bounds, level-based entries) are identical to
    GridSignalCore. The key addition is a per-bar leverage multiplier that:
      - Tightens the entry threshold in low-vol regimes (more aggressive)
      - Widens the entry threshold in high-vol / trending regimes (conservative)
      - Applies a 24-bar cooldown after a stop loss (daily circuit breaker)

    Usage (backtest):
        core = DynamicGridSignalCore(config)
        for i in range(n):
            signal = core.update(close[i], high[i], low[i])

    Usage (live — via GenericIndicator with use_dual_mode=True):
        # During warmup: update_indicators_only() called each bar (returns HOLD)
        # After enable_live_mode(): update() called, returns live signals
    """

    def __init__(
        self,
        config: DynamicGridConfig,
        min_holding_bars: int = 1,
        cooldown_bars: int = 0,
    ):
        self._config = config

        # Filter params
        self._min_holding_bars = min_holding_bars
        self._cooldown_bars = cooldown_bars

        # ---- Grid indicators ----
        self._sma = StreamingSMA(config.sma_period)
        self._atr = StreamingATR(config.atr_period)

        # ---- Dynamic leverage indicators ----
        # SMA of ATR values for volatility-expansion detection
        self._atr_sma = StreamingSMA(config.atr_sma_period)
        self._adx = StreamingADX(config.adx_period)

        # ---- Grid state ----
        self._grid_lines: Optional[np.ndarray] = None
        self._current_level: int = 0
        self._peak_level: int = 0
        self._trough_level: int = 999
        self._last_recalc: int = 0

        # ---- Dynamic leverage state ----
        self._effective_leverage_mult: float = config.base_leverage_mult

        # ---- Position management ----
        self.position: int = 0  # 0=flat, 1=long, -1=short
        self.entry_price: float = 0.0
        self.cooldown_until: int = 0
        self.bar_index: int = 0

    # ------------------------------------------------------------------ #
    # Warmup mode                                                          #
    # ------------------------------------------------------------------ #

    def update_indicators_only(self, close: float, high: float, low: float) -> None:
        """Update all indicators without generating a signal (warmup mode)."""
        sma_val = self._sma.update(close)  # noqa: F841
        atr_val = self._atr.update(high, low, close)
        if atr_val is not None:
            self._atr_sma.update(atr_val)
        self._adx.update(high, low, close)
        self.bar_index += 1

    # ------------------------------------------------------------------ #
    # Live mode                                                            #
    # ------------------------------------------------------------------ #

    def update(self, close: float, high: float, low: float) -> int:
        """Process one bar and return a trading signal.

        Returns:
            Signal value: HOLD(0), BUY(1), SELL(-1), or CLOSE(2).
        """
        sma_val = self._sma.update(close)
        atr_val = self._atr.update(high, low, close)
        if atr_val is not None:
            self._atr_sma.update(atr_val)
        self._adx.update(high, low, close)

        self.bar_index += 1
        i = self.bar_index

        # Need SMA and ATR to be ready
        if sma_val is None or atr_val is None or atr_val <= 0:
            return HOLD

        # ---- Compute dynamic leverage multiplier ----
        self._effective_leverage_mult = self._compute_leverage_mult(close, atr_val)

        # ---- Recalculate grid periodically ----
        if self._grid_lines is None or (
            i - self._last_recalc >= self._config.recalc_period
        ):
            center = sma_val
            half_range = self._config.atr_multiplier * atr_val
            upper = center + half_range
            lower = center - half_range
            if upper <= lower:
                return HOLD
            self._grid_lines = np.linspace(lower, upper, self._config.grid_count + 1)
            self._last_recalc = i
            self._current_level = int(np.searchsorted(self._grid_lines, close))
            self._current_level = max(
                0, min(self._config.grid_count, self._current_level)
            )
            self._peak_level = self._current_level
            self._trough_level = self._current_level

        if self._grid_lines is None:
            return HOLD

        # ---- Get current grid level ----
        new_level = int(np.searchsorted(self._grid_lines, close))
        new_level = max(0, min(self._config.grid_count, new_level))

        # ---- Stop loss (with daily circuit-breaker cooldown) ----
        if self.position != 0 and self.entry_price > 0:
            if self.position == 1:
                loss = (self.entry_price - close) / self.entry_price
            else:
                loss = (close - self.entry_price) / self.entry_price
            if loss > self._config.stop_loss_pct:
                self.position = 0
                self.entry_price = 0.0
                self._peak_level = new_level
                self._trough_level = new_level
                # Apply daily circuit-breaker: 24-bar cooldown after stop loss
                self.cooldown_until = i + _BARS_PER_DAY
                self._current_level = new_level
                return CLOSE

        # ---- Cooldown check ----
        if i < self.cooldown_until:
            self._current_level = new_level
            return HOLD

        # ---- Track peak / trough ----
        if new_level > self._peak_level:
            self._peak_level = new_level
        if new_level < self._trough_level:
            self._trough_level = new_level

        # ---- Dynamic entry and profit thresholds ----
        entry_lines = self._effective_entry_lines()
        profit_lines = self._config.profit_lines

        # ---- Signal generation (same mechanics as GridSignalCore) ----
        if self.position == 0:
            if (
                self._peak_level - new_level >= entry_lines
                and new_level <= self._config.grid_count // 2
            ):
                self.position = 1
                self.entry_price = close
                self._trough_level = new_level
                self._peak_level = new_level
                self._current_level = new_level
                return BUY

            if (
                new_level - self._trough_level >= entry_lines
                and new_level >= self._config.grid_count // 2
            ):
                self.position = -1
                self.entry_price = close
                self._peak_level = new_level
                self._trough_level = new_level
                self._current_level = new_level
                return SELL

        elif self.position == 1:
            if new_level - self._trough_level >= profit_lines:
                self.position = 0
                self.entry_price = 0.0
                self._peak_level = new_level
                self._trough_level = new_level
                self.cooldown_until = i + self._cooldown_bars
                self._current_level = new_level
                return CLOSE

        elif self.position == -1:
            if self._peak_level - new_level >= profit_lines:
                self.position = 0
                self.entry_price = 0.0
                self._peak_level = new_level
                self._trough_level = new_level
                self.cooldown_until = i + self._cooldown_bars
                self._current_level = new_level
                return CLOSE

        self._current_level = new_level
        return HOLD

    # ------------------------------------------------------------------ #
    # Dynamic leverage helpers                                             #
    # ------------------------------------------------------------------ #

    def _compute_leverage_mult(self, close: float, atr_val: float) -> float:
        """Compute the current leverage multiplier from vol/trend indicators.

        Step 1: Base leverage from ATR/price volatility ratio.
        Step 2: Reduce if ADX signals a strong trend (bad for grid).
        Step 3: Reduce if ATR is expanding above its own SMA (rising vol risk).
        """
        if close <= 0:
            return self._config.base_leverage_mult

        atr_ratio = atr_val / close
        cfg = self._config

        # Step 1: Volatility regime
        if atr_ratio < cfg.vol_low_threshold:
            lev = cfg.low_vol_mult
        elif atr_ratio < cfg.vol_med_threshold:
            lev = cfg.med_vol_mult
        elif atr_ratio < cfg.vol_high_threshold:
            lev = cfg.normal_vol_mult
        else:
            lev = cfg.high_vol_mult

        # Step 2: ADX trend filter
        adx_val = self._adx.value
        if adx_val is not None and adx_val > cfg.adx_trend_threshold:
            lev *= 1.0 - cfg.trend_leverage_reduction

        # Step 3: Volatility expansion filter
        atr_sma_val = self._atr_sma.value
        if atr_sma_val is not None and atr_sma_val > 0 and atr_val > atr_sma_val:
            lev *= 1.0 - cfg.vol_expanding_reduction

        return max(0.1, lev)

    def _effective_entry_lines(self) -> int:
        """Compute effective entry lines based on current leverage multiplier.

        High leverage (≥ 3.0) → enter on fewer grid crossings (aggressive).
        Low leverage  (≤ 0.6) → require more crossings (conservative).
        Normal range: use configured entry_lines unchanged.
        """
        lev = self._effective_leverage_mult
        base = self._config.entry_lines
        if lev >= 3.0:
            return max(1, base - 1)
        elif lev <= 0.6:
            return base + 1
        return base

    # ------------------------------------------------------------------ #
    # State management                                                     #
    # ------------------------------------------------------------------ #

    def reset(self) -> None:
        """Reset all state."""
        self._sma.reset()
        self._atr.reset()
        self._atr_sma.reset()
        self._adx.reset()
        self._grid_lines = None
        self._current_level = 0
        self._peak_level = 0
        self._trough_level = 999
        self._last_recalc = 0
        self._effective_leverage_mult = self._config.base_leverage_mult
        self.position = 0
        self.entry_price = 0.0
        self.cooldown_until = 0
        self.bar_index = 0


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
    def atr_sma_value(self) -> Optional[float]:
        return self._atr_sma.value

    @property
    def adx_value(self) -> Optional[float]:
        return self._adx.value

    @property
    def effective_leverage_mult(self) -> float:
        """Current leverage multiplier (used by live calculate_position_size_fn)."""
        return self._effective_leverage_mult

    @property
    def grid_lines(self) -> Optional[np.ndarray]:
        return self._grid_lines

    @property
    def current_level(self) -> int:
        return self._current_level

    @property
    def peak_level(self) -> int:
        return self._peak_level

    @property
    def trough_level(self) -> int:
        return self._trough_level
