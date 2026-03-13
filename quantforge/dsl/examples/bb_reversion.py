"""Bollinger Bands Mean Reversion — buy at lower band, sell at upper band.

When price touches lower band -> BUY (oversold).
When price touches upper band -> SELL (overbought).
When price returns near SMA -> CLOSE.
"""

from quantforge.dsl import Param, Strategy


class BBReversion(Strategy):
    """Bollinger Bands Mean Reversion — trade band bounces."""

    name = "decl_bb_reversion"
    timeframe = "15m"

    bb_period = Param(20, min=10, max=40, step=5)
    bb_multiplier = Param(2.0, min=1.5, max=3.0, step=0.5)
    exit_threshold = Param(0.3, min=0.1, max=0.5, step=0.1)

    def setup(self):
        self.bb = self.add_indicator("bb", self.bb_period, self.bb_multiplier)
        self._position = 0

    def on_bar(self, bar):
        if not self.bb.ready:
            return self.HOLD

        price = bar.close
        sma = self.bb.value
        upper = self.bb.upper
        lower = self.bb.lower

        # Exit: price returns near SMA
        if self._position != 0 and sma is not None and upper is not None:
            band_half = upper - sma
            if band_half > 1e-10:
                distance_ratio = abs(price - sma) / band_half
                if distance_ratio < self.exit_threshold:
                    self._position = 0
                    return self.CLOSE

        # Entry signals
        if self._position == 0:
            if lower is not None and price <= lower:
                self._position = 1
                return self.BUY
            if upper is not None and price >= upper:
                self._position = -1
                return self.SELL

        return self.HOLD
