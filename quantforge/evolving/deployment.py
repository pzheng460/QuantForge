"""Strategy deployment registry and promotion gate.

The registry is deliberately file-backed and side-effect-light. It records
which strategy artifact is eligible for paper/shadow/live promotion, but it
does not restart live engines or submit/cancel orders.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from quantforge.evolving.approvals import ApprovalQueue
from quantforge.evolving.live_policy import evaluate_live_policy


class DeploymentStatus(StrEnum):
    CANDIDATE = "candidate"
    PAPER = "paper"
    SHADOW = "shadow"
    PROMOTED = "promoted"
    ARCHIVED = "archived"
    REJECTED = "rejected"


class DeploymentError(RuntimeError):
    """Base deployment registry error."""


class PromotionRejected(DeploymentError):
    """Raised when evidence does not satisfy the promotion gate."""


@dataclass(frozen=True)
class StrategyVersion:
    strategy_id: str
    version_id: str
    pine_path: str
    evidence_path: str
    status: DeploymentStatus
    source: str
    created_at: str
    promoted_at: str | None = None
    previous_version_id: str | None = None


class DeploymentRegistry:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = (
            Path(path) if path else Path.home() / ".quantforge" / "deployments.json"
        )

    def register_candidate(
        self,
        strategy_id: str,
        pine_path: str | Path,
        evidence_path: str | Path,
        source: str = "manual",
    ) -> StrategyVersion:
        pine = Path(pine_path)
        evidence = Path(evidence_path)
        version = StrategyVersion(
            strategy_id=strategy_id,
            version_id=self._version_id(strategy_id, pine, evidence),
            pine_path=str(pine),
            evidence_path=str(evidence),
            status=DeploymentStatus.CANDIDATE,
            source=source,
            created_at=_now(),
        )
        data = self._read()
        versions = data.setdefault("versions", {})
        versions[version.version_id] = _serialize(version)
        self._write(data)
        return version

    def get(self, version_id: str) -> StrategyVersion:
        raw = self._read().get("versions", {}).get(version_id)
        if not raw:
            raise KeyError(version_id)
        return _deserialize(raw)

    def list(self, strategy_id: str | None = None) -> list[StrategyVersion]:
        versions = [_deserialize(v) for v in self._read().get("versions", {}).values()]
        if strategy_id:
            versions = [v for v in versions if v.strategy_id == strategy_id]
        return sorted(versions, key=lambda v: v.created_at)

    def current(self, strategy_id: str) -> StrategyVersion:
        promoted = [
            v for v in self.list(strategy_id) if v.status == DeploymentStatus.PROMOTED
        ]
        if not promoted:
            raise KeyError(f"no promoted version for {strategy_id}")
        return promoted[-1]

    def transition(self, version_id: str, status: DeploymentStatus) -> StrategyVersion:
        if status == DeploymentStatus.PROMOTED:
            return self.promote(version_id)
        version = self.get(version_id)
        _validate_transition(version.status, status)
        updated = _replace(version, status=status)
        self._save_version(updated)
        return updated

    def promote(self, version_id: str) -> StrategyVersion:
        version = self.get(version_id)
        if version.status != DeploymentStatus.SHADOW:
            raise DeploymentError("only shadow versions can be promoted")
        evidence = _load_json(Path(version.evidence_path))
        _assert_promotable(evidence)

        data = self._read()
        versions = data.setdefault("versions", {})
        previous_id = None
        for vid, raw in list(versions.items()):
            existing = _deserialize(raw)
            if (
                existing.strategy_id == version.strategy_id
                and existing.status == DeploymentStatus.PROMOTED
            ):
                previous_id = existing.version_id
                versions[vid] = _serialize(
                    _replace(existing, status=DeploymentStatus.ARCHIVED)
                )

        promoted = _replace(
            version,
            status=DeploymentStatus.PROMOTED,
            promoted_at=_now(),
            previous_version_id=previous_id,
        )
        versions[promoted.version_id] = _serialize(promoted)
        self._write(data)
        return promoted

    def rollback(self, strategy_id: str) -> StrategyVersion:
        current = self.current(strategy_id)
        if not current.previous_version_id:
            raise DeploymentError("no previous promoted version to roll back to")
        previous = self.get(current.previous_version_id)
        data = self._read()
        versions = data.setdefault("versions", {})
        versions[current.version_id] = _serialize(
            _replace(current, status=DeploymentStatus.ARCHIVED)
        )
        restored = _replace(
            previous, status=DeploymentStatus.PROMOTED, promoted_at=_now()
        )
        versions[restored.version_id] = _serialize(restored)
        self._write(data)
        return restored

    def _save_version(self, version: StrategyVersion) -> None:
        data = self._read()
        data.setdefault("versions", {})[version.version_id] = _serialize(version)
        self._write(data)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"versions": {}}
        return json.loads(self.path.read_text())

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        tmp.replace(self.path)

    @staticmethod
    def _version_id(strategy_id: str, pine: Path, evidence: Path) -> str:
        payload = "|".join(
            [
                strategy_id,
                str(pine),
                pine.read_text() if pine.exists() else "",
                evidence.read_text() if evidence.exists() else "",
                _now(),
            ]
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()[:12]
        return f"{strategy_id}__{digest}"


def _assert_promotable(report: dict[str, Any]) -> None:
    decision = (
        report.get("decision") or report.get("evidence", {}).get("decision") or {}
    )
    if decision.get("action") != "observe":
        raise PromotionRejected(f"decision action is {decision.get('action')!r}")
    evidence = report.get("evidence") or {}
    news = evidence.get("news_risk") or {}
    if news.get("risk_level") == "high":
        raise PromotionRejected("news risk is high")
    if evidence.get("trigger_reasons"):
        raise PromotionRejected("evidence still has trigger reasons")


def _validate_transition(current: DeploymentStatus, target: DeploymentStatus) -> None:
    allowed = {
        DeploymentStatus.CANDIDATE: {
            DeploymentStatus.PAPER,
            DeploymentStatus.REJECTED,
            DeploymentStatus.ARCHIVED,
        },
        DeploymentStatus.PAPER: {
            DeploymentStatus.SHADOW,
            DeploymentStatus.REJECTED,
            DeploymentStatus.ARCHIVED,
        },
        DeploymentStatus.SHADOW: {DeploymentStatus.REJECTED, DeploymentStatus.ARCHIVED},
        DeploymentStatus.PROMOTED: {DeploymentStatus.ARCHIVED},
        DeploymentStatus.ARCHIVED: set(),
        DeploymentStatus.REJECTED: set(),
    }
    if target not in allowed[current]:
        raise DeploymentError(f"invalid transition {current.value} -> {target.value}")


def _serialize(version: StrategyVersion) -> dict[str, Any]:
    data = asdict(version)
    data["status"] = version.status.value
    return data


def _deserialize(data: dict[str, Any]) -> StrategyVersion:
    return StrategyVersion(
        strategy_id=data["strategy_id"],
        version_id=data["version_id"],
        pine_path=data["pine_path"],
        evidence_path=data["evidence_path"],
        status=DeploymentStatus(data["status"]),
        source=data.get("source", "manual"),
        created_at=data["created_at"],
        promoted_at=data.get("promoted_at"),
        previous_version_id=data.get("previous_version_id"),
    )


def _replace(version: StrategyVersion, **changes: Any) -> StrategyVersion:
    data = asdict(version)
    data.update(changes)
    if not isinstance(data["status"], DeploymentStatus):
        data["status"] = DeploymentStatus(data["status"])
    return StrategyVersion(**data)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _now() -> str:
    return datetime.now(UTC).isoformat()


def build_live_command(
    strategy_id: str,
    *,
    registry_path: str | Path | None = None,
    mode: str = "paper",
    control_state: str = "eval/optimizer_ab/results/trading_control.json",
    approvals_path: str | Path | None = None,
    approval_id: str | None = None,
    policy_path: str | Path | None = None,
    request_path: str | Path | None = None,
    extra: list[str] | None = None,
) -> list[str]:
    if mode not in {"paper", "shadow", "live"}:
        raise ValueError("mode must be paper, shadow, or live")
    version = DeploymentRegistry(registry_path).current(strategy_id)
    cmd = ["uv", "run", "quantforge-cli", "live", version.pine_path]
    if mode in {"paper", "shadow"}:
        cmd.append("--dry-run")
    elif mode == "live":
        ApprovalQueue(approvals_path).require_approved(approval_id)
        if policy_path or request_path:
            if not policy_path or not request_path:
                raise DeploymentError(
                    "both policy_path and request_path are required for live policy"
                )
            policy_report = evaluate_live_policy(
                policy_path, request_path, approvals_path=approvals_path
            )
            if not policy_report["allowed"]:
                raise DeploymentError(
                    f"live policy rejected: {', '.join(policy_report['violations'])}"
                )
        cmd.append("--confirm-live")
    cmd.extend(["--control-state", control_state])
    if extra:
        cmd.extend(extra)
    return cmd
