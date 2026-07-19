"""Preflight checks for QuantForge autonomous bot operations."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quantforge.evolving.auto_tune_scheduler import AutoTuneJob, load_job_file
from quantforge.evolving.deployment import DeploymentRegistry
from quantforge.evolving.live_policy import evaluate_live_policy


def run_bot_preflight(
    strategy_id: str,
    *,
    mode: str = "paper",
    job_file: str | Path | None = None,
    policy_path: str | Path | None = None,
    request_path: str | Path | None = None,
    approvals_path: str | Path | None = None,
    registry_path: str | Path | None = None,
    cron_file: str | Path | None = None,
    require_promoted: bool = False,
    out: str | Path | None = None,
) -> dict[str, Any]:
    if mode not in {"paper", "shadow", "live"}:
        raise ValueError("mode must be paper, shadow, or live")

    errors: list[str] = []
    checks: dict[str, Any] = {}
    job = _check_job(strategy_id, job_file, errors)
    checks["job"] = job["check"]
    checks["pine"] = _check_pine(job["job"], errors)
    checks["windows"] = _check_windows(job["job"], errors)
    if cron_file:
        checks["cron"] = _check_cron(cron_file, strategy_id, errors)
    if registry_path or require_promoted:
        checks["registry"] = _check_registry(
            strategy_id, registry_path, require_promoted, errors
        )
    if mode == "live":
        checks["live_policy"] = _check_live_policy(
            policy_path, request_path, approvals_path, errors
        )

    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "strategy_id": strategy_id,
        "mode": mode,
        "passed": not errors,
        "errors": errors,
        "checks": checks,
    }
    if out:
        _write_json(Path(out), report)
    return report


def _check_job(
    strategy_id: str,
    job_file: str | Path | None,
    errors: list[str],
) -> dict[str, Any]:
    if not job_file:
        errors.append("job_file_missing")
        return {"job": None, "check": {"passed": False, "reason": "job_file_missing"}}
    path = Path(job_file)
    if not path.exists():
        errors.append("job_file_not_found")
        return {
            "job": None,
            "check": {
                "passed": False,
                "path": str(path),
                "reason": "job_file_not_found",
            },
        }
    try:
        jobs = load_job_file(path)
    except Exception as exc:
        errors.append("job_file_invalid")
        return {
            "job": None,
            "check": {"passed": False, "path": str(path), "reason": str(exc)},
        }
    matches = [
        job for job in jobs if job.strategy == strategy_id or job.job_id == strategy_id
    ]
    if not matches:
        errors.append("job_not_found")
        return {
            "job": None,
            "check": {"passed": False, "path": str(path), "reason": "job_not_found"},
        }
    job = matches[0]
    return {
        "job": job,
        "check": {
            "passed": True,
            "path": str(path),
            "strategy": job.strategy,
            "job_id": job.job_id,
            "execute": job.execute,
            "auto_deploy": job.auto_deploy,
        },
    }


def _check_pine(job: AutoTuneJob | None, errors: list[str]) -> dict[str, Any]:
    if job is None:
        return {"passed": False, "reason": "job_missing"}
    path = Path(job.pine)
    if not path.exists():
        errors.append("pine_missing")
        return {"passed": False, "path": str(path), "reason": "pine_missing"}
    text = path.read_text(errors="ignore")
    has_strategy = "strategy(" in text or "indicator(" in text
    if not has_strategy:
        errors.append("pine_entrypoint_missing")
    return {"passed": has_strategy, "path": str(path), "has_entrypoint": has_strategy}


def _check_windows(job: AutoTuneJob | None, errors: list[str]) -> dict[str, Any]:
    if job is None:
        return {"passed": False, "reason": "job_missing"}
    invalid = []
    for window in job.windows.split(","):
        parts = window.strip().split(":")
        if len(parts) != 3 or not all(parts):
            invalid.append(window)
    if invalid:
        errors.append("windows_invalid")
    return {"passed": not invalid, "windows": job.windows, "invalid": invalid}


def _check_cron(
    cron_file: str | Path, strategy_id: str, errors: list[str]
) -> dict[str, Any]:
    path = Path(cron_file)
    if not path.exists():
        errors.append("cron_file_not_found")
        return {"passed": False, "path": str(path), "reason": "cron_file_not_found"}
    text = path.read_text()
    ok = (
        "quantforge-cli auto-tune run-once" in text
        or "quantforge-cli bot cycle" in text
    ) and strategy_id in text
    if not ok:
        errors.append("cron_command_invalid")
    return {"passed": ok, "path": str(path), "contains_strategy": strategy_id in text}


def _check_registry(
    strategy_id: str,
    registry_path: str | Path | None,
    require_promoted: bool,
    errors: list[str],
) -> dict[str, Any]:
    try:
        current = DeploymentRegistry(registry_path).current(strategy_id)
    except KeyError:
        if require_promoted:
            errors.append("promoted_version_missing")
        return {"passed": not require_promoted, "promoted": None}
    return {
        "passed": True,
        "promoted": current.version_id,
        "pine_path": current.pine_path,
    }


def _check_live_policy(
    policy_path: str | Path | None,
    request_path: str | Path | None,
    approvals_path: str | Path | None,
    errors: list[str],
) -> dict[str, Any]:
    if not policy_path or not request_path:
        errors.append("live_policy_files_missing")
        return {
            "passed": False,
            "allowed": False,
            "violations": ["live_policy_files_missing"],
        }
    if not Path(policy_path).exists() or not Path(request_path).exists():
        errors.append("live_policy_files_not_found")
        return {
            "passed": False,
            "allowed": False,
            "violations": ["live_policy_files_not_found"],
        }
    report = evaluate_live_policy(
        policy_path, request_path, approvals_path=approvals_path
    )
    if not report["allowed"]:
        errors.extend(report["violations"])
    return {
        "passed": report["allowed"],
        "allowed": report["allowed"],
        "violations": report["violations"],
        "decision": report["decision"],
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
