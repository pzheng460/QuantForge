"""OAuth pending-state table must stay bounded (L10).

Repeated /auth/start calls used to grow ``_pending_states`` without any
eviction; the purge runs on every new handshake.
"""

from __future__ import annotations

from apps.dashboard.backend.routers import brokers


def test_purge_drops_expired_and_keeps_fresh(monkeypatch):
    expired = "expired-state"
    fresh = "fresh-state"
    monkeypatch.setattr(
        brokers,
        "_pending_states",
        {expired: (100.0, "trading"), fresh: (9_999_999_999.0, "trading")},
    )
    brokers._purge_pending_states(1_000.0)
    assert expired not in brokers._pending_states
    assert fresh in brokers._pending_states


def test_purge_clears_abnormal_capacity(monkeypatch):
    states = {
        f"state-{i}": (9_999_999_999.0, "trading") for i in range(10_001)
    }
    monkeypatch.setattr(brokers, "_pending_states", states)
    brokers._purge_pending_states(0.0)
    assert brokers._pending_states == {}
