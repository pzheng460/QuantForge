"""Multi-cycle paper/shadow soak runner for autonomous bots."""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from quantforge.bot_cycle import run_bot_cycle


CycleRunner = Callable[..., dict[str, Any]]
Sleeper = Callable[[float], None]


def run_bot_soak(
    strategy_id: str,
    *,
    job_file: str | Path,
    cycles: int,
    interval_sec: float = 60.0,
    min_success_rate: float = 1.0,
    stop_on_failure: bool = False,
    report_path: str | Path | None = None,
    cycle_runner: CycleRunner | None = None,
    sleeper: Sleeper | None = None,
    **cycle_kwargs: Any,
) -> dict[str, Any]:
    if cycles < 1:
        raise ValueError("cycles must be at least 1")
    if interval_sec < 0:
        raise ValueError("interval_sec must be non-negative")
    if not 0.0 <= min_success_rate <= 1.0:
        raise ValueError("min_success_rate must be between 0 and 1")

    run_cycle = cycle_runner or run_bot_cycle
    sleep = sleeper or time.sleep
    results: list[dict[str, Any]] = []
    started = datetime.now(UTC)
    for idx in range(cycles):
        result = run_cycle(strategy_id, job_file=job_file, **cycle_kwargs)
        results.append({
            "index": idx + 1,
            "passed": bool(result.get("passed")),
            "phase": result.get("phase", "unknown"),
            "returncode": int(result.get("returncode", 1)),
            "cycle": result,
        })
        if stop_on_failure and not result.get("passed"):
            break
        if idx < cycles - 1:
            sleep(interval_sec)

    summary = _summary(results)
    report = {
        "strategy_id": strategy_id,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "passed": summary["success_rate"] >= min_success_rate,
        "config": {
            "requested_cycles": cycles,
            "interval_sec": interval_sec,
            "min_success_rate": min_success_rate,
            "stop_on_failure": stop_on_failure,
        },
        "summary": summary,
        "results": results,
    }
    if report_path:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True))
    return report


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    passed = sum(1 for item in results if item["passed"])
    failures = Counter(item["phase"] for item in results if not item["passed"])
    return {
        "cycles": total,
        "passed_cycles": passed,
        "failed_cycles": total - passed,
        "success_rate": 0.0 if total == 0 else passed / total,
        "failure_phases": dict(sorted(failures.items())),
    }
