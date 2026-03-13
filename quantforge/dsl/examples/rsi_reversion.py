"""RSI Mean Reversion — buy oversold, sell overbought.

When RSI drops below oversold level -> BUY.
When RSI rises above overbought level -> SELL.
When RSI returns to neutral zone while in position -> CLOSE.
"""

from quantforge.dsl import Param, Strategy


class RSIReversion(Strategy):
    """RSI Mean Reversion — buy oversold, sell overbought."""

    name = "decl_rsi_reversion"
    timeframe = "15m"

    rsi_period = Param(14, min=7, max=28, step=1)
    oversold = Param(30, min=15, max=40, step=5)
    overbought = Param(70, min=60, max=85, step=5)

    def setup(self):
        self.rsi = self.add_indicator("rsi", self.rsi_period)
        self._position = 0  # track position for exit logic

    def on_bar(self, bar):
        if not self.rsi.ready:
            return self.HOLD

        rsi_val = self.rsi.value

        # Exit: RSI returns to neutral
        if self._position != 0:
            if self._position == 1 and rsi_val >= 50:
                self._position = 0
                return self.CLOSE
            if self._position == -1 and rsi_val <= 50:
                self._position = 0
                return self.CLOSE

        # Entry signals
        if rsi_val <= self.oversold and self._position == 0:
            self._position = 1
            return self.BUY
        if rsi_val >= self.overbought and self._position == 0:
            self._position = -1
            return self.SELL

        return self.HOLD
