"""
Unified Backtest Framework.

Provides exchange-agnostic backtesting for any registered strategy.

Usage:
    uv run python -m strategy.backtest -S hurst_kalman -X binance -p 1y --full
"""

from strategy.backtest.config import (
    BacktestResultRecord,
    BaseFilterConfig,
    StrategyConfig,
)
from strategy.backtest.exchange_profiles import (
    ExchangeProfile,
    get_profile,
    list_exchanges,
)
from strategy.backtest.registry import (
    HeatmapConfig,
    StrategyRegistration,
    get_strategy,
    list_strategies,
    register_strategy,
)
from strategy.backtest.runner import BacktestRunner

# Import strategy registrations to populate the registry
import strategy.strategies  # noqa: F401

__all__ = [
    "BacktestRunner",
    "BacktestResultRecord",
    "BaseFilterConfig",
    "ExchangeProfile",
    "HeatmapConfig",
    "StrategyConfig",
    "StrategyRegistration",
    "get_profile",
    "get_strategy",
    "list_exchanges",
    "list_strategies",
    "register_strategy",
]
