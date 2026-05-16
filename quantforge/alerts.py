"""Alert delivery helpers for autonomous bot operations."""

from __future__ import annotations

import json
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


Poster = Callable[[str, dict[str, Any], float], dict[str, Any]]


def emit_alert(
    event_type: str,
    strategy_id: str,
    *,
    severity: str,
    payload: dict[str, Any],
    jsonl_path: str | Path | None = None,
    webhook_url: str | None = None,
    timeout: float = 5.0,
    poster: Poster | None = None,
) -> dict[str, Any]:
    event = {
        "event_type": event_type,
        "strategy_id": strategy_id,
        "severity": severity,
        "created_at": datetime.now(UTC).isoformat(),
        "payload": payload,
        "delivery": {},
    }
    if jsonl_path:
        path = Path(jsonl_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(_without_delivery(event), sort_keys=True) + "\n")
        event["delivery"]["jsonl"] = {"written": True, "path": str(path)}
    if webhook_url:
        try:
            post = poster or _post_json
            event["delivery"]["webhook"] = post(webhook_url, _without_delivery(event), timeout)
        except Exception as exc:  # noqa: BLE001 - alerts must not crash trading control flow.
            event["delivery"]["webhook"] = {"status": "error", "error": str(exc)}
    return event


def should_alert_cycle(report: dict[str, Any]) -> tuple[bool, str, str]:
    risk_action = ((report.get("risk") or {}).get("decision") or {}).get("action")
    if risk_action == "pause":
        return True, "critical", "bot_risk_pause"
    if risk_action == "reduce":
        return True, "warning", "bot_risk_reduce"
    if not report.get("passed", False):
        return True, "critical", "bot_cycle_failed"
    return False, "info", "bot_cycle_ok"


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = json.dumps(payload, sort_keys=True).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return {"status": response.status, "reason": response.reason}


def _without_delivery(event: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in event.items() if k != "delivery"}
