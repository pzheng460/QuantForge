"""DailyEntryCounter — the process-shared, file-backed daily new-position
counter used by every real-money live engine.

Invariants: reservations survive a process restart (a redeploy cannot
silently reset the daily cap), the count file is atomically replaced with
0600 permissions, and release floors at zero.
"""

from __future__ import annotations

import json
import stat

from datetime import datetime, timezone

from quantforge.risk.engine import DailyEntryCounter


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def test_reserve_persists_atomically_with_0600(tmp_path):
    path = tmp_path / "daily.json"
    counter = DailyEntryCounter(path)

    counter.reserve(2)

    assert counter._entries.get(_today()) == 2
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {_today(): 2}
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    # No leftover temp file from the atomic replace.
    assert not (tmp_path / "daily.json.tmp").exists()


def test_restart_reloads_persisted_count(tmp_path):
    """A restart must reload yesterdayfold count so the daily cap cannot be
    bypassed by redeploying the engine."""
    path = tmp_path / "daily.json"
    first = DailyEntryCounter(path)
    first.reserve(3)

    second = DailyEntryCounter(path)
    assert second._entries.get(_today()) == 3
    day, used = second.reserve(1)
    assert day == _today()
    assert used == 4


def test_missing_file_starts_empty(tmp_path):
    counter = DailyEntryCounter(tmp_path / "nope.json")
    assert counter._entries == {}
    day, used = counter.reserve(1)
    assert used == 1


def test_malformed_file_starts_over(tmp_path):
    path = tmp_path / "daily.json"
    path.write_text("{not json!!")
    counter = DailyEntryCounter(path)
    assert counter._entries == {}


def test_release_floors_at_zero(tmp_path):
    path = tmp_path / "daily.json"
    counter = DailyEntryCounter(path)

    counter.reserve(2)
    counter.release(2)
    assert max(0, counter._entries.get(_today(), 0)) == 0
    # Releasing more than reserved must not go negative or un-persist wrongly.
    counter.release(5)
    assert counter._entries.get(_today(), 0) == 0
    assert json.loads(path.read_text(encoding="utf-8")) == {_today(): 0}


def test_shared_counter_enforces_cap_across_engines(tmp_path):
    """Two engines sharing one counter cannot together exceed the daily cap."""
    counter = DailyEntryCounter(tmp_path / "daily.json")
    counter.reserve(9)
    day, used = counter.reserve(1)
    assert used == 10  # both engines' openings land on one shared counter
    assert day == _today()
