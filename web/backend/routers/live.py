"""Live monitoring endpoints — reads PerformanceTracker JSON files."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import List

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from strategy.backtest.registry import list_strategies, get_strategy
from web.backend.models import (
    LivePerformanceOut,
    LiveStrategyStatusOut,
    LiveTradeOut,
)

router = APIRouter()

# Project root: web/backend/routers/live.py -> parents[3] = project root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Default location written by PerformanceTracker
_DEFAULT_PERF_FILE = (
    _PROJECT_ROOT / "strategy" / "strategies" / "_base" / "live_performance.json"
)


def _find_perf_files() -> dict[str, Path]:
    """Return mapping of strategy_name -> performance JSON path.

    Currently PerformanceTracker writes to a single default path.
    Future: per-strategy files can be added here.
    """
    result: dict[str, Path] = {}
    # Scan strategy directories for live_performance.json
    strategies_dir = _PROJECT_ROOT / "strategy" / "strategies"
    for p in strategies_dir.rglob("live_performance.json"):
        # Map to strategy name based on parent directory
        parent = p.parent.name
        if parent == "_base":
            # Default file — associate with "active" key
            result["_active"] = p
        else:
            result[parent] = p
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
    """Return all registered strategies with their live performance status."""
    perf_files = _find_perf_files()
    active_perf = _load_perf(perf_files["_active"]) if "_active" in perf_files else None

    result = []
    for name in list_strategies():
        try:
            reg = get_strategy(name)
        except KeyError:
            continue

        # Check per-strategy file first, fall back to _active
        perf = None
        if name in perf_files:
            perf = _load_perf(perf_files[name])
        elif active_perf is not None:
            # The _active file doesn't store strategy name, so we show it
            # for all strategies (the user knows which one is running)
            perf = active_perf

        result.append(
            LiveStrategyStatusOut(
                strategy=name,
                display_name=reg.display_name,
                is_active=perf is not None and perf.total_trades > 0,
                performance=perf,
            )
        )
    return result


@router.get("/live/performance", response_model=LivePerformanceOut)
def get_live_performance():
    """Return the current active live performance data."""
    perf = _load_perf(_DEFAULT_PERF_FILE)
    if perf is None:
        return LivePerformanceOut()
    return perf


@router.websocket("/ws/live/performance")
async def ws_live_performance(ws: WebSocket):
    """Stream live performance updates every 3 seconds."""
    await ws.accept()
    try:
        last_update = ""
        while True:
            perf = _load_perf(_DEFAULT_PERF_FILE)
            if perf is not None and perf.last_update != last_update:
                last_update = perf.last_update
                await ws.send_json(perf.model_dump())
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        pass
