"""
NexusTrader Indicator wrapper for Funding Rate Arbitrage strategy.

Delegates all indicator calculations to FundingRateSignalCore, ensuring
100% parity with the backtest signal generator.
"""

from enum import Enum

from nexustrader.constants import KlineInterval
from nexustrader.indicator import Indicator
from nexustrader.schema import BookL1, BookL2, Kline, Trade

from strategy.indicators.funding_rate import CLOSE, FundingRateSignalCore, HOLD, SELL
from strategy.strategies.funding_rate.core import FundingRateConfig


class Signal(Enum):
    """Trading signals for funding rate arbitrage."""

    HOLD = "hold"
    SELL = "sell"
    CLOSE = "close"


# Map int constants to Signal enum
_SIGNAL_MAP = {
    HOLD: Signal.HOLD,
    SELL: Signal.SELL,
    CLOSE: Signal.CLOSE,
}


class FundingRateIndicator(Indicator):
    """
    Indicator that tracks SMA and provides entry/exit signals
    for funding rate arbitrage.

    All indicator calculations are delegated to FundingRateSignalCore,
    which is shared with the backtest signal generator.
    """

    def __init__(
        self,
        config: FundingRateConfig,
        kline_interval: KlineInterval = KlineInterval.HOUR_1,
    ):
        super().__init__()
        self.config = config
        self.kline_interval = kline_interval

        # Shared core
        self._core = FundingRateSignalCore(config)

        self._bar_count: int = 0
        self._warmup_target: int = config.price_sma_period
        self.current_price: float = 0.0

    @property
    def is_warmed_up(self) -> bool:
        return self._bar_count >= self._warmup_target

    @property
    def sma(self) -> float:
        return self._core.sma_value or 0.0

    @property
    def avg_funding_rate(self) -> float:
        return self._core.avg_funding_rate

    @property
    def current_funding_rate(self) -> float:
        return self._core.avg_funding_rate

    def set_funding_rate(self, rate: float) -> None:
        """Update current funding rate from external source."""
        self._core.set_funding_rate(rate)

    def handle_kline(self, kline: Kline) -> None:
        """Process a new kline and update indicators."""
        price = float(kline.close)
        self.current_price = price
        self._bar_count += 1

        self._core.update_indicators_only(close=price)

    def get_signal(
        self, hours_to_next_settlement: float, hours_since_last_settlement: float
    ) -> Signal:
        """Generate trading signal based on current state."""
        if not self.is_warmed_up:
            return Signal.HOLD

        raw = self._core.get_signal(
            hours_to_next_settlement, hours_since_last_settlement
        )
        return _SIGNAL_MAP.get(raw, Signal.HOLD)

    def should_stop_loss(self, entry_price: float, current_price: float) -> bool:
        """Check if stop loss should be triggered for a short position."""
        if entry_price <= 0:
            return False
        adverse_pct = (current_price - entry_price) / entry_price
        return adverse_pct > self.config.stop_loss_pct

    def on_kline(self, kline: Kline) -> None:
        self.handle_kline(kline)

    def on_trade(self, trade: Trade) -> None:
        pass

    def on_bookl1(self, bookl1: BookL1) -> None:
        pass

    def on_bookl2(self, bookl2: BookL2) -> None:
        pass
