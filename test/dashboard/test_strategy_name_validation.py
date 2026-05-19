"""Tests for the strategy_name contract used by Evolving Mode.

The contract: any strategy passed to ``bot evolving enable --strategy X`` or
read by Pine engine startup MUST correspond to a real .pine file. Otherwise
the gate silently never fires because TradingControl is keyed by a name no
one else recognises.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from quantforge import evolving


def test_known_strategy_names_returns_real_pine_files():
    names = evolving.known_strategy_names()
    assert len(names) > 0, "expected at least one .pine file"
    assert "ema_crossover" in names, "ema_crossover.pine should be present"
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


def test_bot_evolving_enable_rejects_unknown_strategy(monkeypatch, tmp_path):
    """The CLI should refuse to flip the switch on a typo'd strategy."""
    from quantforge.cli.main import cli

    monkeypatch.setattr(evolving, "STATE_PATH", tmp_path / "evolving.json")
    runner = CliRunner()
    result = runner.invoke(cli, ["bot", "evolving", "enable", "--strategy", "ema_cross", "--no-cron"])
    assert result.exit_code != 0
    assert "Unknown strategy 'ema_cross'" in result.output
    # Did not write the state file
    assert not (tmp_path / "evolving.json").exists() or "ema_cross" not in (tmp_path / "evolving.json").read_text()


def test_bot_evolving_enable_accepts_known_strategy(monkeypatch, tmp_path):
    from quantforge.cli.main import cli

    monkeypatch.setattr(evolving, "STATE_PATH", tmp_path / "evolving.json")
    runner = CliRunner()
    result = runner.invoke(cli, ["bot", "evolving", "enable", "--strategy", "ema_crossover", "--no-cron"])
    assert result.exit_code == 0
    assert "Evolving Mode is now ON" in result.output
