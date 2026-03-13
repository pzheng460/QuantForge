"""Declarative Strategy API — define strategies in ~15-30 lines.

Usage:
    from quantforge.dsl import Strategy, Param

    class EMACross(Strategy):
        name = "ema_crossover"
        timeframe = "15m"
        fast_period = Param(12, min=5, max=30)
        slow_period = Param(26, min=15, max=60)

        def setup(self):
            self.ema_fast = self.add_indicator("ema", self.fast_period)
            self.ema_slow = self.add_indicator("ema", self.slow_period)

        def on_bar(self, bar):
            if self.ema_fast.crossover(self.ema_slow):
                return self.BUY
            if self.ema_fast.crossunder(self.ema_slow):
                return self.SELL
            return self.HOLD
"""

from quantforge.dsl.api import Bar, Param, Strategy
from quantforge.dsl.indicators import Indicator
from quantforge.dsl.registry import get_strategy, list_strategies

__all__ = [
    "Strategy",
    "Param",
    "Bar",
    "Indicator",
    "get_strategy",
    "list_strategies",
]
