"""
NexusTrader Indicator wrapper for EMA Crossover + Regime Filter strategy.

Delegates all indicator calculations to RegimeEMASignalCore, ensuring
100% parity with the backtest signal generator.
"""

from enum import Enum
from typing import Optional

from nexustrader.constants import KlineInterval
from nexustrader.indicator import Indicator
from nexustrader.schema import BookL1, BookL2, Kline, Trade

from strategy.bitget.regime_ema.core import MarketRegime, RegimeEMAConfig
from strategy.indicators.regime_ema import BUY, CLOSE, HOLD, RegimeEMASignalCore, SELL


class Signal(Enum):
    """Trading signals."""

    HOLD = "hold"
    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"


# Map int constants to Signal enum
_SIGNAL_MAP = {
    HOLD: Signal.HOLD,
    BUY: Signal.BUY,
    SELL: Signal.SELL,
    CLOSE: Signal.CLOSE,
}


class RegimeEMAIndicator(Indicator):
    """
    Indicator that calculates EMA crossover signals with regime filtering.

    All indicator calculations are delegated to RegimeEMASignalCore,
    which is shared with the backtest signal generator.
    """

    def __init__(
        self,
        config: Optional[RegimeEMAConfig] = None,
        warmup_period: Optional[int] = None,
        kline_interval: KlineInterval = KlineInterval.HOUR_1,
    ):
        self._config = config or RegimeEMAConfig()

        self._real_warmup_period = (
            max(
                self._config.slow_period,
                self._config.adx_period * 2,
                self._config.regime_lookback,
            )
            + 20
        )

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

        # Shared core
        self._core = RegimeEMASignalCore(self._config)

        self._confirmed_bar_count: int = 0
        self._signal: Signal = Signal.HOLD
        self._last_price: Optional[float] = None

    def handle_kline(self, kline: Kline) -> None:
        """Process a new kline using bar confirmation via timestamp change."""
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
        return self._confirmed_bar_count >= self._real_warmup_period

    def _process_kline_data(self, kline: Kline) -> None:
        price = float(kline.close)
        high = float(kline.high)
        low = float(kline.low)
        self._last_price = price

        self._core.update_indicators_only(close=price, high=high, low=low)

        raw = self._core.get_raw_signal()
        self._signal = _SIGNAL_MAP.get(raw, Signal.HOLD)

    def handle_bookl1(self, bookl1: BookL1) -> None:
        pass

    def handle_bookl2(self, bookl2: BookL2) -> None:
        pass

    def handle_trade(self, trade: Trade) -> None:
        pass

    @property
    def value(self) -> dict:
        return {
            "fast_ema": self._core.ema_fast_value,
            "slow_ema": self._core.ema_slow_value,
            "atr": self._core.atr_value,
            "adx": self._core.adx_value,
            "regime": self._core.regime.value,
            "signal": self._signal.value,
        }

    @property
    def fast_ema(self) -> Optional[float]:
        return self._core.ema_fast_value

    @property
    def slow_ema(self) -> Optional[float]:
        return self._core.ema_slow_value

    @property
    def atr(self) -> Optional[float]:
        return self._core.atr_value

    @property
    def adx(self) -> Optional[float]:
        return self._core.adx_value

    @property
    def regime(self) -> MarketRegime:
        return self._core.regime

    @property
    def signal(self) -> Signal:
        return self._signal

    def get_signal(self) -> Signal:
        return self._signal

    @property
    def is_trending(self) -> bool:
        return self._core.is_trending

    def should_stop_loss(
        self, entry_price: float, current_price: float, is_long: bool
    ) -> bool:
        if entry_price <= 0:
            return False
        if is_long:
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - current_price) / entry_price
        return pnl_pct < -self._config.stop_loss_pct

    def reset(self) -> None:
        self._core.reset()
        self._signal = Signal.HOLD
        self._last_price = None
        self._confirmed_bar_count = 0
        if hasattr(self, "_current_bar_start"):
            del self._current_bar_start
        if hasattr(self, "_current_bar_kline"):
            del self._current_bar_kline
