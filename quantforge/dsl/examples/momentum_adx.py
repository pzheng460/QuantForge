"""Momentum + ADX — trend following with regime filter.

Only trades when ADX indicates a trending market.
Uses EMA crossover for direction, ROC for momentum confirmation.
"""

from quantforge.dsl import Param, Strategy


class MomentumADX(Strategy):
    """Momentum + ADX — regime-filtered trend following."""

    name = "decl_momentum_adx"
    timeframe = "15m"

    fast_period = Param(12, min=5, max=20, step=2)
    slow_period = Param(26, min=15, max=50, step=5)
    adx_period = Param(14, min=10, max=20, step=2)
    adx_threshold = Param(25.0, min=15.0, max=35.0, step=5.0)
    roc_period = Param(10, min=5, max=20, step=5)
    roc_threshold = Param(0.02, min=0.01, max=0.05, step=0.01)

    def setup(self):
        self.ema_fast = self.add_indicator("ema", self.fast_period)
        self.ema_slow = self.add_indicator("ema", self.slow_period)
        self.adx = self.add_indicator("adx", self.adx_period)
        self.roc = self.add_indicator("roc", self.roc_period)
        self._position = 0

    def on_bar(self, bar):
        if not all(ind.ready for ind in [self.ema_fast, self.ema_slow, self.roc]):
            return self.HOLD

        adx_val = self.adx.value
        is_trending = adx_val is not None and adx_val >= self.adx_threshold
        roc_val = self.roc.value

        # Exit in ranging market
        if not is_trending and self._position != 0:
            self._position = 0
            return self.CLOSE

        # Entry only in trending market
        if is_trending and self._position == 0:
            if self.ema_fast.crossover(self.ema_slow) and roc_val > self.roc_threshold:
                self._position = 1
                return self.BUY
            if (
                self.ema_fast.crossunder(self.ema_slow)
                and roc_val < -self.roc_threshold
            ):
                self._position = -1
                return self.SELL

        # Trend reversal exit
        if self._position == 1 and roc_val is not None and roc_val < 0:
            self._position = 0
            return self.CLOSE
        if self._position == -1 and roc_val is not None and roc_val > 0:
            self._position = 0
            return self.CLOSE

        return self.HOLD
