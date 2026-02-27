"""
NexusTrader Indicator wrapper for Multi-Timeframe Momentum strategy.

Delegates all indicator calculations to MomentumSignalCore, ensuring
100% parity with the backtest signal generator.
"""

from enum import Enum
from typing import Optional

from nexustrader.constants import KlineInterval
from nexustrader.indicator import Indicator
from nexustrader.schema import BookL1, BookL2, Kline, Trade

from strategy.bitget.momentum.core import MomentumConfig
from strategy.indicators.momentum import BUY, HOLD, MomentumSignalCore, SELL


class Signal(Enum):
    """Trading signals."""

    HOLD = "hold"
    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"


# Map MomentumSignalCore int constants to Signal enum
_SIGNAL_MAP = {
    HOLD: Signal.HOLD,
    BUY: Signal.BUY,
    SELL: Signal.SELL,
}


class MomentumIndicator(Indicator):
    """
    Indicator that calculates ROC, EMA (fast/slow/trend), ATR, and
    Volume SMA for multi-timeframe momentum signals.

    All indicator calculations are delegated to MomentumSignalCore,
    which is shared with the backtest signal generator.
    """

    def __init__(
        self,
        config: Optional[MomentumConfig] = None,
        warmup_period: Optional[int] = None,
        kline_interval: KlineInterval = KlineInterval.HOUR_1,
    ):
        self._config = config or MomentumConfig()

        # Calculate the real warmup need
        self._real_warmup_period = (
            max(
                self._config.ema_trend,
                self._config.roc_period,
                self._config.atr_period + 1,
                self._config.volume_sma_period,
                self._config.adx_period * 2 + 1,
            )
            + 10
        )

        # Disable framework warmup (Bitget Demo doesn't support request_klines)
        # We manage warmup ourselves via confirmed bar counting
        super().__init__(
            params={
                "roc_period": self._config.roc_period,
                "ema_fast": self._config.ema_fast,
                "ema_slow": self._config.ema_slow,
                "ema_trend": self._config.ema_trend,
            },
            name="Momentum",
            warmup_period=None,
            kline_interval=kline_interval,
        )

        # Shared core (indicators only - no position management)
        self._core = MomentumSignalCore(self._config)

        # Track our own warmup via confirmed bar count
        self._confirmed_bar_count: int = 0

        # Signal (computed from raw indicator values)
        self._signal: Signal = Signal.HOLD
        self._last_price: Optional[float] = None
        self._last_volume: float = 0.0

    def handle_kline(self, kline: Kline) -> None:
        """Process a new kline and update all indicator values.

        Uses bar start timestamp to detect new bars. When a new bar starts,
        the PREVIOUS bar's data is used to update indicators (it's now confirmed).
        This handles exchanges like Bitget where confirm=False is hardcoded.
        """
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

    @property
    def is_warmed_up(self) -> bool:
        """Check if enough confirmed bars have been processed."""
        return self._confirmed_bar_count >= self._real_warmup_period

    def _process_kline_data(self, kline: Kline) -> None:
        """Core kline processing: delegate to MomentumSignalCore."""
        price = float(kline.close)
        volume = float(kline.volume)
        self._last_price = price
        self._last_volume = volume

        self._core.update_indicators_only(
            close=price,
            high=float(kline.high),
            low=float(kline.low),
            volume=volume,
        )

        # Update signal from raw indicator values
        raw = self._core.get_raw_signal(price, volume)
        self._signal = _SIGNAL_MAP.get(raw, Signal.HOLD)

    def handle_bookl1(self, bookl1: BookL1) -> None:
        pass

    def handle_bookl2(self, bookl2: BookL2) -> None:
        pass

    def handle_trade(self, trade: Trade) -> None:
        pass

    # ---------- Properties (delegate to core) ----------

    @property
    def value(self) -> dict:
        return {
            "roc": self._core.roc_value or 0.0,
            "ema_fast": self._core.ema_fast_value,
            "ema_slow": self._core.ema_slow_value,
            "ema_trend": self._core.ema_trend_value,
            "atr": self._core.atr_value,
            "vol_sma": self._core.vol_sma_value or 0.0,
            "signal": self._signal.value,
        }

    @property
    def roc(self) -> float:
        return self._core.roc_value or 0.0

    @property
    def ema_fast(self) -> Optional[float]:
        return self._core.ema_fast_value

    @property
    def ema_slow(self) -> Optional[float]:
        return self._core.ema_slow_value

    @property
    def ema_trend(self) -> Optional[float]:
        return self._core.ema_trend_value

    @property
    def atr(self) -> Optional[float]:
        return self._core.atr_value

    @property
    def vol_sma(self) -> float:
        return self._core.vol_sma_value or 0.0

    @property
    def adx(self) -> Optional[float]:
        return self._core.adx_value

    @property
    def is_trending(self) -> bool:
        return self._core.is_trending

    @property
    def signal(self) -> Signal:
        return self._signal

    def get_signal(self) -> Signal:
        return self._signal

    def should_exit_long(self) -> bool:
        """Check if long position should exit based on momentum/trend."""
        roc = self._core.roc_value
        ema_f = self._core.ema_fast_value
        ema_s = self._core.ema_slow_value
        if roc is None or ema_f is None or ema_s is None:
            return False
        return roc < 0 or ema_f < ema_s

    def should_exit_short(self) -> bool:
        """Check if short position should exit based on momentum/trend."""
        roc = self._core.roc_value
        ema_f = self._core.ema_fast_value
        ema_s = self._core.ema_slow_value
        if roc is None or ema_f is None or ema_s is None:
            return False
        return roc > 0 or ema_f > ema_s

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
        atr = self._core.atr_value
        if atr is None:
            return 0.0
        offset = atr * self._config.atr_multiplier
        if is_long:
            return entry_price - offset
        else:
            return entry_price + offset

    def reset(self) -> None:
        self._core.reset()
        self._signal = Signal.HOLD
        self._last_price = None
        self._last_volume = 0.0
        self._confirmed_bar_count = 0
        if hasattr(self, "_current_bar_start"):
            del self._current_bar_start
        if hasattr(self, "_current_bar_kline"):
            del self._current_bar_kline
