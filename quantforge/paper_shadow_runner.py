"""Runtime paper/shadow observation runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quantforge.deployment import (
    DeploymentError,
    DeploymentRegistry,
    DeploymentStatus,
    StrategyVersion,
)
from quantforge.paper_ledger import PaperLedger


def run_shadow_observation(
    strategy_id: str,
    *,
    events_path: str | Path,
    registry_path: str | Path | None = None,
    ledger_path: str | Path | None = None,
    initial_equity: float = 100_000.0,
    fee_rate: float = 0.0,
    slippage_bps: float = 0.0,
    out: str | Path | None = None,
) -> dict[str, Any]:
    """Record promoted/shadow signals from a normalized runtime JSONL stream."""
    registry = DeploymentRegistry(registry_path)
    promoted = registry.current(strategy_id)
    shadow = _latest_shadow_candidate(
        registry, strategy_id, exclude_version_id=promoted.version_id
    )
    ledger = PaperLedger(ledger_path, initial_equity=initial_equity)
    processed = 0
    recorded = 0
    for event in _load_events(events_path):
        processed += 1
        price = float(event["price"])
        ts = event.get("ts")
        for role, version in [("promoted", promoted), ("shadow", shadow)]:
            signal = event.get(role)
            if not signal:
                continue
            ledger.record_signal(
                strategy_id=strategy_id,
                role=role,
                side=signal["side"],
                price=float(signal.get("price", price)),
                quantity=float(signal["quantity"]),
                ts=ts,
                version_id=version.version_id,
                fee_rate=float(signal.get("fee_rate", fee_rate)),
                slippage_bps=float(signal.get("slippage_bps", slippage_bps)),
                metadata={"source": "shadow_observation"},
            )
            recorded += 1
    report = {
        "strategy_id": strategy_id,
        "processed_events": processed,
        "recorded_signals": recorded,
        "versions": {
            "promoted": promoted.version_id,
            "shadow": shadow.version_id,
        },
        "paths": {
            "events": str(events_path),
            "ledger": str(ledger.path),
            "registry": str(registry.path),
        },
        "summary": ledger.summary(strategy_id),
    }
    if out:
        output = Path(out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True))
    return report


def _latest_shadow_candidate(
    registry: DeploymentRegistry,
    strategy_id: str,
    *,
    exclude_version_id: str,
) -> StrategyVersion:
    candidates = [
        version
        for version in registry.list(strategy_id)
        if version.status == DeploymentStatus.SHADOW
        and version.version_id != exclude_version_id
    ]
    if not candidates:
        raise DeploymentError(f"no shadow candidate for {strategy_id}")
    return candidates[-1]


def _load_events(path: str | Path) -> list[dict[str, Any]]:
    events = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events
