"""Concurrency contract for the live-engine registry.

``list_engines``/``_save_state``/``emergency_halt_all`` iterate ``_engines``
and may run sync on the FastAPI threadpool, while ``start_engine``/
``delete_engine`` mutate it under ``_registry_lock``. Without holding the lock
during iteration a concurrent mutation raises
``RuntimeError: dictionary changed size during iteration``. These tests verify
the lock is acquired during iteration and that the returned snapshots are
independent of later mutations.
"""

from __future__ import annotations

import threading

import pytest

import apps.dashboard.backend.live_engines as live_engines


class _TrackingLock:
    """A lock wrapper that records whether it was held during a critical
    section. Behaves as a non-reentrant mutex (mirrors threading.Lock)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.held_during: list[bool] = []  # set True while a watched op runs
        self._watching = False

    def __enter__(self):
        self._lock.acquire()
        if self._watching:
            self.held_during.append(True)

    def __exit__(self, *exc):
        self._lock.release()

    def watch(self, flag: bool):
        self._watching = flag


def _entry(eid: str = "e1", **overrides) -> dict:
    entry = {
        "engine_id": eid,
        "engine": None,
        "task": None,
        "status": "stopped",
        "strategy": eid,
        "exchange": "okx",
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1h",
        "demo": True,
        "leverage": 1,
        "position_size_usdt": 100.0,
        "warmup_bars": 50,
        "config_override": None,
        "risk_limits": {},
        "created_at": "2025-01-01T00:00:00+00:00",
        "stopped_at": None,
        "error": None,
    }
    entry.update(overrides)
    return entry


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(live_engines, "_engines", {})
    # Point persistence at a temp file and keep the REAL _save_state (do not
    # stub it) so its locked-iteration path is actually exercised.
    monkeypatch.setattr(
        live_engines, "_PERSIST_FILE", tmp_path / "engines.json"
    )
    yield


def test_list_engines_acquires_registry_lock(monkeypatch):
    """list_engines must hold _registry_lock while iterating _engines, so a
    concurrent start_engine/delete_engine cannot mutate it mid-iteration."""
    live_engines._engines.update(
        {f"e{i}": _entry(f"e{i}") for i in range(20)}
    )
    tracker = _TrackingLock()
    monkeypatch.setattr(live_engines, "_registry_lock", tracker)

    tracker.watch(True)
    live_engines.list_engines()
    tracker.watch(False)

    assert tracker.held_during, "list_engines did not acquire _registry_lock"


def test_save_state_acquires_registry_lock(monkeypatch):
    """_save_state must hold _registry_lock while iterating _engines."""
    live_engines._engines.update(
        {f"e{i}": _entry(f"e{i}") for i in range(20)}
    )
    tracker = _TrackingLock()
    monkeypatch.setattr(live_engines, "_registry_lock", tracker)

    tracker.watch(True)
    live_engines._save_state()
    tracker.watch(False)

    assert tracker.held_during, "_save_state did not acquire _registry_lock"
    assert live_engines._PERSIST_FILE.exists()


def test_list_engines_survives_concurrent_mutation(monkeypatch):
    """End-to-end: a mutation racing list_engines must not raise
    "dictionary changed size during iteration" — the snapshot is taken under
    _registry_lock."""
    live_engines._engines.update(
        {f"e{i}": _entry(f"e{i}") for i in range(50)}
    )
    errors: list[Exception] = []

    def mutator():
        # Bounded churn: add and remove a fixed number of entries while the
        # main thread iterates. Each op takes the lock, mirroring
        # start_engine/delete_engine.
        for i in range(50, 150):
            try:
                with live_engines._registry_lock:
                    live_engines._engines[f"e{i}"] = _entry(f"e{i}")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
                return

    t = threading.Thread(target=mutator)
    t.start()
    try:
        # list_engines must not blow up while the mutator churns the dict.
        for _ in range(100):
            try:
                live_engines.list_engines()
            except Exception as exc:
                errors.append(exc)
                break
    finally:
        t.join(timeout=10)

    assert not errors, f"unexpected exception during concurrent access: {errors}"


def test_list_engines_returns_copy_not_live_view():
    """The returned list must not be backed by the live _engines dict, so a
    later mutation does not change a previously returned snapshot."""
    live_engines._engines["e1"] = _entry("e1")
    snapshot = live_engines.list_engines()
    live_engines._engines["e2"] = _entry("e2")
    assert len(snapshot) == 1
    assert snapshot[0]["engine_id"] == "e1"
