"""
Universal Quant Stratification Standard (UQSS).

A universal tiering system for quantitative strategies that abstracts away from
specific indicator names (like Z-Score or RSI) to enable reuse across different
strategy types: mean-reversion, trend-following, arbitrage, or ML-based.

The classification is based on "Alpha Profile" rather than indicator values:
- Time Horizon: How long positions are typically held
- Signal Specificity: How selective the entry criteria are
- Risk Profile: Expected win rate and risk/reward characteristics

Usage:
    from nexustrader.strategy.config_schema import StrategyLevel, UniversalConfig

    # Define a strategy-specific config
    config = UniversalConfig(
        level=StrategyLevel.L2_SWING,
        name="Hurst-Kalman Swing",
        description="Swing trading based on Hurst exponent and Kalman filter",
        timeframe="4h",
        risk_per_trade=0.02,
        max_holding_bars=100,
        params={"z_entry": 2.5, "hurst_max": 0.45, "window": 100}
    )
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class StrategyLevel(Enum):
    """
    Universal Strategy Tier Levels (UQSS).

    Classification based on time horizon and alpha profile, not specific indicators.

    L1 - Macro/Structural: Weekly/Monthly, captures major market regime shifts
    L2 - Swing: 2-10 days, standard technical analysis comfort zone
    L3 - Intraday: 4-24 hours, exploits daily sentiment and funding cycles
    L4 - Scalp/Burst: Minutes to hours, requires low latency and low fees
    L5 - Event/Sniper: Condition-triggered, not time-series based
    """

    L1_MACRO = 1  # Structural / Long-term (weeks/months)
    L2_SWING = 2  # Standard Swing (2-10 days)
    L3_INTRADAY = 3  # Day Trading (4-24 hours)
    L4_SCALP = 4  # High Frequency / Scalping (minutes/hours)
    L5_SNIPER = 5  # Event Driven / Extreme Conditions


# Human-readable level descriptions
LEVEL_DESCRIPTIONS: Dict[StrategyLevel, Dict[str, str]] = {
    StrategyLevel.L1_MACRO: {
        "codename": "Macro / Structural",
        "chinese": "宏观/结构型",
        "definition": "Captures major cycle deviations. Filters all short-term noise, "
        "responds only to significant market structure changes. Maximum capacity, minimum frequency.",
        "holding_period": "Weeks to Months",
        "win_rate_profile": "Low win rate / Extreme risk-reward (Trend) or Very high win rate / Very low frequency (Mean Rev)",
        "use_case": "Long-term allocation, large capital base positions",
    },
    StrategyLevel.L2_SWING: {
        "codename": "Swing",
        "chinese": "波段型",
        "definition": "Captures multi-day trends or reversions. Standard 'main wave' or "
        "'weekly-level pullback'. The comfort zone for most technical analysis strategies.",
        "holding_period": "2-10 days",
        "win_rate_profile": "Balanced (40-55% win rate)",
        "use_case": "Retail main allocation, balancing life and trading",
    },
    StrategyLevel.L3_INTRADAY: {
        "codename": "Day / Intraday",
        "chinese": "日内型",
        "definition": "Captures intraday sentiment swings. Exploits major market open/close effects "
        "or intraday fund flows. Usually avoids overnight positions (or major funding settlement times).",
        "holding_period": "4-24 hours",
        "win_rate_profile": "Requires higher win rate (>55%)",
        "use_case": "Active traders, overnight risk avoidance",
    },
    StrategyLevel.L4_SCALP: {
        "codename": "Scalp / Burst",
        "chinese": "剥头皮/爆发型",
        "definition": "Captures microstructure imbalances. Extremely sensitive to order flow, "
        "instant momentum, or small spreads. Heavily dependent on low latency and low fees.",
        "holding_period": "Minutes to Hours",
        "win_rate_profile": "Very high win rate required (>60%)",
        "use_case": "High-frequency trading, rebate accounts, automated market making",
    },
    StrategyLevel.L5_SNIPER: {
        "codename": "Event / Sniper",
        "chinese": "事件/狙击型",
        "definition": "Specific condition triggered. Not based on continuous time series, "
        "but on specific events (crash circuit breakers, wicks, funding arbitrage opportunities).",
        "holding_period": "Variable (instant)",
        "win_rate_profile": "Extremely high confidence",
        "use_case": "Black swan protection, extreme market arbitrage",
    },
}


@dataclass
class UniversalConfig:
    """
    Universal configuration container for any quantitative strategy.

    This class provides a strategy-agnostic way to define configurations that can
    be used across different strategy implementations (Hurst-Kalman, SuperTrend, etc.)

    Attributes:
        level: The UQSS tier level (L1-L5)
        name: Human-readable name for the configuration
        description: Detailed description of the configuration's purpose
        timeframe: Primary trading timeframe (e.g., '1d', '4h', '15m')
        risk_per_trade: Risk per trade as decimal (e.g., 0.02 for 2%)
        max_holding_bars: Maximum bars to hold a position
        params: Strategy-specific parameters as a flexible dictionary
        recommended: Whether this is the recommended configuration
        notes: Additional notes about this configuration
    """

    level: StrategyLevel
    name: str
    description: str

    # Generic properties applicable to all strategies
    timeframe: str
    risk_per_trade: float = 0.02
    max_holding_bars: int = 1000

    # Strategy-specific parameters (flexible dictionary)
    # For Hurst-Kalman: {'z_entry': 3.0, 'hurst_max': 0.45, 'window': 100}
    # For SuperTrend:   {'atr_period': 20, 'factor': 3.0}
    # For ML-based:     {'model_path': '...', 'confidence_threshold': 0.8}
    params: Dict[str, Any] = field(default_factory=dict)

    # Metadata
    recommended: bool = False
    notes: str = ""

    def get_level_info(self) -> Dict[str, str]:
        """Get human-readable information about this level."""
        return LEVEL_DESCRIPTIONS.get(self.level, {})

    def summary(self) -> str:
        """Return a summary of this configuration."""
        info = self.get_level_info()
        lines = [
            f"=== {self.name} ({self.level.name}) ===",
            f"Codename: {info.get('codename', 'N/A')} ({info.get('chinese', '')})",
            f"Description: {self.description}",
            f"",
            f"Time Profile:",
            f"  Timeframe: {self.timeframe}",
            f"  Holding Period: {info.get('holding_period', 'N/A')}",
            f"  Max Holding Bars: {self.max_holding_bars}",
            f"",
            f"Risk Profile:",
            f"  Risk Per Trade: {self.risk_per_trade * 100:.1f}%",
            f"  Win Rate Profile: {info.get('win_rate_profile', 'N/A')}",
            f"",
            f"Strategy Parameters:",
        ]
        for key, value in self.params.items():
            lines.append(f"  {key}: {value}")

        if self.notes:
            lines.extend(["", f"Notes: {self.notes}"])

        if self.recommended:
            lines.extend(["", "*** RECOMMENDED ***"])

        return "\n".join(lines)


def get_level_name(level: StrategyLevel) -> str:
    """Get the codename for a strategy level."""
    info = LEVEL_DESCRIPTIONS.get(level, {})
    return info.get("codename", level.name)


def get_level_description(level: StrategyLevel) -> str:
    """Get the full description for a strategy level."""
    info = LEVEL_DESCRIPTIONS.get(level, {})
    return info.get("definition", "")


def list_all_levels() -> None:
    """Print information about all strategy levels."""
    print("=" * 80)
    print("UNIVERSAL QUANT STRATIFICATION STANDARD (UQSS)")
    print("=" * 80)

    for level in StrategyLevel:
        info = LEVEL_DESCRIPTIONS.get(level, {})
        print(f"\n[{level.name}] {info.get('codename', 'N/A')} ({info.get('chinese', '')})")
        print(f"  Definition: {info.get('definition', 'N/A')}")
        print(f"  Holding Period: {info.get('holding_period', 'N/A')}")
        print(f"  Win Rate Profile: {info.get('win_rate_profile', 'N/A')}")
        print(f"  Use Case: {info.get('use_case', 'N/A')}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    list_all_levels()
