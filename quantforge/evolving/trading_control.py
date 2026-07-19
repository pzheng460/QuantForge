"""Trading control state for pause/reduce/resume decisions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ControlDecision:
    strategy_id: str
    action: str
    reasons: list[str]
    score: float | None
    updated_at: str


class TradingControl:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = (
            Path(path) if path else Path.home() / ".quantforge" / "trading_control.json"
        )

    def set_action(
        self,
        strategy_id: str,
        action: str,
        *,
        reasons: list[str] | None = None,
        score: float | None = None,
    ) -> ControlDecision:
        if action not in {"observe", "pause", "reduce", "resume", "reoptimize"}:
            raise ValueError(f"unsupported control action: {action}")
        decision = ControlDecision(
            strategy_id=strategy_id,
            action=action,
            reasons=reasons or [],
            score=score,
            updated_at=datetime.now(UTC).isoformat(),
        )
        data = self._read()
        data[strategy_id] = decision.__dict__
        self._write(data)
        return decision

    def get_action(self, strategy_id: str) -> dict[str, Any]:
        return self._read().get(strategy_id, {"action": "resume", "reasons": []})

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        tmp.replace(self.path)


def apply_auto_tune_report(
    report_path: str | Path,
    *,
    state_path: str | Path | None = None,
    strategy_id: str,
) -> ControlDecision:
    report = json.loads(Path(report_path).read_text())
    decision = (
        report.get("decision") or report.get("evidence", {}).get("decision") or {}
    )
    action = decision.get("action", "observe")
    evidence = report.get("evidence") or {}
    reasons = evidence.get("trigger_reasons") or decision.get("reasons") or []
    score = decision.get("score")
    return TradingControl(state_path).set_action(
        strategy_id, action, reasons=reasons, score=score
    )
