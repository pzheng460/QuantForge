"""Live monitoring & engine management endpoints.

Provides read-only performance monitoring (JSON file scanning) plus
start/stop control for PineLiveEngine instances running as asyncio
tasks inside the FastAPI process.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from web.backend.models import (
    LiveEngineOut,
    LivePerformanceOut,
    LiveStartRequest,
    LiveStrategyStatusOut,
    LiveTradeOut,
)

router = APIRouter()

_LIVE_DIR = Path.home() / ".quantforge" / "live"


# ─── Helpers (used by live_engines.py too) ────────────────────────────────────


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


# ─── Read-only monitoring endpoints ──────────────────────────────────────────


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


# ─── Engine management endpoints ─────────────────────────────────────────────


@router.post("/live/start", response_model=LiveEngineOut)
async def start_live(req: LiveStartRequest) -> LiveEngineOut:
    """Start a new PineLiveEngine as an asyncio task."""
    from web.backend.live_engines import list_engines, start_engine

    # Prevent duplicate engines for the same strategy
    for eng in list_engines():
        if (
            eng["strategy"] == (req.strategy or "custom_strategy")
            and eng["status"] in ("warmup", "running")
        ):
            raise HTTPException(
                status_code=409,
                detail=f"Engine for '{eng['strategy']}' is already {eng['status']}",
            )

    try:
        engine_id = await start_engine(
            strategy=req.strategy,
            pine_source=req.pine_source,
            exchange=req.exchange,
            symbol=req.symbol,
            timeframe=req.timeframe,
            demo=req.demo,
            position_size_usdt=req.position_size_usdt,
            leverage=req.leverage,
            warmup_bars=req.warmup_bars,
            config_override=req.config_override,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    from web.backend.live_engines import get_engine

    entry = get_engine(engine_id)
    return LiveEngineOut(
        engine_id=engine_id,
        status=entry["status"],
        strategy=entry["strategy"],
        exchange=entry["exchange"],
        symbol=entry["symbol"],
        timeframe=entry["timeframe"],
        demo=entry["demo"],
        leverage=entry["leverage"],
        created_at=entry["created_at"],
    )


@router.post("/live/stop/{engine_id}", response_model=LiveEngineOut)
async def stop_live(engine_id: str) -> LiveEngineOut:
    """Stop a running engine."""
    from web.backend.live_engines import get_engine, stop_engine

    entry = get_engine(engine_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Engine {engine_id} not found")

    if entry["status"] not in ("warmup", "running"):
        raise HTTPException(
            status_code=400, detail=f"Engine is {entry['status']}, cannot stop"
        )

    await stop_engine(engine_id)

    entry = get_engine(engine_id)
    perf_files = _find_perf_files()
    perf = None
    if entry["strategy"] in perf_files:
        perf = _load_perf(perf_files[entry["strategy"]])

    return LiveEngineOut(
        engine_id=engine_id,
        status=entry["status"],
        strategy=entry["strategy"],
        exchange=entry["exchange"],
        symbol=entry["symbol"],
        timeframe=entry["timeframe"],
        demo=entry["demo"],
        leverage=entry["leverage"],
        created_at=entry["created_at"],
        error=entry["error"],
        performance=perf,
    )


@router.get("/live/engines", response_model=List[LiveEngineOut])
def get_live_engines() -> List[LiveEngineOut]:
    """List all engines with their current status and performance."""
    from web.backend.live_engines import list_engines

    return [LiveEngineOut(**eng) for eng in list_engines()]
