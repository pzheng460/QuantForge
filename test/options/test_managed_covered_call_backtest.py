from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from quantforge.options.backtest import (
    ManagedCoveredCallConfig,
    run_managed_covered_call_approximation,
)


def _bars(*, days: int = 280, daily_change: float = 0.0) -> list[list]:
    start = date(2025, 1, 1)
    rows = []
    price = 100.0
    for offset in range(days):
        day = start + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        price += daily_change
        timestamp = int(
            datetime.combine(
                day,
                datetime.min.time(),
                tzinfo=timezone.utc,
            ).timestamp()
            * 1000
        )
        rows.append([timestamp, price, price + 1, price - 1, price, 1000])
    return rows


@pytest.mark.critical
def test_managed_backtest_is_deterministic_auditable_and_respects_coverage():
    bars = _bars()
    config = ManagedCoveredCallConfig(
        minimum_core_shares=600,
        maximum_covered_ratio=0.50,
        stock_fee_per_share=0.01,
        option_fee_per_contract=0.65,
    )

    first = run_managed_covered_call_approximation(
        "TSLA",
        bars,
        initial_capital=100_000,
        config=config,
        # A known earnings date just past the first open attempt (200 closes,
        # ~200 weekdays from 2025-01-01 ≈ 2025-10-07) must block that entry.
        earnings_dates=(date(2025, 10, 20),),
    )
    second = run_managed_covered_call_approximation(
        "TSLA",
        bars,
        initial_capital=100_000,
        config=config,
        earnings_dates=(date(2025, 10, 20),),
    )

    assert first == second
    assert first.quality == "approximate_unvalidated"
    assert first.strategy_version == "managed_cc_v1_default_unvalidated"
    assert first.max_contracts_open <= 4
    assert first.action_counts["earnings_block"] > 0


def test_unknown_earnings_date_blocks_entries_like_live():
    """Parity with the live OptionManager: when the next earnings date is
    unknown (no calendar / exhausted calendar) the manager refuses ALL new
    covered calls ('缺少财报日期'); the backtest must block the same way."""
    bars = _bars()
    result = run_managed_covered_call_approximation(
        "NVDA",
        bars,
        initial_capital=100_000,
        config=ManagedCoveredCallConfig(),
    )

    assert result.action_counts.get("open_covered_call", 0) == 0
    assert result.action_counts.get("earnings_block", 0) > 0

    # With a calendar covering the period the same bars DO open — proving the
    # block is specifically the unknown-earnings semantics, not data shape.
    calendar = tuple(
        date(2025, 1, 1) + timedelta(days=91 * q) for q in range(6)
    )
    opened = run_managed_covered_call_approximation(
        "NVDA",
        bars,
        initial_capital=100_000,
        config=ManagedCoveredCallConfig(),
        earnings_dates=calendar,
    )
    assert opened.action_counts.get("open_covered_call", 0) > 0


def test_transaction_costs_cannot_improve_managed_backtest_equity():
    bars = _bars(daily_change=0.0)
    calendar = tuple(
        date(2025, 1, 1) + timedelta(days=91 * q) for q in range(6)
    )
    free = run_managed_covered_call_approximation(
        "NVDA",
        bars,
        initial_capital=100_000,
        config=ManagedCoveredCallConfig(),
        earnings_dates=calendar,
    )
    costly = run_managed_covered_call_approximation(
        "NVDA",
        bars,
        initial_capital=100_000,
        config=ManagedCoveredCallConfig(
            stock_fee_per_share=0.01,
            option_fee_per_contract=0.65,
        ),
        earnings_dates=calendar,
    )

    assert costly.total_costs > 0
    assert costly.final_equity < free.final_equity


