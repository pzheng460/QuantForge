"""Shadow-mode comparison for promoted and candidate strategy versions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from quantforge.evolving.deployment import (
    DeploymentError,
    DeploymentRegistry,
    DeploymentStatus,
    StrategyVersion,
)

MetricEvaluator = Callable[[Path, str, str, str, str, str], dict[str, Any]]


def compare_metrics(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    min_trades: int = 20,
    min_profit_factor_delta: float = 0.0,
    max_drawdown_increase: float = 0.02,
) -> dict[str, Any]:
    """Compare baseline and candidate metrics with conservative shadow gates."""
    deltas = {
        "profit_factor": _num(candidate, "profit_factor")
        - _num(baseline, "profit_factor"),
        "max_drawdown": _num(candidate, "max_drawdown")
        - _num(baseline, "max_drawdown"),
        "n_trades": _num(candidate, "n_trades") - _num(baseline, "n_trades"),
        "return_pct": _num(candidate, "return_pct") - _num(baseline, "return_pct"),
        "sharpe": _num(candidate, "sharpe") - _num(baseline, "sharpe"),
    }

    reasons: list[str] = []
    if _num(candidate, "n_trades") < min_trades:
        reasons.append("trade_count")
    if deltas["profit_factor"] < min_profit_factor_delta:
        reasons.append("profit_factor")
    if deltas["max_drawdown"] > max_drawdown_increase:
        reasons.append("max_drawdown")

    return {
        "passed": not reasons,
        "reasons": reasons,
        "baseline": baseline,
        "candidate": candidate,
        "deltas": deltas,
        "gates": {
            "min_trades": min_trades,
            "min_profit_factor_delta": min_profit_factor_delta,
            "max_drawdown_increase": max_drawdown_increase,
        },
    }


def run_shadow_comparison(
    strategy_id: str,
    *,
    registry_path: str | Path | None = None,
    start: str,
    end: str,
    symbol: str = "BTC/USDT:USDT",
    exchange: str = "bitget",
    timeframe: str = "1h",
    out: str | Path | None = None,
    evaluator: MetricEvaluator | None = None,
) -> dict[str, Any]:
    """Evaluate the promoted version and latest shadow candidate on one window."""
    registry = DeploymentRegistry(registry_path)
    baseline = registry.current(strategy_id)
    candidate = _latest_shadow_candidate(
        registry, strategy_id, exclude_version_id=baseline.version_id
    )
    evaluate = evaluator or _default_evaluator()

    baseline_metrics = evaluate(
        Path(baseline.pine_path), symbol, exchange, timeframe, start, end
    )
    candidate_metrics = evaluate(
        Path(candidate.pine_path), symbol, exchange, timeframe, start, end
    )
    comparison = compare_metrics(baseline_metrics, candidate_metrics)
    report = {
        "strategy_id": strategy_id,
        "baseline_version_id": baseline.version_id,
        "candidate_version_id": candidate.version_id,
        "baseline_path": baseline.pine_path,
        "candidate_path": candidate.pine_path,
        "window": {
            "start": start,
            "end": end,
            "symbol": symbol,
            "exchange": exchange,
            "timeframe": timeframe,
        },
        **comparison,
    }
    if out:
        output = Path(out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True))
    return report


def _latest_shadow_candidate(
    registry: DeploymentRegistry,
    strategy_id: str,
    *,
    exclude_version_id: str,
) -> StrategyVersion:
    candidates = [
        version
        for version in registry.list(strategy_id)
        if version.status == DeploymentStatus.SHADOW
        and version.version_id != exclude_version_id
    ]
    if not candidates:
        raise DeploymentError(f"no shadow candidate for {strategy_id}")
    return candidates[-1]


def _default_evaluator() -> MetricEvaluator:
    from eval.optimizer_ab.holdout_eval import evaluate

    return evaluate


def _num(metrics: dict[str, Any], key: str) -> float:
    value = metrics.get(key, 0.0)
    if value is None:
        return 0.0
    return float(value)
