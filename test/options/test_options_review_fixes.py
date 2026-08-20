"""Unit tests for the options-subsystem review fixes.

Covers:
  #1 pricing NaN guard
  #2 short-put assignment bypasses the cash guard
  #3 malformed schwab expirationDate no longer aborts the whole chain
  #5 _target_delta clamped to the live entry-delta band + candidate filtering
  #6 earnings_confirmed now drives a more conservative earnings buffer
  #7 backtest settlement emits ExpirationResult/Assignment domain events
  #8 backtest settle_pnl scales to actually-delivered shares
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

import pytest

from quantforge.domain.instruments import (
    AssetClass,
    Equity,
    EquityOption,
    InstrumentId,
    OptionRight,
)
from quantforge.domain.intents import OrderSide
from quantforge.options import OptionLifecycle
from quantforge.options.backtest import (
    ManagedCoveredCallConfig,
    _target_delta,
    run_managed_covered_call_approximation,
)
from quantforge.options.manager import (
    OptionCandidate,
    OptionManager,
    OptionManagerInput,
)
from quantforge.options.pricing import ApproximateOptionPricer
from quantforge.options.schwab import candidates_from_schwab_chain
from quantforge.portfolio import PortfolioLedger


# --------------------------------------------------------------------------- #
# Fix #1 — pricing NaN guard
# --------------------------------------------------------------------------- #
def _call_option() -> EquityOption:
    return EquityOption(
        id=InstrumentId("T", AssetClass.EQUITY_OPTION, "m"),
        expiration=date(2026, 8, 21),
        strike=100,
        right=OptionRight.CALL,
    )


@pytest.mark.parametrize(
    "spot, volatility",
    [
        (float("nan"), 0.3),
        (float("inf"), 0.3),
        (0.0, 0.3),
        (-1.0, 0.3),
        (100.0, float("nan")),
        (100.0, float("inf")),
        (100.0, 0.0),
        (100.0, -0.1),
    ],
)
def test_nan_or_nonpositive_inputs_are_rejected(spot, volatility):
    """NaN defeats <=/> comparisons (NaN <= 0 is False), so a NaN spot or
    volatility would slip past the positivity guard and yield a quote carrying
    NaN delta/mark/ask. Non-finite / non-positive inputs must raise."""
    pricer = ApproximateOptionPricer()
    with pytest.raises(ValueError):
        pricer.quote(
            _call_option(),
            spot=spot,
            valuation_date=date(2026, 1, 1),
            volatility=volatility,
        )


def test_nan_risk_free_rate_is_rejected():
    pricer = ApproximateOptionPricer()
    with pytest.raises(ValueError):
        pricer.quote(
            _call_option(),
            spot=100,
            valuation_date=date(2026, 1, 1),
            volatility=0.3,
            risk_free_rate=float("nan"),
        )


def test_finite_inputs_produce_finite_quote():
    pricer = ApproximateOptionPricer()
    quote = pricer.quote(
        _call_option(),
        spot=100,
        valuation_date=date(2026, 1, 1),
        volatility=0.3,
    )
    assert math.isfinite(quote.delta)
    assert math.isfinite(quote.mark)
    assert math.isfinite(quote.ask)
    assert math.isfinite(quote.bid)


# --------------------------------------------------------------------------- #
# Fix #2 — short-put assignment bypasses the cash guard
# --------------------------------------------------------------------------- #
def test_short_put_assignment_does_not_raise_insufficient_cash():
    """A short PUT assignment forces the holder to BUY the underlying at the
    strike — a mandatory, non-rejectable exercise event. The cash guard must
    NOT raise InsufficientCash even when the ledger cannot fund the purchase."""
    equity = Equity(InstrumentId("NVDA", AssetClass.EQUITY, "SCHWAB"))
    put = EquityOption(
        id=InstrumentId("NVDA_PUT", AssetClass.EQUITY_OPTION, "SCHWAB"),
        underlying=equity.id,
        expiration=date(2026, 8, 21),
        strike=200,
        right=OptionRight.PUT,
    )
    # Sell 1 put for a $5 credit; cash is far too small to buy 100 @ 200.
    ledger = PortfolioLedger(cash={"USD": 1000})
    ledger.apply_fill(put, OrderSide.SELL, 1, 5)

    # Put is ITM (spot 180 < strike 200) -> short put is assigned -> BUY 100.
    result = OptionLifecycle().expire(put, equity, 180, ledger)

    assert result.assignment is not None
    assert result.assignment.share_quantity == 100  # positive: shares bought in
    assert result.assignment.reason == "assignment"
    assert ledger.quantity(equity.id) == 100
    # Cash went negative (borrowed to fund the mandatory assignment) but no
    # InsufficientCash was raised — the event is non-rejectable.
    assert ledger.cash["USD"] < 0


def test_short_call_assignment_still_enforces_cash():
    """Only the short-PUT assignment (a forced BUY) bypasses the cash guard.
    A long CALL exercise (also a BUY) is a voluntary exercise the holder
    controls, so the cash guard must still apply there."""
    equity = Equity(InstrumentId("NVDA", AssetClass.EQUITY, "SCHWAB"))
    call = EquityOption(
        id=InstrumentId("NVDA_CALL", AssetClass.EQUITY_OPTION, "SCHWAB"),
        underlying=equity.id,
        expiration=date(2026, 8, 21),
        strike=200,
        right=OptionRight.CALL,
    )
    # Long 1 call (bought), cash too small to exercise into 100 @ 200.
    ledger = PortfolioLedger(cash={"USD": 1000})
    ledger.apply_fill(call, OrderSide.BUY, 1, 5)

    from quantforge.portfolio.ledger import InsufficientCash

    with pytest.raises(InsufficientCash):
        OptionLifecycle().expire(call, equity, 220, ledger)


# --------------------------------------------------------------------------- #
# Fix #3 — malformed schwab expirationDate no longer aborts the whole chain
# --------------------------------------------------------------------------- #
def _schwab_row(symbol: str, expiration: str, **overrides) -> dict:
    base = {
        "symbol": symbol,
        "strikePrice": 400.0,
        "expirationDate": expiration,
        "bid": 5.0,
        "ask": 5.2,
        "delta": 0.2,
        "openInterest": 1000,
        "totalVolume": 100,
    }
    base.update(overrides)
    return base


def _schwab_chain(rows: list[dict], right: str = "CALL") -> dict:
    # Schwab nests contracts as callExpDateMap[expiration][strike] -> [rows].
    # Group each row under its own strike so _contracts can walk the nesting.
    key = "callExpDateMap" if right.upper() == "CALL" else "putExpDateMap"
    by_expiration: dict[str, dict[str, list[dict]]] = {}
    for row in rows:
        expiration = str(row.get("expirationDate") or "")[:10]
        strike = str(row.get("strikePrice"))
        by_expiration.setdefault(expiration, {}).setdefault(strike, []).append(row)
    return {key: by_expiration}


def test_malformed_expiration_date_is_skipped_not_fatal():
    """A single malformed expirationDate (e.g. "2026/08/21" instead of ISO
    "2026-08-21") used to raise ValueError and abort the WHOLE chain —
    dropping every valid candidate after the bad row. The bad row is skipped
    and the valid rows around it are still parsed."""
    chain = _schwab_chain(
        [
            _schwab_row("GOOD_1", "2026-08-21"),
            _schwab_row("BAD", "2026/08/21"),  # slashes, not ISO
            _schwab_row("GOOD_2", "2026-09-18"),
        ]
    )
    candidates = candidates_from_schwab_chain(chain)
    symbols = [c.symbol for c in candidates]
    assert symbols == ["GOOD_1", "GOOD_2"]
    assert all(c.expiration == date(2026, 8, 21) or c.expiration == date(2026, 9, 18)
               for c in candidates)


def test_empty_or_missing_expiration_is_skipped():
    chain = _schwab_chain(
        [
            _schwab_row("GOOD", "2026-08-21"),
            _schwab_row("EMPTY", ""),
            _schwab_row("MISSING", "2026-13-99"),  # invalid month/day
        ]
    )
    candidates = candidates_from_schwab_chain(chain)
    assert [c.symbol for c in candidates] == ["GOOD"]


# --------------------------------------------------------------------------- #
# Fix #5 — _target_delta clamped to the live entry-delta band + filtering
# --------------------------------------------------------------------------- #
def test_target_delta_is_clamped_to_live_entry_band():
    """The mild-up target (0.14) and mild-down target (0.25) used to fall
    OUTSIDE the live OptionManager's [0.15, 0.22] entry band, so the backtest
    modeled a delta profile the live gates would reject. Every returned target
    must now lie inside [0.15, 0.22] (or be None for a strong uptrend)."""
    for ticker in ("TSLA", "NVDA"):
        for state in ("温和上涨", "横盘", "温和下跌"):
            target = _target_delta(ticker, state)
            assert target is not None
            assert 0.15 <= target <= 0.22, (ticker, state, target)
        # Strong uptrend still blocks (None), parity with the live manager.
        assert _target_delta(ticker, "强势上涨") is None


def test_backtest_filters_candidates_outside_delta_band():
    """Modeled candidates whose delta falls outside [delta_min, delta_max] are
    filtered out — when no strike lands in the band the entry is blocked
    (delta_band_block) instead of opening a profile the live gates reject."""
    # Very low volatility + a tight, high delta band that no modeled strike
    # can satisfy -> every open attempt hits delta_band_block.
    start = date(2025, 1, 1)
    rows = []
    price = 100.0
    for offset in range(360):
        day = start + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        rows.append(
            [int(datetime.combine(day, datetime.min.time(),
                                  tzinfo=timezone.utc).timestamp() * 1000),
             price, price + 1, price - 1, price, 1000]
        )
    calendar = tuple(date(2025, 1, 1) + timedelta(days=91 * q) for q in range(6))
    result = run_managed_covered_call_approximation(
        "NVDA",
        rows,
        initial_capital=100_000,
        config=ManagedCoveredCallConfig(delta_min=0.21, delta_max=0.22),
        earnings_dates=calendar,
    )
    assert result.action_counts.get("delta_band_block", 0) > 0
    assert result.action_counts.get("open_covered_call", 0) == 0


# --------------------------------------------------------------------------- #
# Fix #6 — earnings_confirmed drives a more conservative earnings buffer
# --------------------------------------------------------------------------- #
def _earnings_candidate(today: date) -> OptionCandidate:
    return OptionCandidate(
        symbol="TSLA_CALL",
        strike=400,
        expiration=today + timedelta(days=30),  # DTE 30, within 21-45
        bid=5,
        ask=5.2,
        delta=0.19,
        open_interest=1000,
        volume=100,
    )


def test_unconfirmed_earnings_date_is_more_conservative():
    """An unconfirmed earnings date is speculative and can drift, so the
    manager widens the earnings buffer. A candidate that is safe against a
    CONFIRMED date (earnings 40d out, buffer 7d -> 40 > 30+7=37 -> open) is
    blocked when the same date is UNCONFIRMED (buffer doubled to 14d ->
    40 <= 30+14=44 -> block)."""
    today = date(2026, 7, 23)
    earnings = today + timedelta(days=40)

    confirmed = OptionManager().evaluate(
        OptionManagerInput(
            ticker="TSLA",
            as_of=today,
            shares=200,
            minimum_core_shares=100,
            maximum_covered_ratio=0.5,
            stock_price=350,
            trend_state="横盘",
            earnings_date=earnings,
            earnings_confirmed=True,
            candidates=(_earnings_candidate(today),),
        )
    )
    unconfirmed = OptionManager().evaluate(
        OptionManagerInput(
            ticker="TSLA",
            as_of=today,
            shares=200,
            minimum_core_shares=100,
            maximum_covered_ratio=0.5,
            stock_price=350,
            trend_state="横盘",
            earnings_date=earnings,
            earnings_confirmed=False,
            candidates=(_earnings_candidate(today),),
        )
    )
    assert confirmed.action == "开 Covered Call"
    assert unconfirmed.action == "不操作"
    assert "财报" in unconfirmed.reasons[0]


def test_confirmed_earnings_behavior_unchanged():
    """When earnings_confirmed=True the manager behaves exactly as before this
    fix (the existing tests and API all pass confirmed=True), so a candidate
    far enough from earnings still opens."""
    today = date(2026, 7, 23)
    decision = OptionManager().evaluate(
        OptionManagerInput(
            ticker="TSLA",
            as_of=today,
            shares=200,
            minimum_core_shares=100,
            maximum_covered_ratio=0.5,
            stock_price=350,
            trend_state="横盘",
            earnings_date=today + timedelta(days=90),
            earnings_confirmed=True,
            candidates=(_earnings_candidate(today),),
        )
    )
    assert decision.action == "开 Covered Call"


# --------------------------------------------------------------------------- #
# Fix #7 & #8 — backtest settlement emits domain events and scales settle_pnl
# --------------------------------------------------------------------------- #
def _assignment_bars() -> list[list]:
    """Bars that drive one assignment: a covered call opened on a flat price
    finishes ITM only on its expiration bar (so no delta-trigger roll/close
    fires first), then price reverts."""
    start = date(2025, 1, 1)
    rows = []
    for offset in range(600):
        day = start + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        bi = len(rows)
        if bi < 219:
            p = 100.0
        elif bi < 220:
            p = 110.0  # expiration bar: ITM -> assignment
        else:
            p = 100.0  # revert
        rows.append(
            [int(datetime.combine(day, datetime.min.time(),
                                  tzinfo=timezone.utc).timestamp() * 1000),
             p, p + 1, p - 1, p, 1000]
        )
    return rows


def test_backtest_settlement_emits_assignment_domain_event():
    """The backtest must emit the SAME domain event the live OptionLifecycle
    path produces: an ExpirationResult wrapping an Assignment when the short
    call is ITM at expiration, with the correct signed share quantity."""
    calendar = tuple(date(2025, 1, 1) + timedelta(days=91 * q) for q in range(14))
    result = run_managed_covered_call_approximation(
        "NVDA",
        _assignment_bars(),
        initial_capital=100_000,
        config=ManagedCoveredCallConfig(profit_take_pct=0.95, roll_delta=0.80),
        earnings_dates=calendar,
    )
    assignment_events = [e for e in result.events if e.assignment is not None]
    assert len(assignment_events) >= 1
    assert result.assignments >= 1

    event = assignment_events[0]
    assignment = event.assignment
    assert assignment is not None
    # Short covered-call assignment SELLS shares out (negative signed qty),
    # matching lifecycle.py's sign for a short call.
    assert assignment.share_quantity < 0
    assert assignment.strike > 0
    assert assignment.reason == "assignment"
    assert assignment.option_quantity > 0


def test_backtest_settlement_emits_expiration_event_when_otm():
    """An OTM expiration emits an ExpirationResult with no assignment."""
    start = date(2025, 1, 1)
    rows = []
    price = 100.0
    for offset in range(500):
        day = start + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        rows.append(
            [int(datetime.combine(day, datetime.min.time(),
                                  tzinfo=timezone.utc).timestamp() * 1000),
             price, price + 1, price - 1, price, 1000]
        )
    calendar = tuple(date(2025, 1, 1) + timedelta(days=91 * q) for q in range(14))
    result = run_managed_covered_call_approximation(
        "NVDA",
        rows,
        initial_capital=100_000,
        config=ManagedCoveredCallConfig(profit_take_pct=0.95, roll_delta=0.80),
        earnings_dates=calendar,
    )
    expiry_events = [e for e in result.events if e.assignment is None]
    assert len(expiry_events) >= 1
    assert all(e.expired_contracts > 0 for e in expiry_events)


def test_settle_pnl_scales_to_actually_delivered_shares():
    """settle_pnl must be scaled to the shares actually delivered
    (assigned_shares = min(shares, contracts*100)), not the full contracts.
    When fully covered (shares >= contracts*100) the delivered ratio is 1.0
    and settle_pnl equals the full-contract formula; the trade fee is scaled
    by the same ratio so a partially-naked settlement is not over-credited."""
    calendar = tuple(date(2025, 1, 1) + timedelta(days=91 * q) for q in range(14))
    result = run_managed_covered_call_approximation(
        "NVDA",
        _assignment_bars(),
        initial_capital=100_000,
        config=ManagedCoveredCallConfig(profit_take_pct=0.95, roll_delta=0.80),
        earnings_dates=calendar,
    )
    # Find the assigned trade (exit_price > 0 at expiration).
    assigned_trades = [
        t for t in result.result.trades if t.exit_price > 0 and t.pnl < 0
    ]
    assert assigned_trades, "expected at least one assigned settlement trade"
    trade = assigned_trades[0]
    # Fully covered: shares (1000) >= contracts*100, so every contracted share
    # is delivered and the fee equals the full entry cost (ratio 1.0).
    assert trade.fee == pytest.approx(
        ManagedCoveredCallConfig().option_fee_per_contract
        * (trade.quantity / 100)
    )
