"""
Core algorithms for Grid Trading strategy.

Strategy logic:
- Create equal-spaced grid lines between upper and lower bounds
- Buy when price crosses down through a grid line
- Sell when price crosses up through a grid line
- Dynamic grid bounds using SMA + ATR or Bollinger Bands
- Track grid level positions to avoid over-trading

Grid bounds calculation:
- upper = SMA(close, sma_period) + atr_multiplier * ATR(close, atr_period)
- lower = SMA(close, sma_period) - atr_multiplier * ATR(close, atr_period)
- Recalculate grid every N bars (recalc_period)
"""

from dataclasses import dataclass, field
from typing import List

import numpy as np


# ---------------------------------------------------------------------------
# Helper functions (reuse from momentum strategy)
# ---------------------------------------------------------------------------

def calculate_sma(values: np.ndarray, period: int) -> np.ndarray:
    """Calculate Simple Moving Average."""
    n = len(values)
    sma = np.full(n, np.nan)
    for i in range(period - 1, n):
        sma[i] = np.mean(values[i - period + 1 : i + 1])
    return sma


def calculate_atr(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    period: int,
) -> np.ndarray:
    """Calculate Average True Range using Wilder smoothing."""
    n = len(closes)
    atr = np.full(n, np.nan)
    if n < period + 1:
        return atr

    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1])
        lc = abs(lows[i] - closes[i - 1])
        tr[i] = max(hl, hc, lc)

    # SMA seed for first period
    atr[period] = np.mean(tr[1 : period + 1])
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def calculate_bollinger_bands(
    prices: np.ndarray, period: int, std_dev: float = 2.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Calculate Bollinger Bands (middle, upper, lower)."""
    n = len(prices)
    middle = calculate_sma(prices, period)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    
    for i in range(period - 1, n):
        std = np.std(prices[i - period + 1 : i + 1])
        upper[i] = middle[i] + std_dev * std
        lower[i] = middle[i] - std_dev * std
    
    return middle, upper, lower


# ---------------------------------------------------------------------------
# Grid calculation functions
# ---------------------------------------------------------------------------

def calculate_grid_levels(
    upper: float, lower: float, grid_count: int
) -> np.ndarray:
    """Calculate equal-spaced grid levels between upper and lower bounds."""
    if upper <= lower or grid_count < 2:
        return np.array([])
    
    return np.linspace(lower, upper, grid_count)


def get_grid_position_change(
    prev_price: float,
    curr_price: float,
    grid_levels: np.ndarray,
) -> tuple[int, str]:
    """
    Calculate how many grid levels the price has crossed.
    
    Returns:
        (levels_crossed, direction)
        - levels_crossed: positive = moved up, negative = moved down
        - direction: 'up', 'down', or 'none'
    """
    if len(grid_levels) == 0:
        return 0, 'none'
    
    # Find which grid level range the prices are in
    prev_level = find_grid_level(prev_price, grid_levels)
    curr_level = find_grid_level(curr_price, grid_levels)
    
    levels_crossed = curr_level - prev_level
    
    if levels_crossed > 0:
        return levels_crossed, 'up'
    elif levels_crossed < 0:
        return -levels_crossed, 'down'
    else:
        return 0, 'none'


def find_grid_level(price: float, grid_levels: np.ndarray) -> int:
    """Find which grid level the price is currently at/above."""
    if len(grid_levels) == 0:
        return 0
    
    # Count how many grid levels are below the price
    return np.sum(grid_levels <= price)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class GridConfig:
    """Configuration for the Grid Trading strategy."""

    # Trading pairs
    symbols: List[str] = field(default_factory=list)

    # Timeframe
    timeframe: str = "1h"

    # Grid parameters
    grid_count: int = 20              # Number of grid levels
    atr_multiplier: float = 2.0       # ATR multiplier for dynamic bounds
    sma_period: int = 50              # SMA period for grid center
    atr_period: int = 14              # ATR period for dynamic bounds
    recalc_period: int = 24           # Recalculate grid every N bars (1h * 24 = daily)

    # Alternative: Bollinger Band mode
    use_bollinger: bool = False       # Use Bollinger Bands instead of ATR
    bb_period: int = 20               # Bollinger Band period
    bb_std_dev: float = 2.0           # Bollinger Band standard deviation

    # Position management
    position_size_pct: float = 0.05   # Position size per grid level (5%)
    max_position_pct: float = 0.80    # Maximum total position (80%)
    leverage: float = 5.0             # Leverage multiplier

    # Risk management
    stop_loss_pct: float = 0.05       # 5% hard stop loss
    grid_deviation_limit: float = 0.10  # Close all if price moves >10% beyond grid
    daily_loss_limit: float = 0.03    # 3% daily loss circuit breaker

    # Grid crossing thresholds
    entry_lines: int = 2              # Grid lines crossed to trigger entry
    profit_lines: int = 1             # Grid lines crossed to trigger take-profit

    # Grid strategy specific
    min_grid_spacing_pct: float = 0.002  # Minimum 0.2% spacing between grid levels
    grid_rebalance_threshold: float = 0.20  # Rebalance if price moves 20% from grid center