"""
VWAP Mean Reversion Trading Strategy

A mean-reversion strategy that uses:
- VWAP (Volume Weighted Average Price) with daily reset
- Z-Score of price deviation from VWAP
- RSI (14) as secondary confirmation filter
"""

from strategy.bitget.vwap.core import (
    VWAPConfig,
    calculate_rsi,
    calculate_vwap,
    calculate_vwap_zscore,
)
from strategy.bitget.vwap.indicator import (
    VWAPIndicator,
    Signal,
)

__all__ = [
    "VWAPConfig",
    "calculate_rsi",
    "calculate_vwap",
    "calculate_vwap_zscore",
    "VWAPIndicator",
    "Signal",
]
