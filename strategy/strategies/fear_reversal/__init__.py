"""
Fear Reversal Trading Strategy

A long-only mean-reversion strategy that enters when extreme fear
creates a bounce opportunity, using a voting system of 5 indicators.

Indicators used:
- RSI(14) for oversold reversal detection
- ATR(14) for volatility-based trailing stops
- SMA(20) for volume baseline
- EMA(200) as dynamic support proxy
- ADX(14) for trend weakness / exhaustion detection
"""

from strategy.strategies.fear_reversal.core import FearReversalConfig

__all__ = ["FearReversalConfig"]
