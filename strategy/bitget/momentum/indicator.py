"""
NexusTrader Indicator wrapper for Multi-Timeframe Momentum strategy.

This module wraps the core algorithms into a NexusTrader Indicator class
that can be registered with a Strategy for live / paper trading.
"""

from collections import deque
from enum import Enum
from typing import Optional

import numpy as np

from nexustrader.constants import KlineInterval
from nexustrader.indicator import Indicator
from nexustrader.schema import BookL1, BookL2, Kline, Trade

from strategy.bitget.momentum.core import (
    MomentumConfig,
    calculate_ema_single,
)

# ADX uses Wilder smoothing — we compute it incrementally.


class Signal(Enum):
    """Trading signals."""

    HOLD = "hold"
    BUY = "buy"       # Momentum long entry
    SELL = "sell"      # Momentum short entry
    CLOSE = "close"    # Exit / stop


class MomentumIndicator(Indicator):
    """
    Indicator that calculates ROC, EMA (fast/slow/trend), ATR, and
    Volume SMA for multi-timeframe momentum signals.

    Provides:
    - Rate of Change (ROC)
    - Triple EMA (fast / slow / trend)
    - ATR (Wilder smoothing)
    - Volume SMA
    - Trading signals based on momentum + trend + volume confirmation
    """

    def __init__(
        self,
        config: Optional[MomentumConfig] = None,
        warmup_period: Optional[int] = None,
        kline_interval: KlineInterval = KlineInterval.HOUR_1,
    ):
        self._config = config or MomentumConfig()
        # Calculate the real warmup need
        self._real_warmup_period = max(
            self._config.ema_trend,
            self._config.roc_period,
            self._config.atr_period + 1,
            self._config.volume_sma_period,
            self._config.adx_period * 2 + 1,  # ADX needs 2*period bars
        ) + 10

        # Disable framework warmup (it fails on Bitget Demo)
        # We manage warmup ourselves via confirmed bar counting
        super().__init__(
            params={
                "roc_period": self._config.roc_period,
                "ema_fast": self._config.ema_fast,
                "ema_slow": self._config.ema_slow,
                "ema_trend": self._config.ema_trend,
            },
            name="Momentum",
            warmup_period=None,  # Disable framework warmup
            kline_interval=kline_interval,
        )
        # Track our own warmup via confirmed bar count
        self._confirmed_bar_count: int = 0

        roc_len = self._config.roc_period + 2
        ema_seed = max(self._config.ema_fast, self._config.ema_slow, self._config.ema_trend) + 2
        max_history = max(roc_len, ema_seed, self._config.volume_sma_period + 2)

        self._close_history: deque[float] = deque(maxlen=max_history)
        self._volume_history: deque[float] = deque(maxlen=self._config.volume_sma_period + 2)

        # EMA state (incremental)
        self._ema_fast_val: Optional[float] = None
        self._ema_slow_val: Optional[float] = None
        self._ema_trend_val: Optional[float] = None
        self._ema_init_count: int = 0

        # ATR state (Wilder smoothing)
        self._atr_val: Optional[float] = None
        self._atr_init_count: int = 0
        self._tr_history: deque[float] = deque(maxlen=self._config.atr_period + 2)
        self._prev_close: Optional[float] = None

        # ROC
        self._roc_val: float = 0.0

        # Volume SMA
        self._vol_sma_val: float = 0.0

        # ADX state (incremental Wilder smoothing)
        self._adx_period: int = self._config.adx_period
        self._adx_trend_threshold: float = self._config.adx_trend_threshold
        self._plus_dm_smooth: float = 0.0
        self._minus_dm_smooth: float = 0.0
        self._tr_smooth: float = 0.0
        self._dx_history: deque[float] = deque(maxlen=self._adx_period + 2)
        self._adx_val: Optional[float] = None
        self._adx_init_count: int = 0
        self._prev_high: Optional[float] = None
        self._prev_low: Optional[float] = None
        self._is_trending: bool = False

        # Signal
        self._signal: Signal = Signal.HOLD
        self._last_price: Optional[float] = None
        self._bar_count: int = 0

    def handle_kline(self, kline: Kline) -> None:
        """Process a new kline and update all indicator values.

        Uses bar start timestamp to detect new bars. When a new bar starts,
        the PREVIOUS bar's data is used to update indicators (it's now confirmed).
        This handles exchanges like Bitget where confirm=False is hardcoded.
        """
        bar_start = int(kline.start)

        if not hasattr(self, '_current_bar_start'):
            # First kline ever: just store it, don't process yet
            self._current_bar_start = bar_start
            self._current_bar_kline = kline
            return

        if bar_start != self._current_bar_start:
            # New bar started → previous bar is now confirmed/closed
            confirmed_kline = self._current_bar_kline
            self._confirmed_bar_count += 1
            self._process_kline_data(confirmed_kline)

            # Update to new bar
            self._current_bar_start = bar_start
            self._current_bar_kline = kline
        else:
            # Same bar, just update the latest kline data (for real-time price)
            self._current_bar_kline = kline

    @property
    def is_warmed_up(self) -> bool:
        """Check if enough confirmed bars have been processed."""
        return self._confirmed_bar_count >= self._real_warmup_period

    def _process_kline_data(self, kline: Kline) -> None:
        """Core kline processing: update all indicator state."""
        price = float(kline.close)
        high = float(kline.high)
        low = float(kline.low)
        volume = float(kline.volume)
        self._last_price = price
        self._bar_count += 1

        # ---------- Store history ----------
        self._close_history.append(price)
        self._volume_history.append(volume)

        # ---------- EMA update ----------
        self._ema_init_count += 1
        if self._ema_init_count == self._config.ema_fast:
            self._ema_fast_val = float(np.mean(list(self._close_history)[-self._config.ema_fast:]))
        elif self._ema_init_count > self._config.ema_fast and self._ema_fast_val is not None:
            self._ema_fast_val = calculate_ema_single(self._ema_fast_val, price, self._config.ema_fast)

        if self._ema_init_count == self._config.ema_slow:
            self._ema_slow_val = float(np.mean(list(self._close_history)[-self._config.ema_slow:]))
        elif self._ema_init_count > self._config.ema_slow and self._ema_slow_val is not None:
            self._ema_slow_val = calculate_ema_single(self._ema_slow_val, price, self._config.ema_slow)

        if self._ema_init_count == self._config.ema_trend:
            self._ema_trend_val = float(np.mean(list(self._close_history)[-self._config.ema_trend:]))
        elif self._ema_init_count > self._config.ema_trend and self._ema_trend_val is not None:
            self._ema_trend_val = calculate_ema_single(self._ema_trend_val, price, self._config.ema_trend)

        # ---------- ATR (True Range) ----------
        if self._prev_close is not None:
            hl = high - low
            hc = abs(high - self._prev_close)
            lc = abs(low - self._prev_close)
            tr = max(hl, hc, lc)
        else:
            tr = high - low
        self._tr_history.append(tr)
        self._prev_close = price

        self._atr_init_count += 1
        period = self._config.atr_period
        if self._atr_init_count == period + 1:
            # SMA seed (skip first TR which has no prev close)
            self._atr_val = float(np.mean(list(self._tr_history)[-period:]))
        elif self._atr_init_count > period + 1 and self._atr_val is not None:
            self._atr_val = (self._atr_val * (period - 1) + tr) / period

        # ---------- ADX (incremental) ----------
        if self._prev_high is not None and self._prev_low is not None:
            up = high - self._prev_high
            down = self._prev_low - low
            plus_dm = up if (up > down and up > 0) else 0.0
            minus_dm = down if (down > up and down > 0) else 0.0

            self._adx_init_count += 1
            p = self._adx_period

            if self._adx_init_count <= p:
                # Accumulate for SMA seed
                self._plus_dm_smooth += plus_dm
                self._minus_dm_smooth += minus_dm
                self._tr_smooth += tr
            elif self._adx_init_count == p + 1:
                # First smoothed values are just the sums (Wilder method)
                self._plus_dm_smooth += plus_dm
                self._minus_dm_smooth += minus_dm
                self._tr_smooth += tr
                # Compute first DX
                if self._tr_smooth > 0:
                    plus_di = 100.0 * self._plus_dm_smooth / self._tr_smooth
                    minus_di = 100.0 * self._minus_dm_smooth / self._tr_smooth
                    di_sum = plus_di + minus_di
                    dx = 100.0 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0
                    self._dx_history.append(dx)
            else:
                # Wilder smoothing
                self._plus_dm_smooth = self._plus_dm_smooth - self._plus_dm_smooth / p + plus_dm
                self._minus_dm_smooth = self._minus_dm_smooth - self._minus_dm_smooth / p + minus_dm
                self._tr_smooth = self._tr_smooth - self._tr_smooth / p + tr

                if self._tr_smooth > 0:
                    plus_di = 100.0 * self._plus_dm_smooth / self._tr_smooth
                    minus_di = 100.0 * self._minus_dm_smooth / self._tr_smooth
                    di_sum = plus_di + minus_di
                    dx = 100.0 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0
                    self._dx_history.append(dx)

                    if self._adx_val is None and len(self._dx_history) >= p:
                        self._adx_val = float(np.mean(list(self._dx_history)[-p:]))
                    elif self._adx_val is not None:
                        self._adx_val = (self._adx_val * (p - 1) + dx) / p

            self._is_trending = self._adx_val is not None and self._adx_val >= self._adx_trend_threshold

        self._prev_high = high
        self._prev_low = low

        # ---------- ROC ----------
        closes = list(self._close_history)
        rp = self._config.roc_period
        if len(closes) > rp:
            prev = closes[-(rp + 1)]
            if prev != 0:
                self._roc_val = (price - prev) / prev
            else:
                self._roc_val = 0.0
        else:
            self._roc_val = 0.0

        # ---------- Volume SMA ----------
        vols = list(self._volume_history)
        vsp = self._config.volume_sma_period
        if len(vols) >= vsp:
            self._vol_sma_val = float(np.mean(vols[-vsp:]))
        else:
            self._vol_sma_val = 0.0

        # ---------- Signal ----------
        self._update_signal(price, volume)

    def _update_signal(self, price: float, volume: float) -> None:
        """Generate trading signal based on momentum + trend + volume."""
        if (
            self._ema_fast_val is None
            or self._ema_slow_val is None
            or self._ema_trend_val is None
            or self._atr_val is None
        ):
            self._signal = Signal.HOLD
            return

        roc = self._roc_val
        roc_th = self._config.roc_threshold
        vol_ok = volume > self._vol_sma_val * self._config.volume_threshold if self._vol_sma_val > 0 else False

        # Long entry (regime filter: only in trending market)
        long_ok = (
            self._is_trending
            and roc > roc_th
            and self._ema_fast_val > self._ema_slow_val
            and price > self._ema_trend_val
            and vol_ok
        )
        # Short entry (symmetric)
        short_ok = (
            self._is_trending
            and roc < -roc_th
            and self._ema_fast_val < self._ema_slow_val
            and price < self._ema_trend_val
            and vol_ok
        )

        if long_ok:
            self._signal = Signal.BUY
        elif short_ok:
            self._signal = Signal.SELL
        else:
            self._signal = Signal.HOLD

    def handle_bookl1(self, bookl1: BookL1) -> None:
        pass

    def handle_bookl2(self, bookl2: BookL2) -> None:
        pass

    def handle_trade(self, trade: Trade) -> None:
        pass

    # ---------- Properties ----------

    @property
    def value(self) -> dict:
        return {
            "roc": self._roc_val,
            "ema_fast": self._ema_fast_val,
            "ema_slow": self._ema_slow_val,
            "ema_trend": self._ema_trend_val,
            "atr": self._atr_val,
            "vol_sma": self._vol_sma_val,
            "signal": self._signal.value,
        }

    @property
    def roc(self) -> float:
        return self._roc_val

    @property
    def ema_fast(self) -> Optional[float]:
        return self._ema_fast_val

    @property
    def ema_slow(self) -> Optional[float]:
        return self._ema_slow_val

    @property
    def ema_trend(self) -> Optional[float]:
        return self._ema_trend_val

    @property
    def atr(self) -> Optional[float]:
        return self._atr_val

    @property
    def vol_sma(self) -> float:
        return self._vol_sma_val

    @property
    def adx(self) -> Optional[float]:
        return self._adx_val

    @property
    def is_trending(self) -> bool:
        return self._is_trending

    @property
    def signal(self) -> Signal:
        return self._signal

    def get_signal(self) -> Signal:
        return self._signal

    def should_exit_long(self) -> bool:
        """Check if long position should exit based on momentum/trend."""
        if self._ema_fast_val is None or self._ema_slow_val is None:
            return False
        return self._roc_val < 0 or self._ema_fast_val < self._ema_slow_val

    def should_exit_short(self) -> bool:
        """Check if short position should exit based on momentum/trend."""
        if self._ema_fast_val is None or self._ema_slow_val is None:
            return False
        return self._roc_val > 0 or self._ema_fast_val > self._ema_slow_val

    def should_stop_loss(
        self, entry_price: float, current_price: float, is_long: bool
    ) -> bool:
        """Check if stop loss should be triggered (hard percentage stop)."""
        if entry_price <= 0:
            return False
        if is_long:
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - current_price) / entry_price
        return pnl_pct < -self._config.stop_loss_pct

    def get_atr_stop(self, entry_price: float, is_long: bool) -> float:
        """Calculate ATR-based stop level."""
        if self._atr_val is None:
            return 0.0
        offset = self._atr_val * self._config.atr_multiplier
        if is_long:
            return entry_price - offset
        else:
            return entry_price + offset

    def reset(self) -> None:
        self._close_history.clear()
        self._volume_history.clear()
        self._ema_fast_val = None
        self._ema_slow_val = None
        self._ema_trend_val = None
        self._ema_init_count = 0
        self._atr_val = None
        self._atr_init_count = 0
        self._tr_history.clear()
        self._prev_close = None
        self._roc_val = 0.0
        self._vol_sma_val = 0.0
        self._plus_dm_smooth = 0.0
        self._minus_dm_smooth = 0.0
        self._tr_smooth = 0.0
        self._dx_history.clear()
        self._adx_val = None
        self._adx_init_count = 0
        self._prev_high = None
        self._prev_low = None
        self._is_trending = False
        self._signal = Signal.HOLD
        self._last_price = None
        self._bar_count = 0
        self._confirmed_bar_count = 0
        if hasattr(self, '_current_bar_start'):
            del self._current_bar_start
        if hasattr(self, '_current_bar_kline'):
            del self._current_bar_kline
