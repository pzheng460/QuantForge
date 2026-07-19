"""In-memory job registry shared by the backtest and optimize runners."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

# In-memory job store (process-scoped; resets on server restart)
_jobs: Dict[str, Dict[str, Any]] = {}
_cancel_flags: Dict[str, threading.Event] = {}

_JOB_TTL = timedelta(hours=1)


class JobCancelled(Exception):
    """Raised when a job is cancelled via the cancel flag."""

    pass


def check_cancelled(job_id: str) -> None:
    """Check if a job has been cancelled; raise JobCancelled if so."""
    flag = _cancel_flags.get(job_id)
    if flag and flag.is_set():
        raise JobCancelled(f"Job {job_id} was cancelled")


def cancel_job(job_id: str) -> bool:
    """Cancel a running job. Returns True if successfully cancelled."""
    job = _jobs.get(job_id)
    if job is None:
        return False
    if job["status"] not in ("pending", "running"):
        return False
    flag = _cancel_flags.get(job_id)
    if flag:
        flag.set()
    job["status"] = "cancelled"
    return True


def _cleanup_old_jobs() -> None:
    """Remove completed/failed/cancelled jobs older than TTL."""
    now = datetime.now(timezone.utc)
    expired = [
        jid
        for jid, j in _jobs.items()
        if j["status"] in ("completed", "failed", "cancelled")
        and (now - j.get("created_at", now)) > _JOB_TTL
    ]
    for jid in expired:
        del _jobs[jid]
        _cancel_flags.pop(jid, None)


def create_job() -> str:
    _cleanup_old_jobs()
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "status": "pending",
        "result": None,
        "error": None,
        "created_at": datetime.now(timezone.utc),
    }
    _cancel_flags[job_id] = threading.Event()
    return job_id


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    return _jobs.get(job_id)
