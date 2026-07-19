"""Unified status snapshot for QuantForge autonomous trading bots."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from quantforge.approvals import ApprovalQueue, ApprovalRequest
from quantforge.deployment import DeploymentRegistry, DeploymentStatus, StrategyVersion
from quantforge.paper_ledger import PaperLedger
from quantforge.trading_control import TradingControl


DEFAULT_AUTO_TUNE_STATE = "eval/optimizer_ab/results/auto_tune_jobs_state.json"


def build_bot_status(
    strategy_id: str,
    *,
    registry_path: str | Path | None = None,
    ledger_path: str | Path | None = None,
    control_state_path: str | Path | None = None,
    approvals_path: str | Path | None = None,
    auto_tune_state_path: str | Path | None = DEFAULT_AUTO_TUNE_STATE,
    risk_report_path: str | Path | None = None,
    audit_report_path: str | Path | None = None,
    initial_equity: float = 100_000.0,
) -> dict[str, Any]:
    """Build a read-only status snapshot for one strategy."""
    registry = DeploymentRegistry(registry_path)
    promoted = _current_promoted(registry, strategy_id)
    shadow = _latest_status_version(registry, strategy_id, DeploymentStatus.SHADOW)
    versions = registry.list(strategy_id)
    control = TradingControl(control_state_path).get_action(strategy_id)
    pending = _pending_approvals(approvals_path, strategy_id)
    ledger = PaperLedger(ledger_path, initial_equity=initial_equity)

    auto_tune = _auto_tune_state(auto_tune_state_path, strategy_id)
    status = {
        "strategy_id": strategy_id,
        "overall_status": _overall_status(promoted, control, auto_tune),
        "deployment": {
            "promoted": _version_dict(promoted),
            "shadow": _version_dict(shadow),
            "version_count": len(versions),
            "statuses": _status_counts(versions),
        },
        "ledger": {
            "promoted": ledger.summary(strategy_id, role="promoted"),
            "paper": ledger.summary(strategy_id, role="paper"),
            "shadow": ledger.summary(strategy_id, role="shadow"),
        },
        "control": control,
        "approvals": {
            "pending_count": len(pending),
            "pending": [_approval_dict(req) for req in pending],
        },
        "auto_tune": auto_tune,
    }
    risk_report = _load_optional_json(risk_report_path)
    if risk_report:
        status["risk_report"] = risk_report
    audit_report = _load_optional_json(audit_report_path)
    if audit_report:
        status["audit_report"] = audit_report
    return status


def write_bot_status(report: dict[str, Any], out: str | Path) -> None:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=2, sort_keys=True))
    tmp.replace(path)


def _current_promoted(
    registry: DeploymentRegistry, strategy_id: str
) -> StrategyVersion | None:
    try:
        return registry.current(strategy_id)
    except KeyError:
        return None


def _latest_status_version(
    registry: DeploymentRegistry,
    strategy_id: str,
    status: DeploymentStatus,
) -> StrategyVersion | None:
    versions = [v for v in registry.list(strategy_id) if v.status == status]
    return versions[-1] if versions else None


def _status_counts(versions: list[StrategyVersion]) -> dict[str, int]:
    counts = {status.value: 0 for status in DeploymentStatus}
    for version in versions:
        counts[version.status.value] += 1
    return counts


def _overall_status(
    promoted: StrategyVersion | None,
    control: dict[str, Any],
    auto_tune: dict[str, Any],
) -> str:
    action = control.get("action", "resume")
    if action == "pause":
        return "paused"
    if action == "reduce":
        return "reduced"
    if action == "reoptimize":
        return "reoptimizing"
    if promoted is None:
        if auto_tune:
            return "paper_ready"
        return "needs_setup"
    return "running"


def _pending_approvals(
    approvals_path: str | Path | None,
    strategy_id: str,
) -> list[ApprovalRequest]:
    requests = ApprovalQueue(approvals_path).list(status="pending")
    return [
        req for req in requests if req.payload.get("strategy_id") in {None, strategy_id}
    ]


def _auto_tune_state(path: str | Path | None, strategy_id: str) -> dict[str, Any]:
    data = _load_optional_json(path)
    if not data:
        return {}
    return data.get("jobs", {}).get(strategy_id, {})


def _load_optional_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    return json.loads(target.read_text())


def _version_dict(version: StrategyVersion | None) -> dict[str, Any] | None:
    if version is None:
        return None
    data = asdict(version)
    data["status"] = version.status.value
    return data


def _approval_dict(req: ApprovalRequest) -> dict[str, Any]:
    return asdict(req)
