"""
Bollinger Band Mean Reversion Trading Strategy

A mean-reversion strategy that uses:
- Bollinger Bands for overbought/oversold detection
- Mean reversion to SMA for exit signals
- Price-based stop loss for risk management
"""

from strategy.strategies.bollinger_band.core import (
    BBConfig,
    calculate_bb_single,
    calculate_bollinger_bands,
)

__all__ = [
    "calculate_bollinger_bands",
    "calculate_bb_single",
    "BBConfig",
]
