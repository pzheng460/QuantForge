"""
Multi-Timeframe Momentum Trading Strategy

A momentum strategy that uses:
- ROC (Rate of Change) for momentum detection
- Triple EMA (fast/slow/trend) for trend confirmation
- ATR for volatility-based trailing stops
- Volume SMA for volume confirmation
"""

from strategy.strategies.momentum.core import (
    MomentumConfig,
    calculate_atr,
    calculate_ema,
    calculate_ema_single,
    calculate_roc,
    calculate_sma,
)

__all__ = [
    "MomentumConfig",
    "calculate_atr",
    "calculate_ema",
    "calculate_ema_single",
    "calculate_roc",
    "calculate_sma",
]
