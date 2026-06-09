from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from apps.dashboard.backend.main import app
from apps.dashboard.backend import jobs


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _payload(**overrides):
    base = {
        "pine_source": 'strategy("Validation")',
        "exchange": "bitget",
        "symbol": "BTC/USDT:USDT",
        "timeframe": "1h",
        "start_date": "2026-01-01",
        "end_date": "2026-01-02",
        "warmup_bars": 50,
    }
    base.update(overrides)
    return base


def test_backtest_rejects_negative_warmup_bars(client):
    r = client.post("/api/backtest/run", json=_payload(warmup_bars=-1))
    assert r.status_code == 422


def test_backtest_rejects_unsupported_timeframe(client):
    r = client.post("/api/backtest/run", json=_payload(timeframe="2m"))
    assert r.status_code == 422


def test_backtest_rejects_start_date_on_or_after_end_date(client):
    r = client.post(
        "/api/backtest/run",
        json=_payload(start_date="2026-01-02", end_date="2026-01-02"),
    )
    assert r.status_code == 422


def test_backtest_rejects_future_start_date_without_explicit_end(client):
    payload = _payload(start_date="2999-01-01")
    del payload["end_date"]
    r = client.post("/api/backtest/run", json=payload)
    assert r.status_code == 422


def test_backtest_config_override_replaces_negative_input_defaults(
    monkeypatch,
    tmp_path,
):
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    (strategies_dir / "negative_input.pine").write_text(
        """
//@version=5
strategy("Negative Input")
roc_threshold = input.float(-2.0, title="ROC Threshold")
adx_threshold = input.int(-10, title="ADX Threshold")
""",
    )
    monkeypatch.setattr(jobs, "_STRATEGIES_DIR", strategies_dir)

    source = jobs._resolve_pine_source(
        strategy="negative_input",
        pine_source=None,
        config_override={"roc_threshold": 1.5, "adx_threshold": 20},
    )

    assert "roc_threshold = input.float(1.5," in source
    assert "adx_threshold = input.int(20," in source
    assert "input.float(-2.0" not in source
    assert "input.int(-10" not in source


def test_backtest_config_override_applies_to_raw_pine_source():
    source = jobs._resolve_pine_source(
        strategy=None,
        pine_source="""
//@version=5
strategy("Raw Override")
fast_period = input.int(5, title="Fast")
""",
        config_override={"fast_period": 9},
    )

    assert "fast_period = input.int(9," in source
    assert "input.int(5," not in source


def test_backtest_config_override_matches_exact_variable_name(
    monkeypatch,
    tmp_path,
):
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    (strategies_dir / "exact_input.pine").write_text(
        """
//@version=5
strategy("Exact Input")
myfast = input.int(3, title="My Fast")
fast = input.int(5, title="Fast")
""",
    )
    monkeypatch.setattr(jobs, "_STRATEGIES_DIR", strategies_dir)

    source = jobs._resolve_pine_source(
        strategy="exact_input",
        pine_source=None,
        config_override={"fast": 8},
    )

    assert "myfast = input.int(3," in source
    assert "fast = input.int(8," in source


def test_backtest_config_override_replaces_bool_and_string_inputs():
    source = jobs._resolve_pine_source(
        strategy=None,
        pine_source="""
//@version=5
strategy("Bool String Override")
use_filter = input.bool(false, title="Use Filter")
mode = input.string("fast", title="Mode")
""",
        config_override={"use_filter": True, "mode": "slow"},
    )

    assert "use_filter = input.bool(true," in source
    assert 'mode = input.string("slow",' in source
    assert "input.bool(false" not in source
    assert 'input.string("fast"' not in source


def test_backtest_config_override_replaces_defval_keyword_inputs():
    source = jobs._resolve_pine_source(
        strategy=None,
        pine_source="""
//@version=5
strategy("Kwarg Override")
fast_period = input.int(defval=5, title="Fast")
slow_period = input.int(title="Slow", defval=13)
use_filter = input.bool(title="Use Filter", defval=false)
mode = input.string(title="Mode", defval="fast")
""",
        config_override={
            "fast_period": 8,
            "slow_period": 21,
            "use_filter": True,
            "mode": "slow",
        },
    )

    assert "fast_period = input.int(defval=8," in source
    assert 'slow_period = input.int(title="Slow", defval=21)' in source
    assert 'use_filter = input.bool(title="Use Filter", defval=true)' in source
    assert 'mode = input.string(title="Mode", defval="slow")' in source
