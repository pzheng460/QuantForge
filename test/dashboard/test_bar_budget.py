"""Span / bar-count ceilings against the unbounded-date-range DoS vector.

Explicit start/end dates bypass the bounded period enum, so a request like
``start=1970-01-01 end=now timeframe=1m`` would otherwise page an unbounded
dataset. The model caps the absolute span; jobs.data caps the number of
timeframe ticks.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.dashboard.backend.jobs.data import check_bar_budget
from apps.dashboard.backend.models import BacktestRequest


def test_bar_budget_rejects_absolute_span():
    with pytest.raises(ValueError, match="at most"):
        check_bar_budget("1m", "1970-01-01", "2024-01-01")


def test_bar_budget_accepts_reasonable_spans():
    # A day of 1m bars and several years of 1d bars are both fine.
    check_bar_budget("1m", "2026-01-01", "2026-01-02")
    check_bar_budget("1d", "2020-01-01", "2026-01-01")


def test_request_model_rejects_over_span_dates():
    with pytest.raises(ValidationError, match="date range must not exceed"):
        BacktestRequest(
            strategy="ema_crossover",
            exchange="bitget",
            start_date="2000-01-01",
            end_date="2026-01-01",
        )


def test_request_model_still_accepts_long_coarse_span():
    # 5 years of daily bars passes the model (span bound is just the outer
    # ceiling; the timeframe-aware bar budget is what the job enforces).
    BacktestRequest(
        strategy="ema_crossover",
        exchange="bitget",
        timeframe="1d",
        start_date="2021-01-01",
        end_date="2026-01-01",
    )
