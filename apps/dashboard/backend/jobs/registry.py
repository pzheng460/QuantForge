"""Job registry for the backtest and optimize runners.

Completed job records survive a backend restart. Persistence is pluggable:

* default: a JSON file at ``~/.quantforge/jobs/registry.json``;
* opt-in Redis: when ``QUANTFORGE_REDIS_URL`` (or ``QUANTFORGE_REDIS_HOST``)
  is set and the ``redis`` package is installed, records live in a Redis key
  instead (files under `~/.quantforge` are the default because the infra may
  not be running).

The in-memory ``_jobs`` dict is the authoritative cache in both cases; cancel
flags are inherently process-scoped (a running job dies with its process).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

_PERSIST_PATH = Path.home() / ".quantforge" / "jobs" / "registry.json"
_REDIS_KEY = "quantforge:jobs:registry"
_jobs: Dict[str, Dict[str, Any]] = {}
_cancel_flags: Dict[str, threading.Event] = {}
_lock = threading.RLock()

_JOB_TTL = timedelta(hours=1)


class JobCancelled(Exception):
    """Raised when a job is cancelled via the cancel flag."""

    pass


def _json_default(value: Any) -> Any:
    """JSON-serialize the non-trivial values stored in a job record.

    Completed jobs persist pydantic result models (BacktestResultOut,
    GridSearchResultOut, ...); without this branch the whole registry write
    raises TypeError and completed results silently never survive a restart.
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


# ─── Persistence backends ────────────────────────────────────────────────────


class _FileBackend:
    """Atomic JSON file under ~/.quantforge — the default backend."""

    def load(self) -> Dict[str, Any]:
        try:
            payload = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.error(
                "Job registry unreadable at %s (%s); starting empty",
                _PERSIST_PATH,
                exc,
            )
            return {}

    def save(self, payload: Dict[str, Any]) -> None:
        try:
            _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = _PERSIST_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, default=_json_default, indent=2))
            os.replace(tmp, _PERSIST_PATH)
            _PERSIST_PATH.chmod(0o600)
        except (OSError, TypeError) as exc:
            logger.error("Unable to persist job registry: %s", exc)


class _RedisBackend:
    """Redis-backed registry storage (opt-in via QUANTFORGE_REDIS_URL/HOST).

    Redis is best-effort: read/write failures log and degrade instead of
    crashing the API. The registry dict in memory remains authoritative, so a
    transient Redis outage only loses persistence during the outage window.
    """

    def __init__(self) -> None:
        import redis  # type: ignore[import-not-found]

        url = os.environ.get("QUANTFORGE_REDIS_URL") or (
            f"redis://{os.environ.get('QUANTFORGE_REDIS_HOST', '127.0.0.1')}"
            f":{os.environ.get('QUANTFORGE_REDIS_PORT', '6379')}"
            f"/{os.environ.get('QUANTFORGE_REDIS_DB', '0')}"
        )
        password = os.environ.get("QUANTFORGE_REDIS_PASSWORD")
        self._client = redis.Redis.from_url(url, password=password)
        self._client.ping()

    def load(self) -> Dict[str, Any]:
        try:
            raw = self._client.get(_REDIS_KEY)
            if not raw:
                return {}
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else {}
        except Exception as exc:  # noqa: BLE001 — Redis is best-effort
            logger.error("Redis job registry read failed (%s); starting empty", exc)
            return {}

    def save(self, payload: Dict[str, Any]) -> None:
        try:
            body = json.dumps(payload, default=_json_default)
            self._client.set(_REDIS_KEY, body)
        except Exception as exc:  # noqa: BLE001 — Redis is best-effort
            logger.error("Redis job registry write failed: %s", exc)


def _make_backend() -> Any:
    configured = bool(
        os.environ.get("QUANTFORGE_REDIS_URL")
        or os.environ.get("QUANTFORGE_REDIS_HOST")
    )
    if not configured:
        return _FileBackend()
    try:
        backend = _RedisBackend()
        logger.info("Job registry persisted to Redis key %s", _REDIS_KEY)
        return backend
    except Exception as exc:  # noqa: BLE001 — fall back to file when Redis is down
        logger.error(
            "Redis job registry unavailable (%s) — falling back to %s",
            exc,
            _PERSIST_PATH,
        )
        return _FileBackend()


_backend = _make_backend()


def _load_jobs() -> None:
    now = datetime.now(timezone.utc)
    for job_id, job in _backend.load().items():
        if not isinstance(job, dict):
            continue
        if "created_at" in job:
            try:
                job["created_at"] = datetime.fromisoformat(job["created_at"])
            except (TypeError, ValueError):
                job["created_at"] = now
        _jobs[job_id] = job


_load_jobs()


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
    update_job(job_id, status="cancelled")
    return True


def update_job(job_id: str, **fields: Any) -> None:
    """Atomically update job metadata and persist the registry."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        job.update(fields)
        _persist()


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
    if expired:
        _persist()


def create_job() -> str:
    with _lock:
        _cleanup_old_jobs()
        job_id = str(uuid.uuid4())
        _jobs[job_id] = {
            "status": "pending",
            "result": None,
            "error": None,
            "created_at": datetime.now(timezone.utc),
        }
        _cancel_flags[job_id] = threading.Event()
        _persist()
        return job_id


def _persist() -> None:
    _backend.save(_jobs)


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    return _jobs.get(job_id)
