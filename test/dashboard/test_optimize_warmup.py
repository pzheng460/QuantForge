"""WFO/three-stage evaluation must warm up indicators, not cold-start.

Previously _run_wfo and _run_three_stage called _evaluate(..., warmup=0),
measuring metrics (sharpe, drawdown) from the very first bar of each window
before indicators (EMA, etc.) had settled — a cold-start bias. They now pass
a warmup clamped to the window length, consistent with grid mode's use of
``cutoff`` as warmup.
"""

from __future__ import annotations

from apps.dashboard.backend.jobs.optimize import _clamped_warmup
from apps.dashboard.backend.models import OptimizeRequest


def _req(**overrides) -> OptimizeRequest:
    base = dict(
        strategy="ema_cross",
        exchange="okx",
        symbol="BTC/USDT",
        timeframe="1h",
        mode="grid",
    )
    base.update(overrides)
    return OptimizeRequest(**base)


def test_clamped_warmup_uses_requested_value_when_window_fits():
    """When the window is larger than req.warmup_bars, the full warmup is used
    (matching grid mode's cutoff semantics)."""
    req = _req(warmup_bars=50)
    window = [0] * 200
    assert _clamped_warmup(req, window) == 50


def test_clamped_warmup_clamps_to_window_minus_one():
    """A window shorter than req.warmup_bars must clamp so at least one bar is
    actually evaluated (warmup must not consume the entire window)."""
    req = _req(warmup_bars=500)
    window = [0] * 30
    assert _clamped_warmup(req, window) == 29


def test_clamped_warmup_zero_for_single_bar_window():
    req = _req(warmup_bars=50)
    assert _clamped_warmup(req, [0]) == 0


def test_clamped_warmup_zero_for_empty_window():
    req = _req(warmup_bars=50)
    assert _clamped_warmup(req, []) == 0


def test_clamped_warmup_respects_zero_request():
    """warmup_bars=0 means no warmup is applied (a valid request)."""
    req = _req(warmup_bars=0)
    assert _clamped_warmup(req, [0] * 100) == 0
