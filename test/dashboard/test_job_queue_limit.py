"""Job-queue back-pressure: create_job caps pending+running jobs.

``MAX_PARALLEL_JOBS`` only caps simultaneous execution, not the queue depth.
Without a pending/running cap a client could submit an unbounded number of
jobs (each rewriting the whole registry). Exceeding MAX_QUEUED_JOBS raises
JobLimitExceededError, which the backtest/optimize routers map to HTTP 429.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.dashboard.backend.jobs import registry
from apps.dashboard.backend.main import app


@pytest.fixture
def isolated_registry(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "_PERSIST_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(registry, "_jobs", {})
    monkeypatch.setattr(registry, "_cancel_flags", {})
    monkeypatch.setattr(registry, "_backend", registry._FileBackend())
    registry._load_jobs()
    yield registry


def test_create_job_rejects_beyond_max_queued(isolated_registry, monkeypatch):
    """Once pending+running reaches MAX_QUEUED_JOBS, create_job raises
    JobLimitExceededError instead of accepting more work."""
    monkeypatch.setattr(isolated_registry, "MAX_QUEUED_JOBS", 3)
    for _ in range(3):
        isolated_registry.create_job()
    with pytest.raises(isolated_registry.JobLimitExceededError):
        isolated_registry.create_job()


def test_completed_jobs_do_not_count_against_queue(isolated_registry, monkeypatch):
    """Only pending+running count toward the cap; completed/failed/cancelled
    jobs are free slots so a long-running server is not permanently stuck."""
    monkeypatch.setattr(isolated_registry, "MAX_QUEUED_JOBS", 2)
    j1 = isolated_registry.create_job()
    j2 = isolated_registry.create_job()
    # Both slots are now pending -> a third must be refused.
    with pytest.raises(isolated_registry.JobLimitExceededError):
        isolated_registry.create_job()
    # Completing one frees a slot.
    isolated_registry.update_job(j1, status="completed")
    j3 = isolated_registry.create_job()
    assert j3 != j2


def test_running_jobs_count_against_queue(isolated_registry, monkeypatch):
    monkeypatch.setattr(isolated_registry, "MAX_QUEUED_JOBS", 1)
    j1 = isolated_registry.create_job()
    isolated_registry.update_job(j1, status="running")
    with pytest.raises(isolated_registry.JobLimitExceededError):
        isolated_registry.create_job()


def test_backtest_run_returns_429_when_queue_full(monkeypatch, tmp_path):
    """The /backtest/run router must translate JobLimitExceededError into 429
    so a flooding client is back-pressured rather than silently queued."""
    # Stub create_job to always raise, regardless of state.
    from apps.dashboard.backend.routers import backtest as bt_router

    def always_full():
        raise registry.JobLimitExceededError("queue full (test)")

    monkeypatch.setattr(bt_router, "create_job", always_full)
    with TestClient(app) as client:
        r = client.post(
            "/api/backtest/run",
            json={
                "strategy": "ema_cross",
                "exchange": "okx",
                "symbol": "BTC/USDT",
                "timeframe": "1h",
            },
        )
    assert r.status_code == 429
    assert "queue full" in r.json()["detail"]


def test_optimize_run_returns_429_when_queue_full(monkeypatch, tmp_path):
    """The /optimize/run router must translate JobLimitExceededError into 429."""
    from apps.dashboard.backend.routers import optimize as opt_router

    def always_full():
        raise registry.JobLimitExceededError("queue full (test)")

    monkeypatch.setattr(opt_router, "create_job", always_full)
    with TestClient(app) as client:
        r = client.post(
            "/api/optimize/run",
            json={
                "strategy": "ema_cross",
                "exchange": "okx",
                "symbol": "BTC/USDT",
                "timeframe": "1h",
                "mode": "grid",
            },
        )
    assert r.status_code == 429
    assert "queue full" in r.json()["detail"]
