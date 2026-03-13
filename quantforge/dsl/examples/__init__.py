"""Example strategies using the declarative Strategy API.

Import this module to auto-register all example strategies.
"""

from quantforge.dsl.examples.bb_reversion import BBReversion
from quantforge.dsl.examples.ema_cross import EMACross
from quantforge.dsl.examples.macd_cross import MACDCross
from quantforge.dsl.examples.momentum_adx import MomentumADX
from quantforge.dsl.examples.rsi_reversion import RSIReversion

__all__ = [
    "EMACross",
    "RSIReversion",
    "MACDCross",
    "BBReversion",
    "MomentumADX",
]