def test_delta_breach_rolls_to_viable_replacement_like_live():
    """Parity with the live OptionManager (manager.py:159-173): a delta breach
    ROLLS to a viable replacement (ROLL_COVERED_CALL — atomic close+reopen)
    when one passes the earnings/DTE/delta gates, instead of always closing
    and holding flat. The prior comment "live never rolls" was wrong — the
    live manager does roll up when a viable replacement exists."""
    # Flat trend (allows opens) with one sharp mid-period rally that pushes the
    # open short call ITM (delta breach) before the price reverts.
    start = date(2025, 1, 1)
    rows = []
    price = 100.0
    for offset in range(420):
        day = start + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        if 300 <= offset < 306:
            price += 4.0
        elif 306 <= offset < 310:
            price -= 2.0
        timestamp = int(
            datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000
        )
        rows.append([timestamp, price, price + 1, price - 1, price, 1000])

    calendar = tuple(
        date(2025, 1, 1) + timedelta(days=91 * q) for q in range(8)
    )
    result = run_managed_covered_call_approximation(
        "NVDA",
        rows,
        initial_capital=500_000,
        config=ManagedCoveredCallConfig(),
        earnings_dates=calendar,
    )

    # A delta breach with a viable replacement now ROLLS (close+reopen),
    # mirroring the live ROLL_COVERED_CALL.
    assert result.action_counts.get("roll", 0) > 0
    assert result.rolls > 0
    assert "roll_up" not in result.action_counts
    # Closing is only interesting if the overlay actually traded afterwards.
    assert result.option_trades >= 2


def test_delta_breach_without_replacement_closes_and_holds():
    """When a delta breach has NO viable replacement (earnings date too near
    for the roll replacement to clear its gate), the backtest falls back to
    the live manager's CLOSE_AND_HOLD — buy back and hold flat, like the live
    OptionManager does when _viable_candidates returns empty."""
    # Flat trend (allows opens) with one sharp mid-period rally that pushes the
    # open short call ITM (delta breach) before the price reverts.
    start = date(2025, 1, 1)
    rows = []
    price = 100.0
    for offset in range(420):
        day = start + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        if 300 <= offset < 306:
            price += 4.0
        elif 306 <= offset < 310:
            price -= 2.0
        timestamp = int(
            datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000
        )
        rows.append([timestamp, price, price + 1, price - 1, price, 1000])

    result = run_managed_covered_call_approximation(
        "NVDA",
        rows,
        initial_capital=500_000,
        # A very high profit_take so the breach is decided by the delta
        # trigger, not by an early profit-taking close.
        config=ManagedCoveredCallConfig(profit_take_pct=0.95),
        # An earnings date near the spike (day 320, ~20d from the day-300
        # breach) falls inside the roll replacement's dte+buffer window, so
        # the replacement gate blocks and the breach must close-and-hold.
        earnings_dates=(
            date(2025, 1, 1) + timedelta(days=91),
            date(2025, 1, 1) + timedelta(days=182),
            date(2025, 1, 1) + timedelta(days=273),
            date(2025, 1, 1) + timedelta(days=320),
            date(2025, 1, 1) + timedelta(days=400),
        ),
    )

    assert result.action_counts.get("delta_close", 0) > 0
    assert result.rolls == 0


def test_strong_uptrend_does_not_open_new_covered_calls():
    bars = _bars(days=420, daily_change=0.5)

    result = run_managed_covered_call_approximation(
        "NVDA",
        bars,
        initial_capital=500_000,
        earnings_dates=(date(2026, 1, 1),),
        evaluation_start=date(2025, 1, 1),
    )

    assert result.action_counts["strong_uptrend_block"] > 0
    assert result.action_counts.get("open_covered_call", 0) == 0


def test_strong_downtrend_still_opens_like_live():
    """The live OptionManager blocks ONLY 强势上涨 ('不主动封顶收益'); a strong
    downtrend still opens covered calls (short calls are naturally bearish).
    The backtest must match instead of downgrading to downside_review."""
    bars = _bars(days=420, daily_change=-0.2)
    calendar = tuple(
        date(2025, 1, 1) + timedelta(days=91 * q) for q in range(6)
    )

    result = run_managed_covered_call_approximation(
        "NVDA",
        bars,
        initial_capital=500_000,
        config=ManagedCoveredCallConfig(),
        earnings_dates=calendar,
    )

    assert result.action_counts.get("downside_review", 0) == 0
    assert result.action_counts.get("open_covered_call", 0) > 0
