"""Evolving Mode + bot subsystem state — exposed for the Web UI.

GET  /api/bot/evolving       → current master switch + per-strategy allow-list
POST /api/bot/evolving       → toggle the master switch / add/remove strategies
GET  /api/bot/status         → control state per strategy + last cycle report
                               summary (if ~/.quantforge/ops/cycle.json exists)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from quantforge import evolving
from quantforge.evolving.trading_control import TradingControl

router = APIRouter()

_OPS_DIR = Path.home() / ".quantforge" / "ops"


class EvolvingStateOut(BaseModel):
    enabled: bool
    strategies: List[str]
    updated_at: str


class EvolvingToggleIn(BaseModel):
    enabled: Optional[bool] = None
    add_strategies: Optional[List[str]] = None
    remove_strategies: Optional[List[str]] = None


class ControlAction(BaseModel):
    strategy_id: str
    action: str
    reasons: List[str] = []
    score: Optional[float] = None
    updated_at: Optional[str] = None


class BotStatusOut(BaseModel):
    evolving: EvolvingStateOut
    control_state: List[ControlAction]
    last_cycle: Optional[dict] = None
    last_audit: Optional[dict] = None


# ─── Evolving Mode toggle ────────────────────────────────────────────────────


@router.get("/bot/evolving", response_model=EvolvingStateOut)
def get_evolving():
    return evolving.load_state()


@router.post("/bot/evolving", response_model=EvolvingStateOut)
def set_evolving(req: EvolvingToggleIn):
    if req.add_strategies:
        for name in req.add_strategies:
            evolving.add_strategy(name)
    if req.remove_strategies:
        for name in req.remove_strategies:
            evolving.remove_strategy(name)
    if req.enabled is True:
        return evolving.enable()
    if req.enabled is False:
        return evolving.disable()
    return evolving.load_state()


# ─── Bot status snapshot ─────────────────────────────────────────────────────


def _load_optional_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


@router.get("/bot/status", response_model=BotStatusOut)
def get_bot_status():
    state = evolving.load_state()
    ctrl_raw = TradingControl()._read()
    control_state = [
        ControlAction(
            strategy_id=sid,
            action=entry.get("action", "resume"),
            reasons=entry.get("reasons", []),
            score=entry.get("score"),
            updated_at=entry.get("updated_at"),
        )
        for sid, entry in ctrl_raw.items()
    ]
    return BotStatusOut(
        evolving=EvolvingStateOut(**state),
        control_state=control_state,
        last_cycle=_load_optional_json(_OPS_DIR / "cycle.json"),
        last_audit=_load_optional_json(_OPS_DIR / "audit.json"),
    )


@router.get("/bot/cycle/{strategy_id}")
def get_last_cycle(strategy_id: str):
    """Return the most recent cycle.json if its strategy_id matches."""
    cycle = _load_optional_json(_OPS_DIR / "cycle.json")
    if not cycle:
        raise HTTPException(status_code=404, detail="no cycle has been run")
    if cycle.get("strategy_id") and cycle.get("strategy_id") != strategy_id:
        raise HTTPException(status_code=404, detail="no cycle for this strategy yet")
    return cycle
