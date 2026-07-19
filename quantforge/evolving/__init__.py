"""Evolving Mode — autonomous strategy auto-tune-and-deploy subsystem.

The master ON/OFF switch lives in :mod:`quantforge.evolving.switch`; its
public API is re-exported here so ``from quantforge import evolving;
evolving.is_enabled(...)`` keeps working. The pipeline stages
(news_collector → bot_preflight → auto_tune_scheduler →
deployment_pipeline → paper_shadow_runner → risk_control →
trading_control → audit_report → alerts) are sibling submodules.
"""

from quantforge.evolving.switch import (
    PINE_STRATEGIES_DIR,
    STATE_PATH,
    EvolvingState,
    UnknownStrategyError,
    add_strategy,
    disable,
    enable,
    is_enabled,
    known_strategy_names,
    load_state,
    remove_strategy,
    validate_strategy_name,
)

__all__ = [
    "PINE_STRATEGIES_DIR",
    "STATE_PATH",
    "EvolvingState",
    "UnknownStrategyError",
    "add_strategy",
    "disable",
    "enable",
    "is_enabled",
    "known_strategy_names",
    "load_state",
    "remove_strategy",
    "validate_strategy_name",
]
