"""Auto-registration and lookup for declarative strategies."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from quantforge.dsl.api import Strategy

# Global registry: name -> Strategy class
_REGISTRY: dict[str, type[Strategy]] = {}


def _register(cls: type[Strategy]) -> None:
    """Register a Strategy subclass (called by metaclass)."""
    name = getattr(cls, "name", "") or cls.__name__.lower()
    if name:
        _REGISTRY[name] = cls


def get_strategy(name: str) -> type[Strategy]:
    """Get a registered Strategy class by name.

    Raises KeyError if not found.
    """
    if name not in _REGISTRY:
        raise KeyError(
            f"Strategy '{name}' not found. Available: {', '.join(sorted(_REGISTRY))}"
        )
    return _REGISTRY[name]


def list_strategies() -> list[str]:
    """Return sorted list of registered strategy names."""
    return sorted(_REGISTRY.keys())
