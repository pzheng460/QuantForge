"""
Strategy configuration module for NexusTrader.

Contains Universal Quant Stratification Standard (UQSS) schema and utilities.
"""

from nexustrader.strategy_config.config_schema import (
    LEVEL_DESCRIPTIONS,
    StrategyLevel,
    UniversalConfig,
    get_level_description,
    get_level_name,
    list_all_levels,
)

__all__ = [
    "StrategyLevel",
    "UniversalConfig",
    "LEVEL_DESCRIPTIONS",
    "get_level_name",
    "get_level_description",
    "list_all_levels",
]
