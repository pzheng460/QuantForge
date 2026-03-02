"""
Core configuration for MA Convergence (均线密集) strategy.

Exchange-agnostic version: symbols default to empty list.

Strategy logic:
- Uses 6 MAs: SMA 20/60/120 and EMA 20/60/120
- Entry Method 1: MA Convergence Breakout
  - Detect tight convergence zone (max-min of 6 MAs) / ATR < convergence_threshold
  - Enter on confirmed breakout + pullback to prove direction
- Entry Method 2: First MA20 Retest (after divergence)
  - After MAs spread out, trade the first retest of MA20 that holds
- Exit via configurable method: fixed risk/reward, previous convergence zone,
  or Fibonacci extension
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class MAConvergenceConfig:
    """Configuration for the MA Convergence strategy."""

    # Trading pairs (exchange-agnostic: no default)
    symbols: List[str] = field(default_factory=list)

    # Timeframe
    timeframe: str = "1h"

    # MA periods (SMA and EMA share these periods)
    ma_period_1: int = 20  # short period (used for MA20 retest method)
    ma_period_2: int = 60  # medium period
    ma_period_3: int = 120  # long period

    # ATR for convergence measurement
    atr_period: int = 20

    # Convergence detection: (max_ma - min_ma) / ATR < threshold = convergence
    convergence_threshold: float = 1.5

    # Exit method: "risk_reward" | "prev_convergence" | "fibonacci"
    exit_method: str = "risk_reward"

    # Risk-reward ratio (exit_method="risk_reward")
    reward_ratio: float = 5.0

    # Fibonacci level (exit_method="fibonacci")
    fib_level: float = 1.618

    # Position risk per trade (20% of equity)
    position_size_pct: float = 0.20

    # Small buffer around stop loss (0.2%)
    stop_loss_buffer_pct: float = 0.002

    # Hard stop loss as pct from entry (fallback)
    stop_loss_pct: float = 0.05

    # Daily loss circuit breaker
    daily_loss_limit: float = 0.03
