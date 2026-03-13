"""EMA Crossover — simple trend following strategy.

When fast EMA crosses above slow EMA -> BUY (go long).
When fast EMA crosses below slow EMA -> SELL (go short).
"""

from quantforge.dsl import Param, Strategy


class EMACross(Strategy):
    """EMA Crossover — trend following with dual EMA crossover signals."""

    name = "decl_ema_crossover"
    timeframe = "15m"

    fast_period = Param(12, min=5, max=30, step=2)
    slow_period = Param(26, min=15, max=60, step=5)
    stop_loss_pct = Param(0.05, min=0.02, max=0.10, step=0.01)

    def setup(self):
        self.ema_fast = self.add_indicator("ema", self.fast_period)
        self.ema_slow = self.add_indicator("ema", self.slow_period)

    def on_bar(self, bar):
        if not self.ema_fast.ready or not self.ema_slow.ready:
            return self.HOLD

        if self.ema_fast.crossover(self.ema_slow):
            return self.BUY
        if self.ema_fast.crossunder(self.ema_slow):
            return self.SELL

        return self.HOLD
