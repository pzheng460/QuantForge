"""
Strategy Registration System.

Each strategy registers a StrategyRegistration dataclass describing its
signal generator, config classes, grid parameters, heatmap config, and
conversion functions. The global registry enables the unified BacktestRunner
and CLI to work with any registered strategy.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from nexustrader.constants import KlineInterval


@dataclass
class HeatmapConfig:
    """Heatmap scanning configuration for a strategy."""

    x_param_name: str  # e.g. "zscore_entry", "fast_period", "bb_multiplier"
    y_param_name: str  # e.g. "hurst_window", "slow_period", "bb_period"
    x_range: Tuple[float, float]
    y_range: Tuple[float, float]
    x_label: str
    y_label: str
    third_param_choices: Dict[str, List]
    fixed_params: Dict[str, Any]
    filter_config_factory: Optional[Callable] = None


@dataclass
class LiveConfig:
    """Live trading configuration for the generic runner.

    When present on a StrategyRegistration, the generic runner
    (``strategy.runner``) can launch the strategy without a custom live.py.
    """

    core_cls: Type  # SignalCore class (e.g. EMASignalCore)
    update_columns: Tuple[
        str, ...
    ]  # columns to pass to core.update/update_indicators_only
    warmup_fn: Optional[Callable] = None  # (config) -> int warmup_period_bars
    use_dual_mode: bool = False  # if True, enables enable_live_mode() pattern
    pre_update_hook: Optional[Callable] = None  # (core, kline) -> dict of extra kwargs
    process_signal_fn: Optional[Callable] = None  # override _process_signal
    pre_signal_hook_fn: Optional[Callable] = None  # override _pre_signal_hook
    on_live_activated_fn: Optional[Callable] = None  # override _on_live_activated
    enable_stale_guard: bool = False
    max_kline_age_s: float = 120.0
    default_symbol: str = "BTCUSDT-PERP.BITGET"


@dataclass
class StrategyRegistration:
    """Complete registration for a backtestable strategy."""

    name: str  # e.g. "hurst_kalman"
    display_name: str  # e.g. "Hurst-Kalman"
    signal_generator_cls: Type  # class with generate(data, params) -> np.ndarray
    config_cls: Type  # strategy config dataclass (e.g. HurstKalmanConfig)
    filter_config_cls: Type  # filter config dataclass (e.g. TradeFilterConfig)
    default_grid: Dict[str, List]  # grid search parameter space
    heatmap_config: HeatmapConfig
    default_interval: KlineInterval = KlineInterval.MINUTE_15
    default_filter_kwargs: Dict[str, Any] = field(default_factory=dict)
    split_params_fn: Optional[Callable] = (
        None  # split mixed dict -> (config_kw, filter_kw)
    )
    mesa_dict_to_config_fn: Optional[Callable] = (
        None  # convert Mesa JSON -> StrategyConfig
    )
    export_config_fn: Optional[Callable] = None  # generate Python config code
    live_config: Optional[LiveConfig] = None  # generic runner configuration


# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------
_REGISTRY: Dict[str, StrategyRegistration] = {}


def register_strategy(registration: StrategyRegistration) -> None:
    """Register a strategy for use with the unified backtest framework."""
    _REGISTRY[registration.name] = registration


def get_strategy(name: str) -> StrategyRegistration:
    """Get a registered strategy by name.

    Raises:
        KeyError: If the strategy is not registered.
    """
    if name not in _REGISTRY:
        available = ", ".join(_REGISTRY.keys()) or "(none)"
        raise KeyError(f"Strategy '{name}' is not registered. Available: {available}")
    return _REGISTRY[name]


def list_strategies() -> List[str]:
    """Return names of all registered strategies."""
    return list(_REGISTRY.keys())
