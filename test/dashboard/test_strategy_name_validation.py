"""Tests for registered Python strategy names used by Evolving Mode."""

from __future__ import annotations

import pytest

from quantforge import evolving


def test_known_strategy_names_returns_registered_python_strategies():
    names = evolving.known_strategy_names()
    assert len(names) > 0
    assert "ema_crossover" in names
    # Sorted, unique
    assert names == sorted(set(names))


def test_validate_strategy_name_accepts_known():
    assert evolving.validate_strategy_name("ema_crossover") == "ema_crossover"


def test_validate_strategy_name_rejects_unknown_with_suggestion():
    with pytest.raises(evolving.UnknownStrategyError) as exc_info:
        evolving.validate_strategy_name("ema_cross")  # typo: missing "over"
    err = exc_info.value
    assert err.name == "ema_cross"
    # Should suggest the closest match (prefix-based)
    assert "ema_crossover" in err.suggestions


def test_validate_strategy_name_empty_suggestion_list_for_alien_name():
    with pytest.raises(evolving.UnknownStrategyError) as exc_info:
        evolving.validate_strategy_name("totally_made_up_xyzzy")
    # No close match for a random alien name → suggestions can be empty
    assert exc_info.value.suggestions == []
