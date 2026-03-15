"""Live monitoring endpoints — reads PerformanceTracker JSON files.

NOTE: The old Python strategy registry (strategy/) has been removed.
Live monitoring now works with Pine-based strategies.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import List

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from web.backend.models import (
    LivePerformanceOut,
    LiveStrategyStatusOut,
    LiveTradeOut,
)

router = APIRouter()

_LIVE_DIR = Path.home() / ".quantforge" / "live"


def _find_perf_files() -> dict[str, Path]:
    """Return mapping of strategy_name -> performance JSON path."""
    result: dict[str, Path] = {}
    if not _LIVE_DIR.exists():
        return result
    for p in _LIVE_DIR.glob("*/live_performance.json"):
        result[p.parent.name] = p
    return result


def _load_perf(path: Path) -> LivePerformanceOut | None:
    """Load a performance JSON file into a Pydantic model."""
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        trades_raw = data.pop("trades", [])
        trades = [LiveTradeOut(**t) for t in trades_raw]
        return LivePerformanceOut(**data, trades=trades)
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


@router.get("/live/strategies", response_model=List[LiveStrategyStatusOut])
def get_live_strategies() -> List[LiveStrategyStatusOut]:
    """Return strategies with their live performance status."""
    perf_files = _find_perf_files()
    result = []
    for name, path in perf_files.items():
        perf = _load_perf(path)
        if perf:
            result.append(
                LiveStrategyStatusOut(
                    strategy=name,
                    display_name=name.replace("_", " ").title(),
                    is_active=perf.total_trades > 0,
                    performance=perf,
                )
            )
    return result


@router.get("/live/performance", response_model=LivePerformanceOut)
def get_live_performance():
    """Return the current active live performance data."""
    perf_files = _find_perf_files()
    for path in perf_files.values():
        perf = _load_perf(path)
        if perf:
            return perf
    return LivePerformanceOut()


@router.websocket("/ws/live/performance")
async def ws_live_performance(ws: WebSocket):
    """Stream live performance updates every 3 seconds."""
    await ws.accept()
    try:
        last_update = ""
        while True:
            perf_files = _find_perf_files()
            for path in perf_files.values():
                perf = _load_perf(path)
                if perf is not None and perf.last_update != last_update:
                    last_update = perf.last_update
                    await ws.send_json(perf.model_dump())
                    break
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        pass
