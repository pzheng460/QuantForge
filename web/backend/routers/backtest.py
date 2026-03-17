"""Backtest endpoints."""

from __future__ import annotations

import asyncio

from fastapi import (
    APIRouter,
    BackgroundTasks,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)

from web.backend.jobs import create_job, get_job, run_backtest_job, cancel_job
from web.backend.models import BacktestRequest, JobStatusOut

router = APIRouter()


# ─── Backtest ────────────────────────────────────────────────────────────────


@router.post("/backtest/run", response_model=JobStatusOut)
async def start_backtest(req: BacktestRequest, background_tasks: BackgroundTasks):
    """Submit a backtest job. Returns job_id immediately; poll /backtest/{id} for result."""
    job_id = create_job()
    background_tasks.add_task(run_backtest_job, job_id, req)
    return JobStatusOut(job_id=job_id, status="pending")


@router.get("/backtest/{job_id}", response_model=JobStatusOut)
def get_backtest_status(job_id: str):
    """Poll backtest job status and result."""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatusOut(
        job_id=job_id,
        status=job["status"],
        error=job.get("error"),
        result=job.get("result"),
    )


@router.post("/backtest/cancel/{job_id}")
def cancel_backtest(job_id: str):
    """Cancel a running backtest job."""
    if not cancel_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found or not cancellable")
    return {"job_id": job_id, "status": "cancelled"}


@router.websocket("/ws/backtest/{job_id}")
async def backtest_websocket(websocket: WebSocket, job_id: str):
    """Stream backtest job status via WebSocket."""
    await websocket.accept()
    try:
        while True:
            job = get_job(job_id)
            if job is None:
                await websocket.send_json({"status": "not_found"})
                break

            status = job["status"]
            if status == "completed":
                result = job["result"]
                await websocket.send_json(
                    {
                        "status": "completed",
                        "result": result.model_dump() if result else None,
                    }
                )
                break
            elif status in ("failed", "cancelled"):
                await websocket.send_json(
                    {"status": status, "error": job.get("error")}
                )
                break
            else:
                await websocket.send_json({"status": status})

            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    finally:
        await websocket.close()
