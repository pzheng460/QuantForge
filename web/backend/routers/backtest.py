"""Unified backtest, optimize, parse, and transpile endpoints.

All Pine Script operations go through this single router.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel

from web.backend.jobs import create_job, get_job, run_backtest_job, run_optimize_job
from web.backend.models import (
    BacktestRequest,
    JobStatusOut,
    OptimizeRequest,
    OptimizeJobStatusOut,
)

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
            elif status == "failed":
                await websocket.send_json(
                    {"status": "failed", "error": job.get("error")}
                )
                break
            else:
                await websocket.send_json({"status": status})

            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    finally:
        await websocket.close()


# ─── Optimize ────────────────────────────────────────────────────────────────


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


# ─── Pine Script utilities ───────────────────────────────────────────────────


class PineParseRequest(BaseModel):
    pine_source: str


class PineParseResponse(BaseModel):
    valid: bool
    error: Optional[str] = None
    statement_count: int = 0
    has_strategy: bool = False


class PineTranspileRequest(BaseModel):
    pine_source: str


class PineTranspileResponse(BaseModel):
    success: bool
    python_code: str = ""
    error: Optional[str] = None


@router.post("/pine/parse", response_model=PineParseResponse)
async def parse_pine(req: PineParseRequest) -> PineParseResponse:
    """Parse Pine Script source and validate syntax."""
    try:
        from quantforge.pine.parser.parser import parse
        from quantforge.pine.parser.ast_nodes import StrategyDecl

        ast = parse(req.pine_source)
        has_strategy = any(isinstance(d, StrategyDecl) for d in ast.declarations)
        return PineParseResponse(
            valid=True,
            statement_count=len(ast.body),
            has_strategy=has_strategy,
        )
    except Exception as e:
        return PineParseResponse(valid=False, error=str(e))


@router.post("/pine/transpile", response_model=PineTranspileResponse)
async def transpile_pine(req: PineTranspileRequest) -> PineTranspileResponse:
    """Transpile Pine Script to QuantForge Python code."""
    try:
        from quantforge.pine.parser.parser import parse
        from quantforge.pine.transpiler.codegen import transpile

        ast = parse(req.pine_source)
        python_code = transpile(ast)
        return PineTranspileResponse(success=True, python_code=python_code)
    except Exception as e:
        return PineTranspileResponse(success=False, error=str(e))
