"""Live trading permission boundary checks."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from quantforge.evolving.approvals import ApprovalQueue, ApprovalRequired


def evaluate_live_policy(
    policy_path: str | Path,
    request_path: str | Path,
    *,
    approvals_path: str | Path | None = None,
    out: str | Path | None = None,
) -> dict[str, Any]:
    policy = _load_mapping(policy_path)
    request = _load_mapping(request_path)
    violations = _violations(policy, request, approvals_path=approvals_path)
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "policy_path": str(policy_path),
        "request_path": str(request_path),
        "allowed": not violations,
        "violations": violations,
        "policy": policy,
        "request": request,
        "decision": {
            "action": "allow" if not violations else "reject",
            "reasons": violations,
        },
    }
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True))
    return report


def _load_mapping(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    text = p.read_text()
    if p.suffix.lower() in {".yaml", ".yml"}:
        return yaml.safe_load(text) or {}
    return json.loads(text)


def _violations(
    policy: dict[str, Any],
    request: dict[str, Any],
    *,
    approvals_path: str | Path | None,
) -> list[str]:
    out: list[str] = []
    allowed_symbols = set(policy.get("allowed_symbols") or [])
    symbol = request.get("symbol")
    if allowed_symbols and symbol not in allowed_symbols:
        out.append("symbol_not_allowed")
    if float(request.get("notional_usd", 0.0) or 0.0) > float(
        policy.get("max_notional_usd", float("inf"))
    ):
        out.append("notional_limit")
    if float(request.get("leverage", 0.0) or 0.0) > float(
        policy.get("max_leverage", float("inf"))
    ):
        out.append("leverage_limit")
    if int(request.get("daily_orders", 0) or 0) > int(
        policy.get("max_daily_orders", 10**12)
    ):
        out.append("daily_order_limit")
    if policy.get("require_approval", False):
        try:
            ApprovalQueue(approvals_path).require_approved(request.get("approval_id"))
        except (ApprovalRequired, KeyError):
            out.append("approval_required")
    return out
