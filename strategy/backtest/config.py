"""
Unified Configuration Types.

Consolidates the identical StrategyConfig, BacktestResultRecord, and
BaseFilterConfig dataclasses that were duplicated across three strategy
configs.py files into a single source of truth.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class BaseFilterConfig:
    """Base configuration for trade filtering.

    Subclasses may add strategy-specific fields (e.g. only_mean_reversion).
    """

    min_holding_bars: int = 8
    cooldown_bars: int = 4
    signal_confirmation: int = 1


@dataclass
class BacktestResultRecord:
    """Backtest performance metrics for a specific time period."""

    period: str  # e.g. "6m", "1y"
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
    """Complete strategy configuration with Mesa-derived parameters.

    Generic across all strategies — the strategy_config and filter_config
    fields hold strategy-specific dataclass instances.
    """

    name: str
    description: str
    strategy_config: Any  # e.g. HurstKalmanConfig, EMAConfig, BBConfig
    filter_config: Any  # e.g. TradeFilterConfig, EMATradeFilterConfig
    backtest_results: List[BacktestResultRecord] = field(default_factory=list)
    recommended: bool = False
    notes: str = ""
    # Mesa metadata
    mesa_index: int = 0
    frequency_label: str = ""
    avg_sharpe: float = 0.0
    stability: float = 0.0

    def get_configs(self) -> Tuple[Any, Any]:
        """Return the strategy and filter configs as a tuple."""
        return self.strategy_config, self.filter_config

    def summary(self) -> str:
        """Return a summary of the configuration."""
        lines = [
            f"=== Mesa #{self.mesa_index}: {self.name} ===",
            f"Frequency: {self.frequency_label}",
            f"Description: {self.description}",
            f"Sharpe: {self.avg_sharpe:.2f}  Stability: {self.stability:.2f}",
            f"Recommended: {'Yes' if self.recommended else 'No'}",
        ]
        if self.notes:
            lines.extend(["", f"Notes: {self.notes}"])
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mesa config loading utilities
# ---------------------------------------------------------------------------


def load_mesa_configs(
    results_path: Path,
    mesa_dict_to_config_fn: Callable[[Dict, int], StrategyConfig],
) -> List[StrategyConfig]:
    """Load Mesa configurations from a heatmap_results.json file.

    Args:
        results_path: Path to heatmap_results.json.
        mesa_dict_to_config_fn: Strategy-specific function that converts
            a mesa dict + index into a StrategyConfig.

    Returns:
        List of StrategyConfig, sorted by Sharpe (best first).

    Raises:
        FileNotFoundError: If the results file doesn't exist.
        ValueError: If no Mesa regions found.
    """
    if not results_path.exists():
        raise FileNotFoundError(f"No heatmap_results.json found at {results_path}.")

    with open(results_path) as f:
        data = json.load(f)

    mesas = data.get("mesas", [])
    if not mesas:
        raise ValueError(
            "heatmap_results.json contains no Mesa regions. "
            "Re-run heatmap scan with different parameters."
        )

    return [mesa_dict_to_config_fn(m, i) for i, m in enumerate(mesas)]


def get_config(
    mesa_index: int,
    results_path: Path,
    mesa_dict_to_config_fn: Callable[[Dict, int], StrategyConfig],
) -> StrategyConfig:
    """Get configuration for a specific Mesa (0-indexed, 0 = best).

    Args:
        mesa_index: Index into the Mesa list (sorted by Sharpe).
        results_path: Path to heatmap_results.json.
        mesa_dict_to_config_fn: Conversion function.

    Returns:
        StrategyConfig for the selected Mesa.
    """
    configs = load_mesa_configs(results_path, mesa_dict_to_config_fn)
    if mesa_index < 0 or mesa_index >= len(configs):
        raise ValueError(
            f"Mesa index {mesa_index} out of range. Available: 0-{len(configs) - 1}"
        )
    return configs[mesa_index]
