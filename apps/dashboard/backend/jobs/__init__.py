"""Background job management for backtest/optimize tasks.

Facade over the submodules so routers and callers keep importing from
``apps.dashboard.backend.jobs``. Tests that monkeypatch internals
(e.g. ``_fetch_ohlcv``) must patch the submodule that *uses* the name
(``jobs.backtest`` / ``jobs.optimize``), not this package.
"""

from apps.dashboard.backend.jobs.backtest import run_backtest_job, _run_pine_backtest
from apps.dashboard.backend.jobs.data import (
    _DEFAULT_SYMBOLS,
    _PERIOD_DAYS,
    _STRATEGIES_DIR,
    _apply_config_override,
    _fetch_ohlcv,
    _ohlcv_to_bars,
    _resolve_date_range,
    _resolve_pine_source,
)
from apps.dashboard.backend.jobs.optimize import (
    _run_pine_optimize,
    _run_three_stage,
    _run_wfo,
    run_optimize_job,
)
from apps.dashboard.backend.jobs.registry import (
    JobCancelled,
    cancel_job,
    check_cancelled,
    create_job,
    get_job,
    _jobs,
)

__all__ = [
    "JobCancelled",
    "cancel_job",
    "check_cancelled",
    "create_job",
    "get_job",
    "run_backtest_job",
    "run_optimize_job",
    # compat facade for tests and internal callers
    "_DEFAULT_SYMBOLS",
    "_PERIOD_DAYS",
    "_STRATEGIES_DIR",
    "_apply_config_override",
    "_fetch_ohlcv",
    "_jobs",
    "_ohlcv_to_bars",
    "_resolve_date_range",
    "_resolve_pine_source",
    "_run_pine_backtest",
    "_run_pine_optimize",
    "_run_three_stage",
    "_run_wfo",
]
