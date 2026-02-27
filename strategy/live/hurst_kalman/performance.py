"""
Performance Tracker — re-exports from common module.

All functionality lives in ``strategy.live.common.performance``.
This file preserves backward-compatible imports.
"""

from strategy.live.common.performance import (  # noqa: F401
    PerformanceStats,
    PerformanceTracker,
    TradeRecord,
    print_live_performance,
)

__all__ = [
    "TradeRecord",
    "PerformanceStats",
    "PerformanceTracker",
    "print_live_performance",
]

if __name__ == "__main__":
    print_live_performance()
