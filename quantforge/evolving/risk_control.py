"""Runtime risk checks backed by the paper/shadow ledger."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quantforge.evolving.deployment import DeploymentError, DeploymentRegistry
from quantforge.evolving.paper_ledger import PaperLedger
from quantforge.evolving.trading_control import TradingControl


@dataclass(frozen=True)
class RiskConfig:
    max_drawdown: float = 0.05
    max_daily_loss: float = 500.0
    reduce_daily_loss: float = 250.0
    max_single_loss: float = 250.0
    max_consecutive_losses: int = 3
    initial_equity: float = 100_000.0


def check_risk(
    strategy_id: str,
    *,
    role: str = "promoted",
    ledger_path: str | Path | None = None,
    control_state: str | Path | None = None,
    registry_path: str | Path | None = None,
    auto_rollback: bool = False,
    config: RiskConfig | None = None,
    out: str | Path | None = None,
) -> dict[str, Any]:
    cfg = config or RiskConfig()
    ledger = PaperLedger(ledger_path, initial_equity=cfg.initial_equity)
    summary = ledger.summary(strategy_id, role=role)
    data = ledger._read()
    fills = _fills_for(data, strategy_id, role)
    daily_pnl = _daily_realized_pnl(fills)
    consecutive_losses = _consecutive_losses(fills)
    largest_loss = min(
        [float(f.get("realized_pnl_delta", 0.0)) for f in fills] or [0.0]
    )

    hard_reasons: list[str] = []
    warning_reasons: list[str] = []
    if float(summary["max_drawdown"]) >= cfg.max_drawdown:
        hard_reasons.append("max_drawdown")
    if daily_pnl <= -abs(cfg.max_daily_loss):
        hard_reasons.append("daily_loss")
    if largest_loss <= -abs(cfg.max_single_loss):
        hard_reasons.append("single_loss")
    if consecutive_losses >= cfg.max_consecutive_losses:
        hard_reasons.append("consecutive_losses")
    if daily_pnl <= -abs(cfg.reduce_daily_loss):
        warning_reasons.append("daily_loss_warning")

    if hard_reasons:
        action = "pause"
        reasons = hard_reasons
    elif warning_reasons:
        action = "reduce"
        reasons = warning_reasons
    else:
        action = "observe"
        reasons = []

    score = _risk_score(summary, daily_pnl, consecutive_losses, cfg)
    control = TradingControl(control_state).set_action(
        strategy_id, action, reasons=reasons, score=score
    )
    rollback = _maybe_rollback(
        strategy_id,
        registry_path=registry_path,
        auto_rollback=auto_rollback,
        action=action,
    )
    report = {
        "strategy_id": strategy_id,
        "role": role,
        "checked_at": datetime.now(UTC).isoformat(),
        "config": asdict(cfg),
        "summary": summary,
        "runtime": {
            "daily_realized_pnl": round(daily_pnl, 10),
            "largest_loss": round(largest_loss, 10),
            "consecutive_losses": consecutive_losses,
        },
        "decision": {
            "action": action,
            "reasons": reasons,
            "score": score,
        },
        "control": control.__dict__,
        "rollback": rollback,
    }
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True))
    return report


def _maybe_rollback(
    strategy_id: str,
    *,
    registry_path: str | Path | None,
    auto_rollback: bool,
    action: str,
) -> dict[str, Any]:
    if not auto_rollback:
        return {"rolled_back": False, "reason": "disabled"}
    if action != "pause":
        return {"rolled_back": False, "reason": "decision_not_pause"}
    registry = DeploymentRegistry(registry_path)
    try:
        current = registry.current(strategy_id)
        restored = registry.rollback(strategy_id)
    except (DeploymentError, KeyError) as exc:
        return {"rolled_back": False, "reason": str(exc)}
    return {
        "rolled_back": True,
        "from_version_id": current.version_id,
        "to_version_id": restored.version_id,
        "registry_path": str(registry.path),
    }


def _fills_for(
    data: dict[str, Any], strategy_id: str, role: str
) -> list[dict[str, Any]]:
    return [
        fill
        for fill in data.get("fills", [])
        if fill.get("strategy_id") == strategy_id and fill.get("role") == role
    ]


def _daily_realized_pnl(fills: list[dict[str, Any]]) -> float:
    today = datetime.now(UTC).date()
    total = 0.0
    for fill in fills:
        ts = str(fill.get("ts", ""))
        try:
            day = datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if day == today:
            total += float(fill.get("realized_pnl_delta", 0.0))
    if total == 0.0:
        total = sum(float(fill.get("realized_pnl_delta", 0.0)) for fill in fills)
    return total


def _consecutive_losses(fills: list[dict[str, Any]]) -> int:
    count = 0
    for fill in reversed(fills):
        pnl = float(fill.get("realized_pnl_delta", 0.0))
        if pnl < 0:
            count += 1
        elif pnl > 0:
            break
    return count


def _risk_score(
    summary: dict[str, Any],
    daily_pnl: float,
    consecutive_losses: int,
    cfg: RiskConfig,
) -> float:
    score = 100.0
    score -= min(
        60.0, (float(summary["max_drawdown"]) / max(cfg.max_drawdown, 1e-9)) * 35.0
    )
    if daily_pnl < 0:
        score -= min(35.0, (abs(daily_pnl) / max(cfg.max_daily_loss, 1e-9)) * 35.0)
    score -= min(20.0, (consecutive_losses / max(cfg.max_consecutive_losses, 1)) * 20.0)
    return round(max(0.0, score), 1)
