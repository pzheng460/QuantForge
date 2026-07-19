"""Execution-quality risk model for live and paper orders."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quantforge.trading_control import TradingControl


@dataclass(frozen=True)
class ExecutionRiskConfig:
    max_slippage_bps: float = 50.0
    max_latency_ms: int = 1000
    max_spread_bps: float = 20.0
    min_fill_ratio: float = 0.95
    max_reject_rate: float = 0.05


def assess_execution_risk(
    orders_path: str | Path,
    *,
    strategy_id: str | None = None,
    control_state: str | Path | None = None,
    config: ExecutionRiskConfig | None = None,
    out: str | Path | None = None,
) -> dict[str, Any]:
    cfg = config or ExecutionRiskConfig()
    orders = _load_orders(orders_path)
    events = [_assess_order(order, cfg) for order in orders]
    reasons = sorted({reason for event in events for reason in event["reasons"]})
    metrics = _metrics(events)
    if metrics["reject_rate"] > cfg.max_reject_rate:
        reasons.append("rejected_order")
        reasons = sorted(set(reasons))
    action = (
        "pause"
        if any(
            r in reasons
            for r in {"rejected_order", "slippage", "partial_fill", "latency", "spread"}
        )
        else "observe"
    )
    control = None
    if strategy_id:
        control = TradingControl(control_state).set_action(
            strategy_id,
            action,
            reasons=reasons,
            score=_score(metrics, reasons),
        )
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "config": asdict(cfg),
        "orders_path": str(orders_path),
        "metrics": metrics,
        "events": events,
        "decision": {
            "action": action,
            "reasons": reasons,
            "score": _score(metrics, reasons),
        },
        "control": control.__dict__ if control else None,
    }
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True))
    return report


def _load_orders(path: str | Path) -> list[dict[str, Any]]:
    text = Path(path).read_text()
    if Path(path).suffix == ".json":
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _assess_order(order: dict[str, Any], cfg: ExecutionRiskConfig) -> dict[str, Any]:
    requested = float(order.get("requested_qty", order.get("quantity", 0.0)) or 0.0)
    filled = float(order.get("filled_qty", order.get("filled", requested)) or 0.0)
    fill_ratio = filled / requested if requested > 0 else 0.0
    slippage_bps = _slippage_bps(order)
    latency_ms = int(float(order.get("latency_ms", 0) or 0))
    spread_bps = float(order.get("spread_bps", 0.0) or 0.0)
    status = str(order.get("status", "")).lower()
    reasons: list[str] = []
    if abs(slippage_bps) > cfg.max_slippage_bps:
        reasons.append("slippage")
    if fill_ratio < cfg.min_fill_ratio and status != "rejected":
        reasons.append("partial_fill")
    if status in {"rejected", "failed", "canceled", "cancelled"}:
        reasons.append("rejected_order")
    if latency_ms > cfg.max_latency_ms:
        reasons.append("latency")
    if spread_bps > cfg.max_spread_bps:
        reasons.append("spread")
    return {
        "order": order,
        "fill_ratio": round(fill_ratio, 10),
        "slippage_bps": round(slippage_bps, 10),
        "latency_ms": latency_ms,
        "spread_bps": spread_bps,
        "reasons": reasons,
    }


def _slippage_bps(order: dict[str, Any]) -> float:
    expected = float(order.get("expected_price", order.get("price", 0.0)) or 0.0)
    fill = float(order.get("fill_price", order.get("avg_fill_price", expected)) or 0.0)
    if expected <= 0 or fill <= 0:
        return 0.0
    side = str(order.get("side", "")).lower()
    raw = ((fill - expected) / expected) * 10_000.0
    return raw if side == "buy" else -raw


def _metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(events)
    if n == 0:
        return {
            "n_orders": 0,
            "avg_fill_ratio": 0.0,
            "max_slippage_bps": 0.0,
            "max_latency_ms": 0,
            "max_spread_bps": 0.0,
            "reject_rate": 0.0,
        }
    return {
        "n_orders": n,
        "avg_fill_ratio": round(sum(e["fill_ratio"] for e in events) / n, 10),
        "max_slippage_bps": max(abs(e["slippage_bps"]) for e in events),
        "max_latency_ms": max(e["latency_ms"] for e in events),
        "max_spread_bps": max(e["spread_bps"] for e in events),
        "reject_rate": round(
            sum(1 for e in events if "rejected_order" in e["reasons"]) / n, 10
        ),
    }


def _score(metrics: dict[str, Any], reasons: list[str]) -> float:
    score = 100.0
    score -= len(reasons) * 12.0
    score -= min(40.0, metrics.get("reject_rate", 0.0) * 80.0)
    return round(max(0.0, score), 1)
