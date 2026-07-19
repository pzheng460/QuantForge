"""File-backed approval queue for high-risk trading actions."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ApprovalRequired(RuntimeError):
    """Raised when a high-risk action lacks approval."""


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    action: str
    payload: dict[str, Any]
    status: str
    requested_at: str
    approved_at: str | None = None
    approver: str | None = None


class ApprovalQueue:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = (
            Path(path) if path else Path.home() / ".quantforge" / "approvals.json"
        )

    def request(self, action: str, payload: dict[str, Any]) -> ApprovalRequest:
        req = ApprovalRequest(
            approval_id=str(uuid.uuid4()),
            action=action,
            payload=payload,
            status="pending",
            requested_at=_now(),
        )
        data = self._read()
        data.setdefault("requests", {})[req.approval_id] = asdict(req)
        self._write(data)
        return req

    def approve(self, approval_id: str, *, approver: str) -> ApprovalRequest:
        req = self.get(approval_id)
        approved = ApprovalRequest(
            approval_id=req.approval_id,
            action=req.action,
            payload=req.payload,
            status="approved",
            requested_at=req.requested_at,
            approved_at=_now(),
            approver=approver,
        )
        data = self._read()
        data.setdefault("requests", {})[approval_id] = asdict(approved)
        self._write(data)
        return approved

    def get(self, approval_id: str) -> ApprovalRequest:
        raw = self._read().get("requests", {}).get(approval_id)
        if not raw:
            raise KeyError(approval_id)
        return ApprovalRequest(**raw)

    def list(self, status: str | None = None) -> list[ApprovalRequest]:
        requests = [
            ApprovalRequest(**raw) for raw in self._read().get("requests", {}).values()
        ]
        if status:
            requests = [r for r in requests if r.status == status]
        return sorted(requests, key=lambda r: r.requested_at)

    def require_approved(self, approval_id: str | None) -> ApprovalRequest:
        if not approval_id:
            raise ApprovalRequired("approval is required")
        req = self.get(approval_id)
        if req.status != "approved":
            raise ApprovalRequired(f"approval {approval_id} is {req.status}")
        return req

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"requests": {}}
        return json.loads(self.path.read_text())

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        tmp.replace(self.path)


def _now() -> str:
    return datetime.now(UTC).isoformat()
