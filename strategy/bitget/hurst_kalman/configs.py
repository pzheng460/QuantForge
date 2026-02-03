"""
Hurst-Kalman Strategy Configurations using Universal Quant Stratification Standard (UQSS).

This module implements the UQSS tiering system for the Hurst-Kalman mean reversion strategy.
The 5 levels (L1-L5) represent different time horizons and alpha profiles, not just
risk levels. This makes the tiering system reusable across different strategy types.

UQSS Level Mapping for Hurst-Kalman:
------------------------------------
L1 (Macro):    Long windows, high Z-score thresholds, weekly/monthly cycles
L2 (Swing):    Standard windows, moderate thresholds, multi-day swings (RECOMMENDED)
L3 (Intraday): Short windows, lower thresholds, intraday mean reversion
L4 (Scalp):    Very short windows, low thresholds, minute-level scalping
L5 (Sniper):   Extreme Z-score triggers, event-based entries (wicks, crashes)

Usage:
    from strategy.bitget.hurst_kalman.configs import get_config, CONFIG_BY_LEVEL

    # Get recommended config (L2 Swing)
    config = get_config(level=2)
    hk_config, filter_config = config.get_configs()

    # Or by level enum
    from nexustrader.strategy import StrategyLevel
    config = get_config_by_level(StrategyLevel.L2_SWING)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from nexustrader.strategy_config.config_schema import (
    LEVEL_DESCRIPTIONS,
    StrategyLevel,
    UniversalConfig,
)

from strategy.bitget.hurst_kalman.core import HurstKalmanConfig


@dataclass
class TradeFilterConfig:
    """Configuration for trade filtering."""

    min_holding_bars: int = 8
    cooldown_bars: int = 4
    signal_confirmation: int = 1
    only_mean_reversion: bool = True


@dataclass
class BacktestResult:
    """Backtest performance metrics for a specific time period."""

    period: str  # e.g., "6_months", "1_year", "2_years"
    start_date: str
    end_date: str
    total_return_pct: float
    win_rate_pct: float
    max_drawdown_pct: float
    total_trades: int
    sharpe_ratio: Optional[float] = None
    profit_factor: Optional[float] = None


@dataclass
class StrategyConfig:
    """Complete strategy configuration with UQSS level and backtest results."""

    level: StrategyLevel
    name: str
    description: str
    strategy_config: HurstKalmanConfig
    filter_config: TradeFilterConfig
    backtest_results: List[BacktestResult] = field(default_factory=list)
    recommended: bool = False
    notes: str = ""

    def get_configs(self) -> Tuple[HurstKalmanConfig, TradeFilterConfig]:
        """Return the strategy and filter configs as a tuple."""
        return self.strategy_config, self.filter_config

    def get_best_result(self) -> Optional[BacktestResult]:
        """Return the longest backtest result (most reliable)."""
        if not self.backtest_results:
            return None
        # Sort by period: 2_years > 1_year > 6_months > 3_months
        period_order = {"2_years": 4, "1_year": 3, "6_months": 2, "3_months": 1}
        return max(
            self.backtest_results,
            key=lambda x: period_order.get(x.period, 0),
        )

    def get_level_info(self) -> Dict[str, str]:
        """Get UQSS level information."""
        return LEVEL_DESCRIPTIONS.get(self.level, {})

    def summary(self) -> str:
        """Return a summary of the configuration and its performance."""
        level_info = self.get_level_info()
        lines = [
            f"=== [{self.level.name}] {self.name} ===",
            f"UQSS Tier: {level_info.get('codename', 'N/A')} ({level_info.get('chinese', '')})",
            f"Description: {self.description}",
            f"Holding Period: {level_info.get('holding_period', 'N/A')}",
            f"Recommended: {'Yes' if self.recommended else 'No'}",
            "",
            "Hurst-Kalman Parameters:",
            f"  hurst_window: {self.strategy_config.hurst_window}",
            f"  zscore_entry: {self.strategy_config.zscore_entry}",
            f"  mean_reversion_threshold: {self.strategy_config.mean_reversion_threshold}",
            f"  position_size: {self.strategy_config.position_size_pct * 100}%",
            f"  stop_loss: {self.strategy_config.stop_loss_pct * 100}%",
            "",
            "Filter Parameters:",
            f"  min_holding_bars: {self.filter_config.min_holding_bars}",
            f"  cooldown_bars: {self.filter_config.cooldown_bars}",
            f"  only_mean_reversion: {self.filter_config.only_mean_reversion}",
            "",
            "Backtest Results:",
        ]
        for result in self.backtest_results:
            lines.append(
                f"  {result.period}: {result.total_return_pct:+.1f}% return, "
                f"{result.win_rate_pct:.1f}% win rate, "
                f"{result.max_drawdown_pct:.1f}% max DD, "
                f"{result.total_trades} trades"
            )
        if self.notes:
            lines.extend(["", f"Notes: {self.notes}"])
        return "\n".join(lines)

    def to_universal_config(self) -> UniversalConfig:
        """Convert to universal config format."""
        return UniversalConfig(
            level=self.level,
            name=self.name,
            description=self.description,
            timeframe="15m",  # Hurst-Kalman uses 15-min bars
            risk_per_trade=self.strategy_config.position_size_pct,
            max_holding_bars=self.filter_config.min_holding_bars * 10,
            params={
                "hurst_window": self.strategy_config.hurst_window,
                "zscore_window": self.strategy_config.zscore_window,
                "zscore_entry": self.strategy_config.zscore_entry,
                "mean_reversion_threshold": self.strategy_config.mean_reversion_threshold,
                "trend_threshold": self.strategy_config.trend_threshold,
                "kalman_R": self.strategy_config.kalman_R,
                "kalman_Q": self.strategy_config.kalman_Q,
                "stop_loss_pct": self.strategy_config.stop_loss_pct,
            },
            recommended=self.recommended,
            notes=self.notes,
        )


# =============================================================================
# UQSS CONFIGURATIONS FOR HURST-KALMAN
# =============================================================================

L1_MACRO_CONFIG = StrategyConfig(
    level=StrategyLevel.L1_MACRO,
    name="Macro",
    description="Long-cycle structural plays. Very high Z-score threshold captures only "
    "extreme deviations at historical tops/bottoms. Maximum capacity, minimum frequency.",
    strategy_config=HurstKalmanConfig(
        symbols=["BTCUSDT-PERP.BITGET"],
        hurst_window=200,  # ~3 days on 15m bars, captures weekly structure
        zscore_window=100,
        zscore_entry=3.5,  # Very high threshold - only extreme deviations
        mean_reversion_threshold=0.35,  # Strong mean reversion required
        trend_threshold=0.65,
        kalman_R=0.2,
        kalman_Q=5e-05,
        position_size_pct=0.08,  # Conservative sizing
        stop_loss_pct=0.02,
        daily_loss_limit=0.02,
    ),
    filter_config=TradeFilterConfig(
        min_holding_bars=24,  # 6 hours minimum
        cooldown_bars=16,  # 4 hours between trades
        signal_confirmation=2,  # Require 2 bars of confirmation
        only_mean_reversion=True,
    ),
    backtest_results=[],
    notes="Lowest frequency, highest quality signals. Best for large capital base positions.",
)


L2_SWING_CONFIG = StrategyConfig(
    level=StrategyLevel.L2_SWING,
    name="Swing",
    description="Standard multi-day swing trading. Balanced frequency and win rate. "
    "Captures 2-10 day mean reversion opportunities. The comfort zone for most traders.",
    recommended=True,
    strategy_config=HurstKalmanConfig(
        symbols=["BTCUSDT-PERP.BITGET"],
        hurst_window=100,  # ~1.5 days on 15m bars
        zscore_window=60,
        zscore_entry=3.0,  # Moderate threshold
        mean_reversion_threshold=0.40,
        trend_threshold=0.60,
        kalman_R=0.2,
        kalman_Q=5e-05,
        position_size_pct=0.10,
        stop_loss_pct=0.03,
        daily_loss_limit=0.03,
    ),
    filter_config=TradeFilterConfig(
        min_holding_bars=8,  # 2 hours minimum
        cooldown_bars=4,  # 1 hour between trades
        signal_confirmation=1,
        only_mean_reversion=True,
    ),
    backtest_results=[],
    notes="RECOMMENDED for most traders. Best balance of frequency and reliability.",
)


L3_INTRADAY_CONFIG = StrategyConfig(
    level=StrategyLevel.L3_INTRADAY,
    name="Intraday",
    description="Intraday mean reversion. Captures 4-24 hour sentiment swings. "
    "Lower thresholds for more frequent signals. Usually closes before funding settlement.",
    strategy_config=HurstKalmanConfig(
        symbols=["BTCUSDT-PERP.BITGET"],
        hurst_window=48,  # ~12 hours on 15m bars
        zscore_window=40,
        zscore_entry=2.5,  # Lower threshold for more signals
        mean_reversion_threshold=0.42,
        trend_threshold=0.58,
        kalman_R=0.2,
        kalman_Q=5e-05,
        position_size_pct=0.12,
        stop_loss_pct=0.04,
        daily_loss_limit=0.04,
    ),
    filter_config=TradeFilterConfig(
        min_holding_bars=4,  # 1 hour minimum
        cooldown_bars=2,  # 30 min between trades
        signal_confirmation=1,
        only_mean_reversion=True,
    ),
    backtest_results=[],
    notes="Active intraday trading. Requires >55% win rate to overcome fees.",
)


L4_SCALP_CONFIG = StrategyConfig(
    level=StrategyLevel.L4_SCALP,
    name="Scalp",
    description="High-frequency scalping. Very short windows and low thresholds. "
    "Captures minute-level microstructure imbalances. Requires low fees to be profitable.",
    strategy_config=HurstKalmanConfig(
        symbols=["BTCUSDT-PERP.BITGET"],
        hurst_window=24,  # ~6 hours on 15m bars
        zscore_window=20,
        zscore_entry=2.0,  # Low threshold for frequent signals
        mean_reversion_threshold=0.45,
        trend_threshold=0.55,
        kalman_R=0.2,
        kalman_Q=5e-05,
        position_size_pct=0.15,
        stop_loss_pct=0.05,
        daily_loss_limit=0.05,
    ),
    filter_config=TradeFilterConfig(
        min_holding_bars=2,  # 30 min minimum
        cooldown_bars=1,  # 15 min between trades
        signal_confirmation=1,
        only_mean_reversion=False,  # Trade in all regimes
    ),
    backtest_results=[],
    notes="High frequency, high fee sensitivity. Only for low-fee/rebate accounts.",
)


L5_SNIPER_CONFIG = StrategyConfig(
    level=StrategyLevel.L5_SNIPER,
    name="Sniper",
    description="Event-driven extreme entries. Very high Z-score threshold triggers only "
    "on market crashes or extreme wicks. Designed to 'catch falling knives' at key levels.",
    strategy_config=HurstKalmanConfig(
        symbols=["BTCUSDT-PERP.BITGET"],
        hurst_window=100,
        zscore_window=60,
        zscore_entry=4.5,  # Extreme threshold - only major dislocations
        mean_reversion_threshold=0.30,  # Strong mean reversion confirmation
        trend_threshold=0.70,
        kalman_R=0.2,
        kalman_Q=5e-05,
        position_size_pct=0.15,  # Larger size for high-conviction trades
        stop_loss_pct=0.03,
        daily_loss_limit=0.05,
    ),
    filter_config=TradeFilterConfig(
        min_holding_bars=4,  # 1 hour minimum
        cooldown_bars=8,  # 2 hours between trades
        signal_confirmation=1,
        only_mean_reversion=True,
    ),
    backtest_results=[],
    notes="Black swan protection. Triggers rarely but with high conviction. "
    "Can use limit orders at extreme levels.",
)


# =============================================================================
# CONFIGURATION REGISTRY
# =============================================================================

# Ordered by UQSS level (L1 = longest horizon, L5 = event-driven)
CONFIG_BY_LEVEL: Dict[int, StrategyConfig] = {
    1: L1_MACRO_CONFIG,
    2: L2_SWING_CONFIG,
    3: L3_INTRADAY_CONFIG,
    4: L4_SCALP_CONFIG,
    5: L5_SNIPER_CONFIG,
}

CONFIG_BY_ENUM: Dict[StrategyLevel, StrategyConfig] = {
    StrategyLevel.L1_MACRO: L1_MACRO_CONFIG,
    StrategyLevel.L2_SWING: L2_SWING_CONFIG,
    StrategyLevel.L3_INTRADAY: L3_INTRADAY_CONFIG,
    StrategyLevel.L4_SCALP: L4_SCALP_CONFIG,
    StrategyLevel.L5_SNIPER: L5_SNIPER_CONFIG,
}

# Name-based lookup (for backward compatibility)
ALL_CONFIGS: Dict[str, StrategyConfig] = {
    "macro": L1_MACRO_CONFIG,
    "swing": L2_SWING_CONFIG,
    "intraday": L3_INTRADAY_CONFIG,
    "scalp": L4_SCALP_CONFIG,
    "sniper": L5_SNIPER_CONFIG,
    # Legacy names for backward compatibility
    "ultra_conservative": L1_MACRO_CONFIG,
    "conservative": L2_SWING_CONFIG,
    "moderate": L3_INTRADAY_CONFIG,
    "aggressive": L4_SCALP_CONFIG,
    "short_term_balanced": L5_SNIPER_CONFIG,
}


def get_config(level: int) -> StrategyConfig:
    """Get a configuration by UQSS level number (1-5).

    Args:
        level: UQSS level from 1 (Macro) to 5 (Sniper)

    Returns:
        StrategyConfig for the selected level
    """
    if level not in CONFIG_BY_LEVEL:
        raise ValueError(f"Invalid level {level}. Must be 1-5.")
    return CONFIG_BY_LEVEL[level]


def get_config_by_level(level: StrategyLevel) -> StrategyConfig:
    """Get a configuration by StrategyLevel enum."""
    if level not in CONFIG_BY_ENUM:
        raise ValueError(f"Invalid level {level}.")
    return CONFIG_BY_ENUM[level]


def get_config_by_name(name: str) -> StrategyConfig:
    """Get a configuration by name."""
    name_lower = name.lower().replace("-", "_").replace(" ", "_")
    if name_lower not in ALL_CONFIGS:
        available = ", ".join(
            k for k in ALL_CONFIGS.keys() if k not in ["ultra_conservative", "conservative", "moderate", "aggressive", "short_term_balanced"]
        )
        raise ValueError(f"Unknown config '{name}'. Available: {available}")
    return ALL_CONFIGS[name_lower]


def get_recommended_config() -> StrategyConfig:
    """Get the recommended configuration (L2 Swing)."""
    return L2_SWING_CONFIG


def list_all_configs() -> None:
    """Print a summary of all available configurations."""
    print("=" * 80)
    print("HURST-KALMAN CONFIGURATIONS (UQSS Tiering)")
    print("=" * 80)
    print("\nLevel  Codename           Holding Period    Z-Score  Hurst    Status")
    print("-" * 80)

    for level, config in CONFIG_BY_LEVEL.items():
        level_info = config.get_level_info()
        rec = " [RECOMMENDED]" if config.recommended else ""
        holding = level_info.get("holding_period", "N/A")

        print(
            f"L{level}     {config.name:<18} {holding:<17} "
            f"{config.strategy_config.zscore_entry:<8} "
            f"{config.strategy_config.mean_reversion_threshold:<8}{rec}"
        )

    print("\n" + "=" * 80)
    print("\nUQSS (Universal Quant Stratification Standard):")
    print("  L1 = Macro/Structural  : Weeks-Months, captures major regime shifts")
    print("  L2 = Swing             : 2-10 Days, standard technical analysis")
    print("  L3 = Intraday          : 4-24 Hours, daily sentiment swings")
    print("  L4 = Scalp/Burst       : Minutes-Hours, microstructure trading")
    print("  L5 = Event/Sniper      : Condition-triggered, extreme events")
    print("\n" + "=" * 80)


if __name__ == "__main__":
    list_all_configs()
    print("\n")
    # Show recommended config details
    recommended = get_recommended_config()
    print(recommended.summary())
