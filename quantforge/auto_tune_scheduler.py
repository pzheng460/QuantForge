"""QuantForge-owned scheduler for automatic strategy health checks."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
import csv
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from quantforge.deployment_pipeline import run_promotion_pipeline
from quantforge.trading_control import apply_auto_tune_report


class CompletedProcessLike(Protocol):
    returncode: int


Runner = Callable[[list[str], str], CompletedProcessLike]
PromotionRunner = Callable[["AutoTuneJob", dict[str, Any]], dict[str, Any]]

HIGHER_IS_BETTER = {
    "oos_sharpe": True,
    "oos_pf": True,
    "oos_win_rate": True,
    "oos_mdd": False,
    "overfit_index": False,
}


class SchedulerLockError(RuntimeError):
    """Raised when an auto-tune scheduler run is already active."""


@dataclass(frozen=True)
class AutoTuneJob:
    pine: str
    strategy: str
    windows: str
    job_id: str = ""
    symbol: str = "BTC/USDT:USDT"
    exchange: str = "bitget"
    timeframe: str = "1h"
    regime: str = "trend_2024h1"
    seeds: str = "1"
    providers: str = "claude,codex"
    news_file: str = ""
    out: str = "eval/optimizer_ab/results/auto_tune_report.json"
    history: str = "eval/optimizer_ab/results/auto_tune_history.jsonl"
    lock: str = "eval/optimizer_ab/results/auto_tune.lock"
    heartbeat: str = "eval/optimizer_ab/results/auto_tune_heartbeat.json"
    control_state: str = "eval/optimizer_ab/results/trading_control.json"
    state: str = "eval/optimizer_ab/results/auto_tune_jobs_state.json"
    runs_dir: str = "eval/optimizer_ab/results/auto_tune_runs"
    failed_dir: str = "eval/optimizer_ab/results/auto_tune_failed"
    optimizer_results_csv: str = "eval/optimizer_ab/results/auto_tune.csv"
    optimizer_trials_dir: str = "eval/optimizer_ab/results/auto_tune_trials"
    deploy_metric: str = "oos_sharpe"
    registry: str = ""
    promotion_report: str = "eval/optimizer_ab/results/promotion_pipeline.json"
    shadow_report: str = "eval/optimizer_ab/results/shadow_compare.json"
    apply_control: bool = True
    execute: bool = False
    auto_deploy: bool = False


@dataclass(frozen=True)
class AutoTuneRunResult:
    command: list[str]
    returncode: int
    report_path: str
    ran_at: str
    deployment_report_path: str = ""
    promoted: bool | None = None


def build_auto_tune_command(job: AutoTuneJob) -> list[str]:
    cmd = [
        "uv", "run", "python", "-m", "eval.auto_tune", "run",
        "--pine", job.pine,
        "--strategy", job.strategy,
        "--symbol", job.symbol,
        "--exchange", job.exchange,
        "--timeframe", job.timeframe,
        "--windows", job.windows,
        "--regime", job.regime,
        "--seeds", job.seeds,
        "--providers", job.providers,
        "--out", job.out,
        "--optimizer-results-csv", job.optimizer_results_csv,
        "--optimizer-trials-dir", job.optimizer_trials_dir,
    ]
    if job.news_file:
        cmd.extend(["--news-file", job.news_file])
    if job.execute:
        cmd.append("--execute")
    return cmd


def load_job_file(path: str | Path, *, include_disabled: bool = False) -> list[AutoTuneJob]:
    data = json.loads(Path(path).read_text())
    jobs = []
    for raw in data.get("jobs", []):
        if not include_disabled and raw.get("enabled", True) is False:
            continue
        payload = {k: v for k, v in raw.items() if k not in {"id", "enabled", "name"}}
        payload["job_id"] = raw.get("id", "")
        jobs.append(AutoTuneJob(**payload))
    return jobs


def run_once(
    job: AutoTuneJob,
    *,
    runner: Runner | None = None,
    promotion_runner: PromotionRunner | None = None,
    cwd: str | Path | None = None,
) -> AutoTuneRunResult:
    with _scheduler_lock(job):
        run_id = str(uuid.uuid4())
        run_log = Path(job.runs_dir) / f"{run_id}.jsonl"
        started = time.time()
        _append_run_event(job, run_log, "started", run_id=run_id)
        _write_heartbeat(job, "running")
        command = build_auto_tune_command(job)
        workdir = str(cwd or Path(__file__).resolve().parents[1])
        run = runner or _subprocess_runner
        completed = run(command, workdir)
        result = AutoTuneRunResult(
            command=command,
            returncode=completed.returncode,
            report_path=job.out,
            ran_at=datetime.now(UTC).isoformat(),
        )
        if result.returncode == 0 and job.apply_control and Path(job.out).exists():
            apply_auto_tune_report(job.out, state_path=job.control_state, strategy_id=job.strategy)
        deployment_report = None
        if result.returncode == 0 and job.auto_deploy:
            deployment_report = _run_auto_deploy(job, run_log, promotion_runner=promotion_runner)
            result = AutoTuneRunResult(
                command=result.command,
                returncode=result.returncode,
                report_path=result.report_path,
                ran_at=result.ran_at,
                deployment_report_path=job.promotion_report if deployment_report else "",
                promoted=deployment_report.get("promoted") if deployment_report else None,
            )
        _append_history(job, result)
        duration_ms = int((time.time() - started) * 1000)
        _update_job_state(job, result, duration_ms=duration_ms)
        _append_run_event(
            job,
            run_log,
            "finished",
            run_id=run_id,
            status="ok" if result.returncode == 0 else "error",
            returncode=result.returncode,
            duration_ms=duration_ms,
            report_path=result.report_path,
            deployment_report_path=result.deployment_report_path,
            promoted=result.promoted,
        )
        if result.returncode != 0:
            _write_failed_run(job, result, run_id=run_id, duration_ms=duration_ms)
        _write_heartbeat(job, "completed" if result.returncode == 0 else "failed")
        return result


def run_daemon(job: AutoTuneJob, *, interval_sec: int, max_runs: int | None = None) -> int:
    if interval_sec < 60:
        raise ValueError("interval_sec must be at least 60")
    runs = 0
    while max_runs is None or runs < max_runs:
        result = run_once(job)
        if result.returncode != 0:
            return result.returncode
        runs += 1
        if max_runs is not None and runs >= max_runs:
            break
        time.sleep(interval_sec)
    return 0


def _subprocess_runner(command: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd)


def select_best_candidate(csv_path: str | Path, *, metric: str = "oos_sharpe") -> dict[str, Any]:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(path)
    candidates = []
    with path.open() as f:
        for row in csv.DictReader(f):
            if _truthy(row.get("lazy_warning")) or _truthy(row.get("no_op")):
                continue
            if not _truthy(row.get("optimization_attempted")):
                continue
            if _to_float(row.get("returncode")) not in {0.0, None}:
                continue
            score = _to_float(row.get(metric))
            if score is None:
                continue
            trial_json = row.get("trial_json") or ""
            if not trial_json or not Path(trial_json).exists():
                continue
            trial = json.loads(Path(trial_json).read_text())
            pine_path = trial.get("optimized_pine") or ""
            if not pine_path or not Path(pine_path).exists():
                continue
            candidates.append({
                "trial_id": row.get("trial_id", ""),
                "metric": metric,
                "score": score,
                "pine_path": pine_path,
                "trial_json": trial_json,
                "row": row,
                "trial": trial,
            })
    if not candidates:
        raise ValueError(f"no deployable candidates in {path}")
    reverse = HIGHER_IS_BETTER.get(metric, True)
    return sorted(candidates, key=lambda c: c["score"], reverse=reverse)[0]


def _run_auto_deploy(
    job: AutoTuneJob,
    run_log: Path,
    *,
    promotion_runner: PromotionRunner | None = None,
) -> dict[str, Any] | None:
    report_path = Path(job.out)
    if not report_path.exists():
        _append_run_event(job, run_log, "auto_deploy_skipped", reason="missing_report")
        return None
    report = json.loads(report_path.read_text())
    if report.get("decision", {}).get("action") != "reoptimize":
        _append_run_event(job, run_log, "auto_deploy_skipped", reason="decision_not_reoptimize")
        return None
    if not job.execute:
        _append_run_event(job, run_log, "auto_deploy_skipped", reason="execute_disabled")
        return None
    selected = select_best_candidate(job.optimizer_results_csv, metric=job.deploy_metric)
    run = promotion_runner or _promotion_runner
    deployment_report = run(job, selected)
    _append_run_event(
        job,
        run_log,
        "auto_deploy_finished",
        candidate_path=selected["pine_path"],
        trial_id=selected["trial_id"],
        promoted=deployment_report.get("promoted"),
        promotion_report_path=job.promotion_report,
    )
    return deployment_report


def _promotion_runner(job: AutoTuneJob, selected: dict[str, Any]) -> dict[str, Any]:
    _, start, end = _primary_window(job.windows)
    return run_promotion_pipeline(
        job.strategy,
        candidate_pine=selected["pine_path"],
        evidence_path=job.out,
        registry_path=job.registry or None,
        start=start,
        end=end,
        symbol=job.symbol,
        exchange=job.exchange,
        timeframe=job.timeframe,
        source="auto_tune",
        shadow_report_path=job.shadow_report,
        report_path=job.promotion_report,
    )


def _append_history(job: AutoTuneJob, result: AutoTuneRunResult) -> None:
    path = Path(job.history)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "job": asdict(job),
        "result": asdict(result),
        "returncode": result.returncode,
        "strategy": job.strategy,
        "report_path": result.report_path,
    }
    with path.open("a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _to_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _primary_window(windows: str) -> tuple[str, str, str]:
    first = windows.split(",", 1)[0].strip()
    parts = first.split(":")
    if len(parts) != 3:
        raise ValueError(f"window must be name:start:end, got {first!r}")
    return parts[0], parts[1], parts[2]


class _scheduler_lock:
    def __init__(self, job: AutoTuneJob) -> None:
        self.path = Path(job.lock)
        self.fd: int | None = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise SchedulerLockError(f"auto-tune lock exists: {self.path}") from exc
        os.write(self.fd, str(os.getpid()).encode())
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def _write_heartbeat(job: AutoTuneJob, status: str) -> None:
    path = Path(job.heartbeat)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "strategy": job.strategy,
        "status": status,
        "updated_at": datetime.now(UTC).isoformat(),
        "report_path": job.out,
    }, indent=2, sort_keys=True))


def _append_run_event(job: AutoTuneJob, run_log: Path, action: str, **fields) -> None:
    run_log.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "ts": int(time.time() * 1000),
        "jobId": job.strategy,
        "action": action,
        **fields,
    }
    with run_log.open("a") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def _update_job_state(job: AutoTuneJob, result: AutoTuneRunResult, *, duration_ms: int) -> None:
    path = Path(job.state)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text()) if path.exists() else {"version": 1, "jobs": {}}
    jobs = data.setdefault("jobs", {})
    prior = jobs.get(job.strategy, {}).get("state", {})
    ok = result.returncode == 0
    consecutive_errors = 0 if ok else int(prior.get("consecutiveErrors", 0)) + 1
    jobs[job.strategy] = {
        "updatedAtMs": int(time.time() * 1000),
        "scheduleIdentity": json.dumps({
            "version": 1,
            "windows": job.windows,
            "symbol": job.symbol,
            "timeframe": job.timeframe,
        }, sort_keys=True),
        "state": {
            "lastRunAtMs": int(datetime.fromisoformat(result.ran_at).timestamp() * 1000),
            "lastStatus": "ok" if ok else "error",
            "lastRunStatus": "ok" if ok else "error",
            "lastDurationMs": duration_ms,
            "lastReturncode": result.returncode,
            "lastReportPath": result.report_path,
            "consecutiveErrors": consecutive_errors,
        },
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(path)


def _write_failed_run(
    job: AutoTuneJob,
    result: AutoTuneRunResult,
    *,
    run_id: str,
    duration_ms: int,
) -> None:
    path = Path(job.failed_dir) / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "run_id": run_id,
        "strategy": job.strategy,
        "command": result.command,
        "returncode": result.returncode,
        "report_path": result.report_path,
        "duration_ms": duration_ms,
        "ran_at": result.ran_at,
    }, indent=2, sort_keys=True))
