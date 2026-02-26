"""
NexusTrader Indicator wrapper for EMA Crossover + Regime Filter strategy.

Real-time computation of fast/slow EMA, ATR, ADX, and market regime
classification.
"""

from collections import deque
from enum import Enum
from typing import Optional

import numpy as np

from nexustrader.constants import KlineInterval
from nexustrader.indicator import Indicator
from nexustrader.schema import BookL1, BookL2, Kline, Trade

from strategy.bitget.regime_ema.core import (
    MarketRegime,
    RegimeEMAConfig,
    calculate_ema_single,
    classify_regime,
)


class Signal(Enum):
    """Trading signals."""

    HOLD = "hold"
    BUY = "buy"      # Fast EMA crosses above slow EMA (trending)
    SELL = "sell"     # Fast EMA crosses below slow EMA (trending)
    CLOSE = "close"   # Regime switched to ranging — flatten


class RegimeEMAIndicator(Indicator):
    """
    Indicator that calculates EMA crossover signals with regime filtering.

    Provides:
    - Fast EMA / Slow EMA
    - ATR (Average True Range)
    - ADX (Average Directional Index)
    - Market regime classification (TRENDING_UP/DOWN, RANGING, HIGH_VOLATILITY)
    - Trading signals gated on regime state
    """

    def __init__(
        self,
        config: Optional[RegimeEMAConfig] = None,
        warmup_period: Optional[int] = None,
        kline_interval: KlineInterval = KlineInterval.HOUR_1,
    ):
        self._config = config or RegimeEMAConfig()
        if warmup_period is None:
            # Need: slow_period for EMA + adx_period*2 for ADX + regime_lookback for ATR mean
            warmup_period = max(
                self._config.slow_period,
                self._config.adx_period * 2,
                self._config.regime_lookback,
            ) + 20

        super().__init__(
            params={
                "fast_period": self._config.fast_period,
                "slow_period": self._config.slow_period,
                "adx_period": self._config.adx_period,
                "adx_trend_threshold": self._config.adx_trend_threshold,
            },
            name="RegimeEMA",
            warmup_period=warmup_period,
            kline_interval=kline_interval,
        )

        # Price history for SMA seeding
        max_history = max(self._config.slow_period, self._config.regime_lookback) + 20
        self._price_history: deque[float] = deque(maxlen=max_history)

        # EMA state
        self._fast_ema: Optional[float] = None
        self._slow_ema: Optional[float] = None
        self._prev_fast_ema: Optional[float] = None
        self._prev_slow_ema: Optional[float] = None
        self._fast_seeded: bool = False
        self._slow_seeded: bool = False

        # ATR state (Wilder's smoothing, incremental)
        self._atr: Optional[float] = None
        self._atr_count: int = 0
        self._prev_close: Optional[float] = None
        self._tr_sum: float = 0.0  # for SMA seed

        # ATR rolling window for regime classification
        self._atr_history: deque[float] = deque(maxlen=self._config.regime_lookback)

        # ADX state (Wilder's smoothing, incremental)
        self._smoothed_tr: Optional[float] = None
        self._smoothed_plus_dm: Optional[float] = None
        self._smoothed_minus_dm: Optional[float] = None
        self._adx: Optional[float] = None
        self._dx_history: deque[float] = deque(maxlen=self._config.adx_period)
        self._adx_seeded: bool = False
        self._dm_count: int = 0  # bars since first DM calculation

        # Running sums for DM/TR SMA seed
        self._tr_seed_sum: float = 0.0
        self._plus_dm_seed_sum: float = 0.0
        self._minus_dm_seed_sum: float = 0.0

        # Previous high/low for DM calculation
        self._prev_high: Optional[float] = None
        self._prev_low: Optional[float] = None

        # Current values
        self._regime: MarketRegime = MarketRegime.RANGING
        self._signal: Signal = Signal.HOLD
        self._last_price: Optional[float] = None
        self._bar_count: int = 0

    def handle_kline(self, kline: Kline) -> None:
        """Process a new kline and update all indicator values."""
        if not self._is_warmed_up:
            self._warmup_data_count += 1
            if self._warmup_data_count >= self.warmup_period:
                self._is_warmed_up = True

        price = float(kline.close)
        high = float(kline.high)
        low = float(kline.low)
        self._last_price = price
        self._bar_count += 1

        self._price_history.append(price)

        # --- Update EMAs ---
        self._prev_fast_ema = self._fast_ema
        self._prev_slow_ema = self._slow_ema

        if not self._fast_seeded:
            if len(self._price_history) >= self._config.fast_period:
                prices = list(self._price_history)
                self._fast_ema = float(np.mean(prices[-self._config.fast_period:]))
                self._fast_seeded = True
        else:
            self._fast_ema = calculate_ema_single(
                self._fast_ema, price, self._config.fast_period
            )

        if not self._slow_seeded:
            if len(self._price_history) >= self._config.slow_period:
                prices = list(self._price_history)
                self._slow_ema = float(np.mean(prices[-self._config.slow_period:]))
                self._slow_seeded = True
        else:
            self._slow_ema = calculate_ema_single(
                self._slow_ema, price, self._config.slow_period
            )

        # --- Update ATR ---
        self._update_atr(high, low, price)

        # --- Update ADX ---
        self._update_adx(high, low, price)

        # Store previous H/L/C
        self._prev_close = price
        self._prev_high = high
        self._prev_low = low

        # --- Classify regime ---
        self._update_regime()

        # --- Generate signal ---
        self._update_signal()

    def _update_atr(self, high: float, low: float, close: float) -> None:
        """Incrementally update ATR using Wilder's smoothing."""
        period = self._config.atr_period

        if self._prev_close is None:
            # First bar: TR = H - L
            tr = high - low
        else:
            hl = high - low
            hc = abs(high - self._prev_close)
            lc = abs(low - self._prev_close)
            tr = max(hl, hc, lc)

        self._atr_count += 1

        if self._atr is None:
            self._tr_sum += tr
            if self._atr_count >= period:
                self._atr = self._tr_sum / period
                self._atr_history.append(self._atr)
        else:
            self._atr = (self._atr * (period - 1) + tr) / period
            self._atr_history.append(self._atr)

    def _update_adx(self, high: float, low: float, close: float) -> None:
        """Incrementally update ADX using Wilder's smoothing."""
        period = self._config.adx_period

        if self._prev_high is None or self._prev_low is None or self._prev_close is None:
            return

        # Directional movement
        up_move = high - self._prev_high
        down_move = self._prev_low - low

        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0

        # True range
        hl = high - low
        hc = abs(high - self._prev_close)
        lc = abs(low - self._prev_close)
        tr = max(hl, hc, lc)

        self._dm_count += 1

        if self._smoothed_tr is None:
            # Accumulating for SMA seed
            self._tr_seed_sum += tr
            self._plus_dm_seed_sum += plus_dm
            self._minus_dm_seed_sum += minus_dm

            if self._dm_count >= period:
                self._smoothed_tr = self._tr_seed_sum
                self._smoothed_plus_dm = self._plus_dm_seed_sum
                self._smoothed_minus_dm = self._minus_dm_seed_sum
                # Calculate first DX
                self._compute_and_store_dx()
        else:
            # Wilder's smoothing
            self._smoothed_tr = self._smoothed_tr - self._smoothed_tr / period + tr
            self._smoothed_plus_dm = (
                self._smoothed_plus_dm - self._smoothed_plus_dm / period + plus_dm
            )
            self._smoothed_minus_dm = (
                self._smoothed_minus_dm - self._smoothed_minus_dm / period + minus_dm
            )
            self._compute_and_store_dx()

    def _compute_and_store_dx(self) -> None:
        """Compute DX from smoothed DI values and update ADX."""
        period = self._config.adx_period

        if self._smoothed_tr is None or self._smoothed_tr < 1e-10:
            return

        di_plus = 100.0 * self._smoothed_plus_dm / self._smoothed_tr
        di_minus = 100.0 * self._smoothed_minus_dm / self._smoothed_tr
        di_sum = di_plus + di_minus

        if di_sum < 1e-10:
            dx = 0.0
        else:
            dx = 100.0 * abs(di_plus - di_minus) / di_sum

        self._dx_history.append(dx)

        if not self._adx_seeded:
            if len(self._dx_history) >= period:
                self._adx = float(np.mean(list(self._dx_history)))
                self._adx_seeded = True
        else:
            self._adx = (self._adx * (period - 1) + dx) / period

    def _update_regime(self) -> None:
        """Classify the current market regime."""
        if (
            self._atr is None
            or self._adx is None
            or self._fast_ema is None
            or self._slow_ema is None
            or len(self._atr_history) < 5
        ):
            self._regime = MarketRegime.RANGING
            return

        atr_mean = float(np.mean(list(self._atr_history)))

        self._regime = classify_regime(
            atr_val=self._atr,
            atr_mean=atr_mean,
            adx_val=self._adx,
            fast_ema=self._fast_ema,
            slow_ema=self._slow_ema,
            trend_atr_threshold=self._config.trend_atr_threshold,
            ranging_atr_threshold=self._config.ranging_atr_threshold,
            adx_trend_threshold=self._config.adx_trend_threshold,
        )

    def _update_signal(self) -> None:
        """Generate trading signal based on EMA crossover + regime filter."""
        is_trending = self._regime in (
            MarketRegime.TRENDING_UP,
            MarketRegime.TRENDING_DOWN,
        )

        # If not trending, signal CLOSE (strategy will flatten positions)
        if not is_trending:
            self._signal = Signal.CLOSE
            return

        # Need both EMAs and previous values for crossover detection
        if (
            self._fast_ema is None
            or self._slow_ema is None
            or self._prev_fast_ema is None
            or self._prev_slow_ema is None
        ):
            self._signal = Signal.HOLD
            return

        prev_diff = self._prev_fast_ema - self._prev_slow_ema
        curr_diff = self._fast_ema - self._slow_ema

        if prev_diff <= 0 and curr_diff > 0:
            self._signal = Signal.BUY
        elif prev_diff >= 0 and curr_diff < 0:
            self._signal = Signal.SELL
        else:
            self._signal = Signal.HOLD

    def handle_bookl1(self, bookl1: BookL1) -> None:
        pass

    def handle_bookl2(self, bookl2: BookL2) -> None:
        pass

    def handle_trade(self, trade: Trade) -> None:
        pass

    # --- Properties ---

    @property
    def value(self) -> dict:
        return {
            "fast_ema": self._fast_ema,
            "slow_ema": self._slow_ema,
            "atr": self._atr,
            "adx": self._adx,
            "regime": self._regime.value,
            "signal": self._signal.value,
        }

    @property
    def fast_ema(self) -> Optional[float]:
        return self._fast_ema

    @property
    def slow_ema(self) -> Optional[float]:
        return self._slow_ema

    @property
    def atr(self) -> Optional[float]:
        return self._atr

    @property
    def adx(self) -> Optional[float]:
        return self._adx

    @property
    def regime(self) -> MarketRegime:
        return self._regime

    @property
    def signal(self) -> Signal:
        return self._signal

    def get_signal(self) -> Signal:
        return self._signal

    @property
    def is_trending(self) -> bool:
        return self._regime in (
            MarketRegime.TRENDING_UP,
            MarketRegime.TRENDING_DOWN,
        )

    def should_stop_loss(
        self, entry_price: float, current_price: float, is_long: bool
    ) -> bool:
        """Check if stop loss should be triggered (price-based only)."""
        if entry_price <= 0:
            return False

        if is_long:
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - current_price) / entry_price

        return pnl_pct < -self._config.stop_loss_pct

    def reset(self) -> None:
        """Reset indicator to initial state."""
        self._price_history.clear()
        self._fast_ema = None
        self._slow_ema = None
        self._prev_fast_ema = None
        self._prev_slow_ema = None
        self._fast_seeded = False
        self._slow_seeded = False
        self._atr = None
        self._atr_count = 0
        self._prev_close = None
        self._tr_sum = 0.0
        self._atr_history.clear()
        self._smoothed_tr = None
        self._smoothed_plus_dm = None
        self._smoothed_minus_dm = None
        self._adx = None
        self._dx_history.clear()
        self._adx_seeded = False
        self._dm_count = 0
        self._tr_seed_sum = 0.0
        self._plus_dm_seed_sum = 0.0
        self._minus_dm_seed_sum = 0.0
        self._prev_high = None
        self._prev_low = None
        self._regime = MarketRegime.RANGING
        self._signal = Signal.HOLD
        self._last_price = None
        self._bar_count = 0
        self.reset_warmup()
