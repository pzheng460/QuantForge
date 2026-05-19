"""Evolving Mode — global gate for the autonomous strategy-tune-and-deploy loop.

Evolving Mode is the umbrella flag for everything in the
``quantforge/{bot_cycle,auto_tune_scheduler,risk_control,trading_control,…}``
subsystem. When OFF (the default), the bot CLI commands refuse to run and the
live Pine engine ignores ``trading_control.json``. When ON, the cycle can
re-optimise, deploy, and override Pine engine behaviour (pause new entries,
reduce position size, etc.) per-strategy.

State lives in a single JSON file ``~/.quantforge/evolving.json``::

    {
        "enabled": false,
        "strategies": ["ema_crossover"],
        "updated_at": "2026-05-18T09:12:33Z"
    }

* ``enabled`` is the global master switch. If false, nothing in the bot
  subsystem activates regardless of per-strategy entries.
* ``strategies`` is the allow-list of strategy names that are under evolving
  control. Strategies *not* in this list are unaffected even when the master
  switch is on — they run as plain manual Pine engines.

Default behaviour is conservative: a fresh install has no file at all, which
``is_enabled()`` treats identically to ``{"enabled": false}``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, TypedDict


STATE_PATH = Path.home() / ".quantforge" / "evolving.json"

# Canonical Pine strategy directory — single source of truth for what strategy
# names are real. We resolve it from the package root so it follows the repo.
PINE_STRATEGIES_DIR = Path(__file__).resolve().parent / "pine" / "strategies"


def known_strategy_names() -> list[str]:
    """Return the strategy names that currently exist as ``.pine`` files."""
    if not PINE_STRATEGIES_DIR.exists():
        return []
    return sorted(p.stem for p in PINE_STRATEGIES_DIR.glob("*.pine"))


class UnknownStrategyError(ValueError):
    """Raised when a strategy name doesn't correspond to a real .pine file.

    The .suggestions attribute holds a short list of close matches the caller
    can surface to the user.
    """

    def __init__(self, name: str, suggestions: list[str]) -> None:
        self.name = name
        self.suggestions = suggestions
        hint = f"  Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        super().__init__(f"Unknown strategy '{name}'." + hint)


def validate_strategy_name(name: str) -> str:
    """Verify ``name`` matches an on-disk .pine file. Returns the name on success.

    Raises :class:`UnknownStrategyError` with close-match suggestions if not.
    """
    known = known_strategy_names()
    if name in known:
        return name
    # Suggest close matches (case-insensitive substring + prefix priority).
    lower = name.lower()
    prefix = [k for k in known if k.lower().startswith(lower)]
    substring = [k for k in known if lower in k.lower() and k not in prefix]
    suggestions = (prefix + substring)[:3]
    raise UnknownStrategyError(name, suggestions)


class EvolvingState(TypedDict):
    enabled: bool
    strategies: List[str]
    updated_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_state() -> EvolvingState:
    return {"enabled": False, "strategies": [], "updated_at": _now_iso()}


def load_state() -> EvolvingState:
    """Read the current state. Missing file → conservative default."""
    if not STATE_PATH.exists():
        return _default_state()
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_state()
    return {
        "enabled": bool(raw.get("enabled", False)),
        "strategies": [str(s) for s in raw.get("strategies", [])],
        "updated_at": str(raw.get("updated_at", _now_iso())),
    }


def _save_state(state: EvolvingState) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def is_enabled(strategy: str | None = None) -> bool:
    """Return True if evolving mode is active for the given strategy.

    With ``strategy=None``: returns the global master switch.
    With a strategy name: returns True only if both the master switch is on
    AND the strategy is on the allow-list (or the allow-list is empty,
    interpreted as "applies to all" — useful for one-strategy setups).
    """
    state = load_state()
    if not state["enabled"]:
        return False
    if strategy is None:
        return True
    if not state["strategies"]:
        # Empty allow-list when enabled = apply to all subscribed strategies.
        return True
    return strategy in state["strategies"]


def enable(strategies: Iterable[str] | None = None) -> EvolvingState:
    """Turn on the master switch. Optionally seed the strategy allow-list."""
    state = load_state()
    state["enabled"] = True
    if strategies is not None:
        state["strategies"] = sorted(set(strategies))
    state["updated_at"] = _now_iso()
    _save_state(state)
    return state


def disable() -> EvolvingState:
    """Turn off the master switch. Does not clear the allow-list."""
    state = load_state()
    state["enabled"] = False
    state["updated_at"] = _now_iso()
    _save_state(state)
    return state


def add_strategy(name: str) -> EvolvingState:
    state = load_state()
    if name not in state["strategies"]:
        state["strategies"].append(name)
        state["strategies"].sort()
    state["updated_at"] = _now_iso()
    _save_state(state)
    return state


def remove_strategy(name: str) -> EvolvingState:
    state = load_state()
    state["strategies"] = [s for s in state["strategies"] if s != name]
    state["updated_at"] = _now_iso()
    _save_state(state)
    return state
