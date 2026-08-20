"""Live monitoring and trusted Python engine management endpoints.

Provides start/stop/list control for Python live-engine instances running
as asyncio tasks inside the FastAPI process.

Note: per-trade live performance telemetry was previously served from
``~/.quantforge/live/<strategy>/live_performance.json``, but the migration
to the Python-first engine removed the writer that produced those files.
The endpoints/CLI that read them were dead surface (they could never return
real data), so they have been removed rather than fabricating P&L from
unconfirmed engine orders. ``/live/engines`` exposes the real, in-memory
engine registry (status, config, error) which is the truthful monitoring
data available today.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException

from apps.dashboard.backend.http_errors import safe_exception_detail
from apps.dashboard.backend.models import (
    LiveEngineOut,
    LiveStartRequest,
)

router = APIRouter()


@router.post("/live/start", response_model=LiveEngineOut)
async def start_live(req: LiveStartRequest) -> LiveEngineOut:
    """Start a registered Python strategy; every order passes hard risk checks."""
    from apps.dashboard.backend.live_engines import list_engines, start_engine

    # Prevent duplicate engines for the same strategy
    for eng in list_engines():
        if eng["strategy"] == req.strategy and eng["status"] in (
            "warmup",
            "running",
        ):
            raise HTTPException(
                status_code=409,
                detail=f"Engine for '{eng['strategy']}' is already {eng['status']}",
            )

    try:
        engine_id = await start_engine(
            strategy=req.strategy,
            exchange=req.exchange,
            symbol=req.symbol,
            timeframe=req.timeframe,
            demo=req.demo,
            position_size_usdt=req.position_size_usdt,
            leverage=req.leverage,
            warmup_bars=req.warmup_bars,
            config_override=req.config_override,
            risk_limits={
                "max_order_notional": req.max_order_notional,
                "max_spread_pct": req.max_spread_pct,
                "max_leverage": req.max_leverage,
                "max_daily_new_positions": req.max_daily_new_positions,
            },
        )
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=404,
            detail=safe_exception_detail(e, prefix="engine configuration/data not found"),
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=safe_exception_detail(e, prefix="failed to start engine"),
        )

    from apps.dashboard.backend.live_engines import get_engine

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
    from apps.dashboard.backend.live_engines import get_engine, stop_engine

    entry = get_engine(engine_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Engine {engine_id} not found")

    if entry["status"] not in ("warmup", "running"):
        raise HTTPException(
            status_code=400, detail=f"Engine is {entry['status']}, cannot stop"
        )

    await stop_engine(engine_id)

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
        error=entry["error"],
    )


@router.get("/live/engines", response_model=List[LiveEngineOut])
def get_live_engines() -> List[LiveEngineOut]:
    """List all engines — active (running/warmup) + archived (stopped/failed)."""
    from apps.dashboard.backend.live_engines import list_engines

    return [LiveEngineOut(**eng) for eng in list_engines()]


@router.delete("/live/engines/{engine_id}")
def delete_live_engine(engine_id: str):
    """Permanently remove an archived engine from the history list.

    Refuses with 409 if the engine is still warmup/running — stop it first.
    """
    from apps.dashboard.backend.live_engines import delete_engine

    try:
        delete_engine(engine_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Engine {engine_id} not found")
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=safe_exception_detail(
                exc, prefix="engine is still active; stop it before deleting"
            ),
        )
    return {"engine_id": engine_id, "deleted": True}
