"""Optimizer job endpoints (Pine Script grid search)."""

from __future__ import annotations

import asyncio

from fastapi import (
    APIRouter,
    BackgroundTasks,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)

from web.backend.jobs import create_job, get_job, run_optimize_job
from web.backend.models import OptimizeRequest, OptimizeJobStatusOut

router = APIRouter()


def _job_to_optimize_status(job_id: str, job: dict) -> OptimizeJobStatusOut:
    return OptimizeJobStatusOut(
        job_id=job_id,
        status=job["status"],
        error=job.get("error"),
        mode=job.get("mode"),
        grid_result=job.get("grid_result"),
        wfo_result=job.get("wfo_result"),
        full_result=job.get("full_result"),
        heatmap_result=job.get("heatmap_result"),
    )


@router.post("/optimize/run", response_model=OptimizeJobStatusOut)
async def start_optimize(req: OptimizeRequest, background_tasks: BackgroundTasks):
    """Submit an optimization job."""
    job_id = create_job()
    background_tasks.add_task(run_optimize_job, job_id, req)
    return OptimizeJobStatusOut(job_id=job_id, status="pending", mode=req.mode)


@router.get("/optimize/{job_id}", response_model=OptimizeJobStatusOut)
def get_optimize_status(job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_optimize_status(job_id, job)


@router.websocket("/ws/optimize/{job_id}")
async def optimize_websocket(websocket: WebSocket, job_id: str):
    """Stream optimization job status via WebSocket."""
    await websocket.accept()
    try:
        while True:
            job = get_job(job_id)
            if job is None:
                await websocket.send_json({"status": "not_found"})
                break

            status = job["status"]
            if status in ("completed", "failed"):
                out = _job_to_optimize_status(job_id, job)
                await websocket.send_json(out.model_dump())
                break
            else:
                await websocket.send_json({"status": status, "job_id": job_id})

            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass
    finally:
        await websocket.close()
