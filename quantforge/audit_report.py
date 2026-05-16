"""Unified audit report builder for autonomous strategy operations."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def build_audit_report(
    strategy_id: str,
    *,
    auto_tune_path: str | Path | None = None,
    promotion_path: str | Path | None = None,
    shadow_path: str | Path | None = None,
    risk_path: str | Path | None = None,
    json_out: str | Path | None = None,
    markdown_out: str | Path | None = None,
) -> dict[str, Any]:
    auto_tune = _load_optional(auto_tune_path)
    promotion = _load_optional(promotion_path)
    shadow = _load_optional(shadow_path) or (promotion.get("shadow") if promotion else {})
    risk = _load_optional(risk_path)
    status, reasons = _overall_status(auto_tune, promotion, shadow, risk)
    report = {
        "strategy_id": strategy_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "sources": {
            "auto_tune": str(auto_tune_path) if auto_tune_path else "",
            "promotion": str(promotion_path) if promotion_path else "",
            "shadow": str(shadow_path) if shadow_path else "",
            "risk": str(risk_path) if risk_path else "",
        },
        "overall": {
            "status": status,
            "reasons": reasons,
        },
        "auto_tune": auto_tune,
        "promotion": promotion,
        "shadow": shadow,
        "risk": risk,
    }
    if json_out:
        path = Path(json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True))
    if markdown_out:
        path = Path(markdown_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_markdown(report))
    return report


def render_markdown(report: dict[str, Any]) -> str:
    auto_tune = report.get("auto_tune") or {}
    promotion = report.get("promotion") or {}
    shadow = report.get("shadow") or {}
    risk = report.get("risk") or {}
    lines = [
        f"# QuantForge Audit: {report['strategy_id']}",
        "",
        f"- Status: {report['overall']['status']}",
        f"- Reasons: {', '.join(report['overall'].get('reasons') or []) or 'none'}",
        f"- Generated at: {report['generated_at']}",
        "",
        "## Auto Tune",
        f"- Decision: {_path(auto_tune, 'decision.action', 'unknown')}",
        f"- Score: {_path(auto_tune, 'decision.score', 'unknown')}",
        f"- Worst window: {_path(auto_tune, 'evidence.worst_window', 'unknown')}",
        f"- Trigger reasons: {', '.join(_path(auto_tune, 'evidence.trigger_reasons', []) or []) or 'none'}",
        "",
        "## Promotion",
        f"- Promoted: {promotion.get('promoted', 'unknown')}",
        f"- Candidate version: {promotion.get('candidate_version_id', 'unknown')}",
        f"- Promoted version: {promotion.get('promoted_version_id', 'unknown')}",
        "",
        "## Shadow",
        f"- Passed: {shadow.get('passed', 'unknown')}",
        f"- Reasons: {', '.join(shadow.get('reasons') or []) or 'none'}",
        f"- Deltas: `{json.dumps(shadow.get('deltas', {}), sort_keys=True)}`",
        "",
        "## Risk",
        f"- Decision: {_path(risk, 'decision.action', 'unknown')}",
        f"- Reasons: {', '.join(_path(risk, 'decision.reasons', []) or []) or 'none'}",
        f"- Rolled back: {_path(risk, 'rollback.rolled_back', 'unknown')}",
    ]
    return "\n".join(lines) + "\n"


def _load_optional(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def _overall_status(
    auto_tune: dict[str, Any],
    promotion: dict[str, Any],
    shadow: dict[str, Any],
    risk: dict[str, Any],
) -> tuple[str, list[str]]:
    risk_decision = risk.get("decision") or {}
    risk_reasons = risk_decision.get("reasons") or []
    rollback = risk.get("rollback") or {}
    if rollback.get("rolled_back"):
        return "rolled_back", risk_reasons
    if risk_decision.get("action") == "pause":
        return "paused", risk_reasons
    if promotion.get("promoted"):
        return "promoted", []
    if promotion and not promotion.get("promoted"):
        return "rejected", (shadow.get("reasons") or [])
    decision = auto_tune.get("decision") or {}
    if decision.get("action"):
        return decision["action"], (auto_tune.get("evidence") or {}).get("trigger_reasons") or []
    return "unknown", []


def _path(data: dict[str, Any], dotted: str, default: Any) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur
