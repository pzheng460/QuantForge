"""MACD Crossover — trend following using MACD line vs signal line.

Uses two EMAs to compute MACD line, and a signal EMA on the MACD line.
When MACD crosses above signal -> BUY.
When MACD crosses below signal -> SELL.
"""

from quantforge.dsl import Param, Strategy


class MACDCross(Strategy):
    """MACD Crossover — trend following with MACD/signal line crossover."""

    name = "decl_macd_cross"
    timeframe = "15m"

    fast_period = Param(12, min=8, max=16, step=2)
    slow_period = Param(26, min=20, max=34, step=2)
    signal_period = Param(9, min=5, max=14, step=1)

    def setup(self):
        self.ema_fast = self.add_indicator("ema", self.fast_period)
        self.ema_slow = self.add_indicator("ema", self.slow_period)
        self.signal_ema = self.add_indicator("ema", self.signal_period)
        self._prev_macd = None
        self._prev_signal = None

    def on_bar(self, bar):
        if not self.ema_fast.ready or not self.ema_slow.ready:
            return self.HOLD

        macd = self.ema_fast.value - self.ema_slow.value

        # Feed MACD value into signal EMA (manually update the inner indicator)
        self.signal_ema._inner.update(macd)
        self.signal_ema._history.appendleft(self.signal_ema._inner.value)
        signal_val = self.signal_ema.value

        if signal_val is None or self._prev_macd is None or self._prev_signal is None:
            self._prev_macd = macd
            self._prev_signal = signal_val
            return self.HOLD

        # Crossover detection
        result = self.HOLD
        if self._prev_macd <= self._prev_signal and macd > signal_val:
            result = self.BUY
        elif self._prev_macd >= self._prev_signal and macd < signal_val:
            result = self.SELL

        self._prev_macd = macd
        self._prev_signal = signal_val
        return result
