"""Closed-loop optimization endpoints."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)

from web.backend.jobs import create_job, get_job
from web.backend.models import (
    ClosedLoopRequest,
    ClosedLoopJobStatus,
    ClosedLoopIteration,
    FailureModeOut,
    ParameterChangeOut,
    MathematicalReflectionOut,
    BacktestResultOut,
    TradeOut,
    HoldoutResultOut,
)

router = APIRouter()

# Store websocket connections by job_id
_websocket_connections: Dict[str, WebSocket] = {}

# Extended job store for closed-loop specific data
_closedloop_jobs: Dict[str, ClosedLoopJobStatus] = {}


def _resolve_pine_source(strategy: Optional[str], pine_source: Optional[str]) -> str:
    """Resolve Pine source from strategy name or direct source."""
    if pine_source:
        return pine_source

    if strategy:
        # Load from strategies directory
        strategies_dir = Path(__file__).resolve().parents[2] / "quantforge" / "pine" / "strategies"
        strategy_file = strategies_dir / f"{strategy}.pine"
        if strategy_file.exists():
            return strategy_file.read_text()
        else:
            raise ValueError(f"Strategy file not found: {strategy}")

    raise ValueError("Either strategy or pine_source must be provided")


@router.post("/closedloop/run", response_model=ClosedLoopJobStatus)
async def start_closedloop_optimization(req: ClosedLoopRequest, background_tasks: BackgroundTasks):
    """Start a closed-loop optimization job."""
    job_id = create_job()

    job_status = ClosedLoopJobStatus(
        job_id=job_id,
        status="pending",
        original_pine_source=_resolve_pine_source(req.strategy, req.pine_source)
    )
    _closedloop_jobs[job_id] = job_status

    background_tasks.add_task(run_closedloop_job, job_id, req)
    return job_status


@router.get("/closedloop/{job_id}", response_model=ClosedLoopJobStatus)
def get_closedloop_status(job_id: str):
    """Get closed-loop optimization job status."""
    if job_id not in _closedloop_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return _closedloop_jobs[job_id]


@router.websocket("/ws/closedloop/{job_id}")
async def closedloop_websocket(websocket: WebSocket, job_id: str):
    """Stream closed-loop optimization updates via WebSocket."""
    await websocket.accept()
    _websocket_connections[job_id] = websocket

    try:
        while True:
            # Send periodic updates
            if job_id in _closedloop_jobs:
                status = _closedloop_jobs[job_id]
                await websocket.send_json(status.model_dump())

                if status.status in ("completed", "failed"):
                    break

            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    finally:
        if job_id in _websocket_connections:
            del _websocket_connections[job_id]


async def _notify_websocket(job_id: str):
    """Notify WebSocket clients of job updates."""
    if job_id in _websocket_connections and job_id in _closedloop_jobs:
        ws = _websocket_connections[job_id]
        status = _closedloop_jobs[job_id]
        try:
            await ws.send_json(status.model_dump())
        except Exception:
            # Remove broken connections
            del _websocket_connections[job_id]


async def run_closedloop_job(job_id: str, req: ClosedLoopRequest):
    """Run the full closed-loop optimization workflow."""
    job_status = _closedloop_jobs[job_id]
    job_status.status = "running"

    try:
        pine_source = _resolve_pine_source(req.strategy, req.pine_source)
        job_status.original_pine_source = pine_source
        job_status.final_pine_source = pine_source

        # Step 1: Validate Pine script
        await _notify_websocket(job_id)
        validation_result = await _validate_pine_script(pine_source)
        if not validation_result["valid"]:
            job_status.status = "failed"
            job_status.error = f"Pine validation failed: {validation_result['errors']}"
            await _notify_websocket(job_id)
            return

        # Step 2: Run initial baseline backtest
        baseline_result = await _run_baseline_backtest(req, pine_source)
        if not baseline_result:
            job_status.status = "failed"
            job_status.error = "Baseline backtest failed"
            await _notify_websocket(job_id)
            return

        current_pine_source = pine_source
        current_metrics = baseline_result
        consecutive_no_improvement = 0

        # Step 3: Iterate until convergence or max iterations
        for iteration in range(1, req.max_iterations + 1):
            job_status.current_iteration = iteration

            # Create new iteration
            iter_obj = ClosedLoopIteration(
                iteration=iteration,
                level=min(3, (iteration - 1) // 3 + 1),  # Level 1: iter 1-3, Level 2: iter 4-6, Level 3: iter 7-9
                metrics_before=current_metrics,
                status="running"
            )
            job_status.iterations.append(iter_obj)
            await _notify_websocket(job_id)

            # Analyze failures
            failures = await _analyze_backtest_failures(current_metrics, current_pine_source)
            iter_obj.failures = failures

            if not failures:
                # No failures - check Gate 1
                gate1_result = _check_gate1(current_metrics)
                iter_obj.gate1_pass = gate1_result["pass"]
                iter_obj.gate1_criteria = gate1_result["criteria"]

                if gate1_result["pass"]:
                    # Gate 1 passed - run Gate 2 (holdout test)
                    holdout = await _run_holdout_test(req, current_pine_source)
                    job_status.holdout_result = holdout

                    if holdout and holdout.pass_gate2:
                        job_status.final_verdict = "converged"
                        job_status.final_pine_source = current_pine_source
                        iter_obj.status = "completed"
                        job_status.status = "completed"
                        await _notify_websocket(job_id)
                        return
                    else:
                        job_status.final_verdict = "gate2_failed"
                        iter_obj.status = "completed"
                        job_status.status = "completed"
                        await _notify_websocket(job_id)
                        return
                else:
                    consecutive_no_improvement += 1
            else:
                consecutive_no_improvement = 0

            # Mathematical reflection and parameter modification
            reflection = await _mathematical_reflection(failures, iter_obj.level, current_pine_source)
            iter_obj.mathematical_reflection = reflection

            # Apply parameter changes
            if reflection and reflection.parameter_adjustments:
                modified_pine = _apply_parameter_changes(current_pine_source, reflection.parameter_adjustments)
                iter_obj.pine_source_modified = modified_pine
                iter_obj.parameter_changes = reflection.parameter_adjustments

                # Re-backtest with modifications
                new_metrics = await _run_baseline_backtest(req, modified_pine)
                if new_metrics:
                    iter_obj.metrics_after = new_metrics

                    # Check improvement
                    improvement = _calculate_improvement(current_metrics, new_metrics)
                    iter_obj.improvement_pct = improvement

                    if improvement > 0:
                        # Improvement found
                        current_pine_source = modified_pine
                        current_metrics = new_metrics
                        job_status.final_pine_source = modified_pine
                        consecutive_no_improvement = 0
                    else:
                        consecutive_no_improvement += 1
                else:
                    consecutive_no_improvement += 1
            else:
                consecutive_no_improvement += 1

            iter_obj.status = "completed"
            await _notify_websocket(job_id)

            # Check for early termination
            if consecutive_no_improvement >= 2:
                break

        # Final evaluation
        gate1_result = _check_gate1(current_metrics)
        if gate1_result["pass"]:
            holdout = await _run_holdout_test(req, current_pine_source)
            job_status.holdout_result = holdout
            if holdout and holdout.pass_gate2:
                job_status.final_verdict = "converged"
            else:
                job_status.final_verdict = "gate2_failed"
        else:
            if consecutive_no_improvement >= 2:
                job_status.final_verdict = "no_improvement"
            else:
                job_status.final_verdict = "max_iterations_reached"

        job_status.status = "completed"

    except Exception as e:
        job_status.status = "failed"
        job_status.error = str(e)

    await _notify_websocket(job_id)


async def _validate_pine_script(pine_source: str) -> Dict[str, Any]:
    """Validate Pine script using the validation script."""
    try:
        # Write Pine source to temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.pine', delete=False) as f:
            f.write(pine_source)
            pine_file = f.name

        script_path = Path.home() / ".openclaw/skills/quantforge-optimizer/scripts/validate_pine.py"
        result = subprocess.run(
            ["python", str(script_path), pine_file],
            capture_output=True,
            text=True,
            cwd="/home/pzheng46/QuantForge"
        )

        os.unlink(pine_file)

        if result.returncode == 0:
            return {"valid": True, "warnings": result.stdout}
        else:
            return {"valid": False, "errors": result.stderr or result.stdout}

    except Exception as e:
        return {"valid": False, "errors": str(e)}


async def _run_baseline_backtest(req: ClosedLoopRequest, pine_source: str) -> Optional[BacktestResultOut]:
    """Run a Pine backtest and return the results."""
    try:
        # Reuse the existing backtest logic from jobs.py
        from web.backend.jobs import _run_pine_backtest
        from web.backend.models import BacktestRequest

        # Convert ClosedLoopRequest to BacktestRequest
        backtest_req = BacktestRequest(
            pine_source=pine_source,
            exchange=req.exchange,
            symbol=req.symbol,
            timeframe=req.timeframe,
            period=req.period,
            start_date=req.start_date,
            end_date=req.end_date,
            warmup_days=req.warmup_days
        )

        return await asyncio.to_thread(_run_pine_backtest, backtest_req)

    except Exception:
        return None


async def _analyze_backtest_failures(metrics: BacktestResultOut, pine_source: str) -> List[FailureModeOut]:
    """Analyze backtest results for failure modes using the analysis script."""
    try:
        # Write metrics to temporary file in CLI output format
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            # Simulate CLI output format
            f.write(f"Backtest Results — Strategy\n")
            f.write(f"Return: {metrics.total_return_pct:.2f}%\n")
            f.write(f"Total Trades: {metrics.total_trades}\n")
            f.write(f"Win Rate: {metrics.win_rate_pct:.2f}%\n")
            f.write(f"Profit Factor: {metrics.profit_factor:.2f}\n")
            f.write(f"Max Drawdown: {metrics.max_drawdown_pct:.2f}%\n")
            f.write(f"Sharpe: {metrics.sharpe_ratio:.2f}\n")
            output_file = f.name

        # Write Pine source to temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.pine', delete=False) as f:
            f.write(pine_source)
            pine_file = f.name

        script_path = Path.home() / ".openclaw/skills/quantforge-optimizer/scripts/analyze_backtest.py"
        result = subprocess.run(
            ["python", str(script_path), "--pine-file", pine_file],
            input=open(output_file).read(),
            capture_output=True,
            text=True,
            cwd="/home/pzheng46/QuantForge"
        )

        os.unlink(output_file)
        os.unlink(pine_file)

        if result.returncode == 0 and result.stdout:
            analysis = json.loads(result.stdout)
            failures = []
            for f in analysis.get("failures", []):
                failures.append(FailureModeOut(
                    type=f["type"],
                    severity=f["severity"],
                    detail=f["detail"],
                    constraint_hint=f["constraint_hint"]
                ))
            return failures
        else:
            return []

    except Exception:
        return []


async def _mathematical_reflection(failures: List[FailureModeOut], level: int, pine_source: str) -> Optional[MathematicalReflectionOut]:
    """Perform mathematical reflection and generate parameter adjustments."""
    if not failures:
        return None

    # Simple rule-based parameter adjustment for Level 1
    if level == 1:
        adjustments = []

        for failure in failures:
            if failure.type == "WHIPSAW" and "adx" not in pine_source.lower():
                # Suggest reducing trade frequency by adjusting EMA periods
                adjustments.append(ParameterChangeOut(
                    name="fast_len",
                    before=9.0,  # Default assumption
                    after=12.0,
                    reason="Increase fast EMA to reduce whipsaw trades"
                ))
                adjustments.append(ParameterChangeOut(
                    name="slow_len",
                    before=21.0,
                    after=26.0,
                    reason="Increase slow EMA to reduce whipsaw trades"
                ))

            elif failure.type == "HIGH_DD":
                # Reduce position size
                adjustments.append(ParameterChangeOut(
                    name="default_qty_value",
                    before=100.0,
                    after=75.0,
                    reason="Reduce position size to limit drawdown"
                ))

            elif failure.type == "LOW_WIN_RATE":
                # Add stricter entry conditions
                adjustments.append(ParameterChangeOut(
                    name="rsi_threshold",
                    before=50.0,
                    after=60.0,
                    reason="Increase RSI threshold for stricter entries"
                ))

        if adjustments:
            return MathematicalReflectionOut(
                risk_scenarios=[f.detail for f in failures],
                constraints=[f.constraint_hint for f in failures],
                reasoning=f"Level {level} parameter tuning based on failure analysis",
                parameter_adjustments=adjustments
            )

    return None


def _apply_parameter_changes(pine_source: str, changes: List[ParameterChangeOut]) -> str:
    """Apply parameter changes to Pine source code."""
    modified = pine_source

    for change in changes:
        # Simple regex replacement for input parameters
        import re
        pattern = rf"({change.name}\s*=\s*input\.[^(]+\()([^,)]+)"
        match = re.search(pattern, modified)
        if match:
            modified = re.sub(pattern, f"{match.group(1)}{change.after}", modified)
        else:
            # If parameter not found, try to add it
            # This is a simplified implementation
            pass

    return modified


def _calculate_improvement(before: BacktestResultOut, after: BacktestResultOut) -> float:
    """Calculate percentage improvement between two backtest results."""
    # Use Sharpe ratio as primary improvement metric
    if before.sharpe_ratio == 0:
        return 100.0 if after.sharpe_ratio > 0 else 0.0

    return ((after.sharpe_ratio - before.sharpe_ratio) / abs(before.sharpe_ratio)) * 100


def _check_gate1(metrics: BacktestResultOut) -> Dict[str, Any]:
    """Check if metrics pass Gate 1 criteria."""
    criteria = {
        "profit_factor_gt_1_2": metrics.profit_factor > 1.2,
        "max_drawdown_lt_15": metrics.max_drawdown_pct < 15.0,
        "win_rate_gt_30": metrics.win_rate_pct > 30.0,
        "total_trades_gte_30": metrics.total_trades >= 30,
    }

    return {
        "pass": all(criteria.values()),
        "criteria": criteria
    }


async def _run_holdout_test(req: ClosedLoopRequest, pine_source: str) -> Optional[HoldoutResultOut]:
    """Run holdout test using the holdout script."""
    try:
        # Write Pine source to temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.pine', delete=False) as f:
            f.write(pine_source)
            pine_file = f.name

        script_path = Path.home() / ".openclaw/skills/quantforge-optimizer/scripts/holdout_test.py"
        result = subprocess.run(
            ["python", str(script_path), pine_file,
             "--symbol", req.symbol or "BTC/USDT:USDT",
             "--timeframe", req.timeframe],
            capture_output=True,
            text=True,
            cwd="/home/pzheng46/QuantForge"
        )

        os.unlink(pine_file)

        if result.returncode == 0 and result.stdout:
            # Parse holdout results (simplified)
            lines = result.stdout.split('\n')
            train_return = 0.0
            holdout_return = 0.0
            degradation = 0.0

            for line in lines:
                if "Train Return:" in line:
                    train_return = float(line.split(':')[1].strip().rstrip('%'))
                elif "Holdout Return:" in line:
                    holdout_return = float(line.split(':')[1].strip().rstrip('%'))
                elif "Degradation:" in line:
                    degradation = float(line.split(':')[1].strip().rstrip('%'))

            pass_gate2 = degradation < 50 and holdout_return > 0

            return HoldoutResultOut(
                train_return=train_return,
                train_sharpe=1.0,  # Placeholder
                train_drawdown=10.0,  # Placeholder
                holdout_return=holdout_return,
                holdout_sharpe=0.8,  # Placeholder
                holdout_drawdown=12.0,  # Placeholder
                degradation_pct=degradation,
                pass_gate2=pass_gate2
            )

        return None

    except Exception:
        return None