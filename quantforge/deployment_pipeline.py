"""Automated deployment pipeline for candidate strategy artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quantforge.deployment import DeploymentRegistry, DeploymentStatus
from quantforge.paper_ledger import PaperLedger
from quantforge.shadow import MetricEvaluator, run_shadow_comparison


def run_promotion_pipeline(
    strategy_id: str,
    *,
    candidate_pine: str | Path,
    evidence_path: str | Path,
    registry_path: str | Path | None = None,
    start: str,
    end: str,
    symbol: str = "BTC/USDT:USDT",
    exchange: str = "bitget",
    timeframe: str = "1h",
    source: str = "auto_tune",
    shadow_report_path: str | Path | None = None,
    report_path: str | Path | None = None,
    ledger_path: str | Path | None = None,
    min_runtime_fills: int = 2,
    min_runtime_pnl_delta: float = 0.0,
    max_runtime_drawdown_delta: float = 0.02,
    evaluator: MetricEvaluator | None = None,
) -> dict[str, Any]:
    """Register a candidate, run paper/shadow gates, and promote only on pass."""
    registry = DeploymentRegistry(registry_path)
    candidate = registry.register_candidate(
        strategy_id=strategy_id,
        pine_path=candidate_pine,
        evidence_path=evidence_path,
        source=source,
    )
    paper = registry.transition(candidate.version_id, DeploymentStatus.PAPER)
    shadow = registry.transition(paper.version_id, DeploymentStatus.SHADOW)
    shadow_report = run_shadow_comparison(
        strategy_id,
        registry_path=registry_path,
        start=start,
        end=end,
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
        out=shadow_report_path,
        evaluator=evaluator,
    )
    runtime_shadow = compare_runtime_shadow(
        strategy_id,
        ledger_path=ledger_path,
        min_fills=min_runtime_fills,
        min_pnl_delta=min_runtime_pnl_delta,
        max_drawdown_delta=max_runtime_drawdown_delta,
    )

    promoted = None
    final_status = DeploymentStatus.REJECTED
    if shadow_report["passed"] and runtime_shadow["passed"]:
        promoted = registry.promote(shadow.version_id)
        final_status = promoted.status
    else:
        rejected = registry.transition(shadow.version_id, DeploymentStatus.REJECTED)
        final_status = rejected.status

    report = {
        "strategy_id": strategy_id,
        "candidate_version_id": candidate.version_id,
        "candidate_path": str(candidate_pine),
        "evidence_path": str(evidence_path),
        "paper_version_id": paper.version_id,
        "shadow_version_id": shadow.version_id,
        "final_status": final_status.value,
        "promoted": promoted is not None,
        "promoted_version_id": promoted.version_id if promoted else None,
        "shadow": shadow_report,
        "runtime_shadow": runtime_shadow,
    }
    if report_path:
        output = Path(report_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True))
    return report


def compare_runtime_shadow(
    strategy_id: str,
    *,
    ledger_path: str | Path | None = None,
    min_fills: int = 2,
    min_pnl_delta: float = 0.0,
    max_drawdown_delta: float = 0.02,
) -> dict[str, Any]:
    if not ledger_path:
        return {"passed": True, "reasons": [], "enabled": False}
    ledger = PaperLedger(ledger_path)
    promoted = ledger.summary(strategy_id, role="promoted")
    shadow = ledger.summary(strategy_id, role="shadow")
    deltas = {
        "realized_pnl": float(shadow["realized_pnl"]) - float(promoted["realized_pnl"]),
        "max_drawdown": float(shadow["max_drawdown"]) - float(promoted["max_drawdown"]),
        "n_fills": int(shadow["n_fills"]) - int(promoted["n_fills"]),
    }
    reasons: list[str] = []
    if int(shadow["n_fills"]) < min_fills:
        reasons.append("runtime_sample")
    if deltas["realized_pnl"] < min_pnl_delta:
        reasons.append("runtime_pnl")
    if deltas["max_drawdown"] > max_drawdown_delta:
        reasons.append("runtime_drawdown")
    return {
        "passed": not reasons,
        "enabled": True,
        "reasons": reasons,
        "promoted": promoted,
        "shadow": shadow,
        "deltas": deltas,
        "gates": {
            "min_fills": min_fills,
            "min_pnl_delta": min_pnl_delta,
            "max_drawdown_delta": max_drawdown_delta,
        },
    }
