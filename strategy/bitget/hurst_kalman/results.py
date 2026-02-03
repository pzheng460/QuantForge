"""
Backtest Results Storage System.

This module provides functionality to save and load backtest results,
and automatically integrate them with the configuration system.

Usage:
    from strategy.bitget.hurst_kalman.results import (
        save_backtest_result,
        load_all_results,
        get_best_config,
    )
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from strategy.bitget.hurst_kalman.core import HurstKalmanConfig


# Results storage file
RESULTS_FILE = Path(__file__).parent / "backtest_results.json"


@dataclass
class BacktestResultData:
    """Single backtest result data."""

    # Identification
    config_level: int  # 1-5
    config_name: str
    period: str  # "6_months", "1_year", "2_years"

    # Time range
    start_date: str
    end_date: str
    backtest_run_time: str  # When the backtest was run

    # Performance metrics
    total_return_pct: float
    win_rate_pct: float
    max_drawdown_pct: float
    total_trades: int
    sharpe_ratio: Optional[float] = None
    profit_factor: Optional[float] = None
    avg_bars_held: Optional[float] = None

    # Configuration used
    zscore_entry: float = 0.0
    mean_reversion_threshold: float = 0.0
    position_size_pct: float = 0.0
    stop_loss_pct: float = 0.0
    min_holding_bars: int = 0
    cooldown_bars: int = 0
    only_mean_reversion: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BacktestResultData":
        """Create from dictionary."""
        return cls(**data)


def save_backtest_result(result: BacktestResultData) -> None:
    """Save a single backtest result to the results file."""
    results = load_all_results()

    # Create unique key for this result
    key = f"{result.config_level}_{result.period}"

    # Update or add result
    results[key] = result.to_dict()

    # Save to file
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2)

    print(f"Saved result: Level {result.config_level} ({result.config_name}) - {result.period}")


def save_batch_results(results_list: List[BacktestResultData]) -> None:
    """Save multiple backtest results at once."""
    results = load_all_results()

    for result in results_list:
        key = f"{result.config_level}_{result.period}"
        results[key] = result.to_dict()

    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2)

    print(f"Saved {len(results_list)} results to {RESULTS_FILE}")


def load_all_results() -> Dict[str, Dict[str, Any]]:
    """Load all saved results from the results file."""
    if not RESULTS_FILE.exists():
        return {}

    with open(RESULTS_FILE, "r") as f:
        return json.load(f)


def get_results_for_level(level: int) -> List[BacktestResultData]:
    """Get all results for a specific config level."""
    results = load_all_results()
    level_results = []

    for key, data in results.items():
        if data.get("config_level") == level:
            level_results.append(BacktestResultData.from_dict(data))

    return level_results


def get_best_config_by_metric(
    metric: str = "total_return_pct",
    period: str = "2_years",
) -> Optional[int]:
    """Find the best config level by a specific metric.

    Args:
        metric: Metric to optimize ("total_return_pct", "sharpe_ratio", "win_rate_pct")
        period: Time period to use for comparison

    Returns:
        Best config level (1-5) or None if no results
    """
    results = load_all_results()

    best_level = None
    best_value = float("-inf")

    for key, data in results.items():
        if data.get("period") == period:
            value = data.get(metric, 0)
            if value is not None and value > best_value:
                best_value = value
                best_level = data.get("config_level")

    return best_level


def print_results_summary() -> None:
    """Print a summary of all saved results."""
    results = load_all_results()

    if not results:
        print("No backtest results saved yet.")
        print(f"Run backtests and results will be saved to: {RESULTS_FILE}")
        return

    print("=" * 80)
    print("SAVED BACKTEST RESULTS")
    print("=" * 80)

    # Group by level
    by_level: Dict[int, List[Dict]] = {}
    for key, data in results.items():
        level = data.get("config_level", 0)
        if level not in by_level:
            by_level[level] = []
        by_level[level].append(data)

    for level in sorted(by_level.keys()):
        level_data = by_level[level]
        name = level_data[0].get("config_name", "Unknown")
        print(f"\n[{level}] {name}")
        print("-" * 40)

        for data in sorted(level_data, key=lambda x: x.get("period", "")):
            period = data.get("period", "unknown")
            ret = data.get("total_return_pct", 0)
            win = data.get("win_rate_pct", 0)
            dd = data.get("max_drawdown_pct", 0)
            trades = data.get("total_trades", 0)
            run_time = data.get("backtest_run_time", "unknown")

            print(
                f"  {period:>10}: {ret:+7.1f}% return, "
                f"{win:5.1f}% win, {dd:6.1f}% DD, {trades:3d} trades"
            )
            print(f"              (run: {run_time})")

    print("\n" + "=" * 80)


def create_result_from_backtest(
    config_level: int,
    config_name: str,
    strategy_config: HurstKalmanConfig,
    filter_config: Any,  # TradeFilterConfig
    period: str,
    start_date: str,
    end_date: str,
    total_return: float,
    win_rate: float,
    max_drawdown: float,
    total_trades: int,
    sharpe_ratio: Optional[float] = None,
    avg_bars_held: Optional[float] = None,
) -> BacktestResultData:
    """Helper to create BacktestResultData from backtest output."""
    return BacktestResultData(
        config_level=config_level,
        config_name=config_name,
        period=period,
        start_date=start_date,
        end_date=end_date,
        backtest_run_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total_return_pct=total_return * 100,
        win_rate_pct=win_rate * 100,
        max_drawdown_pct=max_drawdown * 100,
        total_trades=total_trades,
        sharpe_ratio=sharpe_ratio,
        avg_bars_held=avg_bars_held,
        zscore_entry=strategy_config.zscore_entry,
        mean_reversion_threshold=strategy_config.mean_reversion_threshold,
        position_size_pct=strategy_config.position_size_pct,
        stop_loss_pct=strategy_config.stop_loss_pct,
        min_holding_bars=filter_config.min_holding_bars,
        cooldown_bars=filter_config.cooldown_bars,
        only_mean_reversion=filter_config.only_mean_reversion,
    )


if __name__ == "__main__":
    print_results_summary()
