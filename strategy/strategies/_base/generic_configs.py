"""
Generic config loader for any registered strategy.

Loads Mesa configs from heatmap_results.json using the strategy's
registered mesa_dict_to_config_fn. Replaces per-strategy configs.py
files for strategies using the generic runner.

Usage:
    from strategy.strategies._base.generic_configs import get_config, list_configs

    config = get_config("ema_crossover", mesa_index=0)
    strategy_config, filter_config = config.get_configs()
"""

from pathlib import Path
from typing import List

from strategy.backtest.config import StrategyConfig
from strategy.backtest.config import get_config as _get_config
from strategy.backtest.config import load_mesa_configs as _load_mesa_configs
from strategy.backtest.registry import get_strategy


def _results_path(strategy_name: str) -> Path:
    """Get the heatmap_results.json path for a strategy."""
    return Path(__file__).parent.parent / strategy_name / "heatmap_results.json"


def get_config(strategy_name: str, mesa_index: int = 0) -> StrategyConfig:
    """Get configuration for a specific Mesa (0-indexed, 0 = best).

    Args:
        strategy_name: Registered strategy name (e.g. "ema_crossover").
        mesa_index: Index into the Mesa list (sorted by Sharpe).

    Returns:
        StrategyConfig for the selected Mesa.

    Raises:
        KeyError: If the strategy is not registered.
        FileNotFoundError: If heatmap_results.json doesn't exist.
        ValueError: If mesa_index is out of range.
    """
    reg = get_strategy(strategy_name)
    if reg.mesa_dict_to_config_fn is None:
        raise ValueError(
            f"Strategy '{strategy_name}' does not have a mesa_dict_to_config_fn."
        )
    return _get_config(
        mesa_index, _results_path(strategy_name), reg.mesa_dict_to_config_fn
    )


def load_configs(strategy_name: str) -> List[StrategyConfig]:
    """Load all Mesa configurations for a strategy."""
    reg = get_strategy(strategy_name)
    if reg.mesa_dict_to_config_fn is None:
        raise ValueError(
            f"Strategy '{strategy_name}' does not have a mesa_dict_to_config_fn."
        )
    return _load_mesa_configs(_results_path(strategy_name), reg.mesa_dict_to_config_fn)


def list_configs(strategy_name: str) -> None:
    """Print a summary of all available Mesa configurations for a strategy."""
    reg = get_strategy(strategy_name)

    try:
        configs = load_configs(strategy_name)
    except FileNotFoundError:
        print(f"No heatmap_results.json found for '{strategy_name}'.")
        print(
            f"Generate with: uv run python -m strategy.backtest "
            f"-S {strategy_name} -X bitget --heatmap"
        )
        return

    print("=" * 80)
    print(f"{reg.display_name.upper()} MESA CONFIGURATIONS")
    print("=" * 80)
    print(f"\n{'#':<4} {'Name':<30} {'Sharpe':>8} {'Stability':>10} {'Freq':<25}")
    print("-" * 80)

    for cfg in configs:
        rec = " [BEST]" if cfg.recommended else ""
        print(
            f"{cfg.mesa_index:<4} {cfg.name:<30}{rec:<7} "
            f"{cfg.avg_sharpe:>8.2f} {cfg.stability:>10.2f} "
            f"{cfg.frequency_label:<25}"
        )

    print("\n" + "=" * 80)
    print(f"To use: get_config('{strategy_name}', 0) for best, etc.")
    print(
        f"Re-generate: uv run python -m strategy.backtest "
        f"-S {strategy_name} -X bitget --heatmap"
    )
    print("=" * 80)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        list_configs(sys.argv[1])
    else:
        print(
            "Usage: python -m strategy.strategies._base.generic_configs <strategy_name>"
        )
