"""Remaining real-money risk gates: option nakedness, quote freshness/spread,
leverage caps (global + instrument), order notional (incl. option multiplier),
max option legs, and daily-entry rollback."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from quantforge.domain.instruments import (
    AssetClass,
    CryptoPerpetual,
    Equity,
    EquityOption,
    InstrumentId,
    OptionRight,
)
from quantforge.domain.intents import (
    MultiLegOrderIntent,
    OrderIntent,
    OrderSide,
)
from quantforge.portfolio.ledger import PortfolioLedger, Position
from quantforge.risk.engine import RiskEngine, RiskLimits, RiskRejected

_NVDA = InstrumentId("NVDA", AssetClass.EQUITY, "schwab")
_TSLA = InstrumentId("TSLA", AssetClass.EQUITY, "schwab")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _equity() -> Equity:
    return Equity(_TSLA)


def _nvda_equity() -> Equity:
    return Equity(_NVDA)


def _options(side: OrderSide, right: OptionRight, qty: float = 1):
    return EquityOption(
        id=InstrumentId(
            "NVDA  260821C00200000", AssetClass.EQUITY_OPTION, "schwab"
        ),
        underlying=_NVDA,
        expiration=date(2026, 8, 21),
        strike=200.0,
        right=right,
    )


def _intent(
    *,
    instrument=None,
    side=OrderSide.BUY,
    quantity: float = 1,
    reduce_only: bool = False,
    leverage: float = 1,
    quote=None,
    limit_price: float | None = None,
) -> OrderIntent:
    instrument = instrument or _equity()
    return OrderIntent(
        strategy_id="test",
        instrument=instrument,
        side=side,
        quantity=quantity,
        reduce_only=reduce_only,
        leverage=leverage,
        quote_bid=quote[0] if quote else None,
        quote_ask=quote[1] if quote else None,
        quote_timestamp=quote[2] if quote else None,
        limit_price=limit_price,
    )


def _engine(limits: RiskLimits | None = None) -> RiskEngine:
    return RiskEngine(
        limits or RiskLimits(live_enabled=True, max_daily_new_positions=10)
    )


def _ledger(*positions) -> PortfolioLedger:
    ledger = PortfolioLedger(cash={"USD": 1_000_000})
    for instrument, qty, price in positions:
        ledger.positions[instrument.id] = Position(instrument, qty, price)
    return ledger


# ─── Option nakedness ────────────────────────────────────────────────────────

def test_naked_call_is_rejected():
    intent = _intent(instrument=_options(OrderSide.SELL, OptionRight.CALL), side=OrderSide.SELL)
    with pytest.raises(RiskRejected, match="naked call"):
        _engine().authorize(intent, _ledger())


def test_short_call_covered_by_shares_is_authorized():
    intent = _intent(instrument=_options(OrderSide.SELL, OptionRight.CALL), side=OrderSide.SELL)
    ledger = _ledger((_nvda_equity(), 100, 200))  # 1 call × 100 multiplier covered
    _engine().authorize(intent, ledger)


def test_short_call_covered_by_one_share_is_rejected():
    intent = _intent(instrument=_options(OrderSide.SELL, OptionRight.CALL), side=OrderSide.SELL)
    ledger = _ledger((_nvda_equity(), 1, 200))
    with pytest.raises(RiskRejected, match="naked call"):
        _engine().authorize(intent, ledger)


def test_short_call_covered_by_long_call_is_authorized():
    short = _intent(instrument=_options(OrderSide.SELL, OptionRight.CALL), side=OrderSide.SELL)
    long_leg = _intent(instrument=_options(OrderSide.BUY, OptionRight.CALL), side=OrderSide.BUY)
    plan = MultiLegOrderIntent(
        strategy_id="test", net_limit_price=-2.5, legs=(long_leg, short)
    )
    _engine().authorize(plan, _ledger())


def test_uncovered_short_put_is_rejected_without_cash():
    put = _options(OrderSide.SELL, OptionRight.PUT)  # strike 200 × 100 = 20_000
    intent = _intent(instrument=put, side=OrderSide.SELL)
    ledger = _ledger()
    ledger.cash["USD"] = 5_000
    with pytest.raises(RiskRejected, match="uncovered short put"):
        _engine().authorize(intent, ledger)


def test_uncovered_short_put_authorized_with_sufficient_cash():
    put = _options(OrderSide.SELL, OptionRight.PUT)
    intent = _intent(instrument=put, side=OrderSide.SELL)
    ledger = _ledger()
    ledger.cash["USD"] = 50_000
    _engine().authorize(intent, ledger)


def test_short_put_covered_by_long_put_is_authorized():
    short = _intent(instrument=_options(OrderSide.SELL, OptionRight.PUT), side=OrderSide.SELL)
    long_leg = _intent(instrument=_options(OrderSide.BUY, OptionRight.PUT), side=OrderSide.BUY)
    plan = MultiLegOrderIntent(
        strategy_id="test", net_limit_price=-2.5, legs=(long_leg, short)
    )
    _engine().authorize(plan, _ledger())


# ─── Quote freshness and spread ──────────────────────────────────────────────

def test_stale_quote_is_rejected():
    old = _now() - timedelta(hours=1)
    intent = _intent(side=OrderSide.BUY, quote=(100, 101, old))
    engine = _engine(RiskLimits(live_enabled=True, require_fresh_quote=True))
    with pytest.raises(RiskRejected, match="stale"):
        engine.authorize(intent, _ledger())


def test_future_quote_is_rejected():
    future = _now() + timedelta(minutes=5)
    intent = _intent(side=OrderSide.BUY, quote=(100, 101, future))
    engine = _engine(RiskLimits(live_enabled=True, require_fresh_quote=True))
    with pytest.raises(RiskRejected, match="stale"):
        engine.authorize(intent, _ledger())


def test_missing_quote_is_rejected():
    intent = _intent(side=OrderSide.BUY, quote=None)
    engine = _engine(RiskLimits(live_enabled=True, require_fresh_quote=True))
    with pytest.raises(RiskRejected, match="fresh quote"):
        engine.authorize(intent, _ledger())


def test_wide_spread_is_rejected():
    # (102 - 98) / 100 = 4% > default 15%? No — set a tight limit instead.
    intent = _intent(side=OrderSide.BUY, quote=(99, 101, _now()))
    engine = _engine(RiskLimits(live_enabled=True, max_spread_pct=0.01))
    with pytest.raises(RiskRejected, match="spread limit"):
        engine.authorize(intent, _ledger())


# ─── Operator override (manual orders may submit on the best available quote) ─

def _operator_intent(**overrides) -> OrderIntent:
    values = dict(
        strategy_id="manual",
        instrument=_equity(),
        side=OrderSide.SELL,
        quantity=19,
        limit_price=1.11,
        quote_bid=1.11,
        quote_ask=1.13,
        quote_timestamp=_now() - timedelta(hours=1),  # deliberately stale
        operator_override=True,
    )
    values.update(overrides)
    return OrderIntent(**values)


def test_operator_override_skips_stale_quote_gate():
    """A human-directed resting close may submit on the best available quote
    even when the market has not refreshed it (illiquid microcaps)."""
    intent = _operator_intent()
    engine = _engine(RiskLimits(live_enabled=True, require_fresh_quote=True))
    decision = engine.authorize(intent, _ledger((_equity(), 19, 1.11)))
    assert decision.allowed


def test_operator_override_still_enforces_notional():
    intent = _operator_intent(quantity=99_999, limit_price=100.0)
    engine = _engine(
        RiskLimits(
            live_enabled=True,
            require_fresh_quote=True,
            max_order_notional=10_000,
        )
    )
    with pytest.raises(RiskRejected, match="notional"):
        engine.authorize(intent, _ledger())


def test_operator_override_still_enforces_spread():
    intent = _operator_intent(quote_bid=50, quote_ask=51)
    engine = _engine(
        RiskLimits(live_enabled=True, require_fresh_quote=True, max_spread_pct=0.01)
    )
    with pytest.raises(RiskRejected, match="spread limit"):
        engine.authorize(intent, _ledger())


def test_operator_override_still_fails_closed_without_price_reference():
    """Waiving freshness never waives the notional reference: a MARKET intent
    with no price and no quote must still fail closed."""
    intent = _operator_intent(limit_price=None, quote_bid=None, quote_ask=None)
    engine = _engine(RiskLimits(live_enabled=True, require_fresh_quote=True))
    with pytest.raises(RiskRejected, match="no price reference"):
        engine.authorize(intent, _ledger())


def test_operator_override_logs_loud_warning(caplog):
    import logging

    intent = _operator_intent()
    engine = _engine(RiskLimits(live_enabled=True, require_fresh_quote=True))
    with caplog.at_level(logging.WARNING, logger="quantforge.risk.engine"):
        engine.authorize(intent, _ledger((_equity(), 19, 1.11)))
    assert any(
        "operator_override" in record.message for record in caplog.records
    )


# ─── Leverage caps ───────────────────────────────────────────────────────────

def test_global_leverage_cap_is_enforced():
    intent = _intent(side=OrderSide.BUY, leverage=5)
    engine = _engine(RiskLimits(live_enabled=True, max_leverage=3))
    with pytest.raises(RiskRejected, match="maximum leverage"):
        engine.authorize(intent, _ledger())


def test_instrument_leverage_cap_is_enforced():
    perp = CryptoPerpetual(
        id=InstrumentId("BTC/USDT:USDT", AssetClass.CRYPTO_PERPETUAL, "okx"),
        max_leverage=2,
    )
    intent = _intent(instrument=perp, side=OrderSide.BUY, leverage=3)
    with pytest.raises(RiskRejected, match="instrument leverage"):
        _engine().authorize(intent, _ledger())


# ─── Notional limits (incl. option multiplier) ───────────────────────────────

def test_notional_cap_uses_quote_ask():
    intent = _intent(side=OrderSide.BUY, quantity=1000, quote=(100, 100, _now()))
    engine = _engine(RiskLimits(live_enabled=True, max_order_notional=10_000))
    with pytest.raises(RiskRejected, match="notional"):
        engine.authorize(intent, _ledger())


def test_option_notional_applies_multiplier():
    # 1 option × 200 strike-style premium × 100 multiplier = 20_000 > 10_000
    put = _options(OrderSide.BUY, OptionRight.PUT)
    intent = _intent(instrument=put, side=OrderSide.BUY, quote=(250, 250, _now()))
    engine = _engine(RiskLimits(live_enabled=True, max_order_notional=10_000))
    with pytest.raises(RiskRejected, match="notional"):
        engine.authorize(intent, _ledger())


# ─── Max option legs ─────────────────────────────────────────────────────────

def test_too_many_option_legs_is_rejected():
    legs = tuple(
        _intent(
            instrument=_options(OrderSide.BUY, OptionRight.CALL),
            side=OrderSide.BUY,
        )
        for _ in range(5)
    )
    plan = MultiLegOrderIntent(strategy_id="test", net_limit_price=1.0, legs=legs)
    engine = _engine(RiskLimits(live_enabled=True, max_option_legs=4))
    with pytest.raises(RiskRejected, match="too many option legs"):
        engine.authorize(plan, _ledger())


# ─── Daily new-position limit + rollback ─────────────────────────────────────

def test_daily_new_position_limit_exceeded_rolls_back():
    engine = _engine(RiskLimits(live_enabled=True, max_daily_new_positions=2))
    day = datetime.now(timezone.utc).date().isoformat()

    intent1 = _intent(side=OrderSide.BUY)
    intent2 = _intent(side=OrderSide.BUY)
    intent3 = _intent(side=OrderSide.BUY)

    engine.authorize(intent1, _ledger())
    engine.authorize(intent2, _ledger())
    with pytest.raises(RiskRejected, match="daily new-position limit"):
        engine.authorize(intent3, _ledger())

    # The failed reservation was rolled back: only 2 openings remain used.
    assert engine._local_entries.get(day) == 2
    # A retry is STILL rejected — the reservation is released, yes, but the
    # cap is a hard ceiling (2 openings), not 3.
    with pytest.raises(RiskRejected, match="daily new-position limit"):
        engine.authorize(intent3, _ledger())
    assert engine._local_entries.get(day) == 2


def test_released_definitive_rejection_frees_daily_count():
    engine = _engine(RiskLimits(live_enabled=True, max_daily_new_positions=1))
    day = datetime.now(timezone.utc).date().isoformat()

    intent = _intent(side=OrderSide.BUY)
    engine.authorize(intent, _ledger())
    engine.release(intent)

    assert engine._local_entries.get(day, 0) == 0
    replacement = _intent(side=OrderSide.BUY)
    engine.authorize(replacement, _ledger())


# ─── L4: ledger shorts must count toward nakedness ──────────────────────────

def test_ledger_short_call_plus_plan_short_is_rejected_as_naked():
    """A naked short call already in the ledger used to be skipped, so a plan
    selling another call slipped through as 'covered'."""
    ledger = _ledger((_options(OrderSide.SELL, OptionRight.CALL), -1, 4.0))
    intent = _intent(
        instrument=_options(OrderSide.SELL, OptionRight.CALL),
        side=OrderSide.SELL,
    )
    with pytest.raises(RiskRejected, match="naked call"):
        _engine().authorize(intent, ledger)


def test_ledger_short_call_covered_by_shares_still_authorized():
    """Two short calls against 200 held shares are fully covered."""
    ledger = _ledger(
        (_nvda_equity(), 200, 200),
        (_options(OrderSide.SELL, OptionRight.CALL), -1, 4.0),
    )
    intent = _intent(
        instrument=_options(OrderSide.SELL, OptionRight.CALL),
        side=OrderSide.SELL,
    )
    _engine().authorize(intent, ledger)


def test_ledger_short_call_closed_in_same_plan_is_not_double_counted():
    """A BUY-reduce closing leg nets against the ledger short before
    measuring the new short, so a close-and-reopen on the same instrument
    does not false-positive."""
    ledger = _ledger((_options(OrderSide.SELL, OptionRight.CALL), -1, 4.0))
    close = _intent(
        instrument=_options(OrderSide.SELL, OptionRight.CALL),
        side=OrderSide.BUY,
        reduce_only=True,
    )  # closes the 1 existing short
    reopen = _intent(
        instrument=_options(OrderSide.SELL, OptionRight.CALL),
        side=OrderSide.SELL,
    )  # opens 1 new short — still naked without shares
    plan = MultiLegOrderIntent(
        strategy_id="test", net_limit_price=-2.5, legs=(close, reopen)
    )
    with pytest.raises(RiskRejected, match="naked call"):
        _engine().authorize(plan, ledger)


def test_ledger_short_put_counts_toward_cash_requirement():
    """A short put in the ledger adds to the uncovered cash requirement that
    used to only aggregate plan legs."""
    put = _options(OrderSide.SELL, OptionRight.PUT)  # strike 200 × 100
    ledger = _ledger((put, -1, 5.0))
    ledger.cash["USD"] = 30_000  # enough for ONE uncovered put, not two
    intent = _intent(instrument=put, side=OrderSide.SELL)
    with pytest.raises(RiskRejected, match="uncovered short put"):
        _engine().authorize(intent, ledger)
    # 40_000 covers both shorts → authorized
    ledger.cash["USD"] = 50_000
    _engine().authorize(intent, ledger)


# ─── L5: NaN must fail closed in risk gates ─────────────────────────────────

def _unvalidated_intent(**overrides) -> OrderIntent:
    """Build an OrderIntent that BYPASSES __post_init__ validation — exactly
    what a broken producer (pickle round-trip, hand-rolled constructor) would
    deliver to risk, and why the risk engine re-checks finiteness itself."""
    import dataclasses

    base = _intent(side=OrderSide.BUY)
    obj = OrderIntent.__new__(OrderIntent)
    for field in dataclasses.fields(OrderIntent):
        object.__setattr__(
            obj, field.name, overrides.get(field.name, getattr(base, field.name))
        )
    return obj


def test_nan_quantity_fails_closed():
    intent = _unvalidated_intent(quantity=float("nan"))
    with pytest.raises(RiskRejected, match="quantity"):
        _engine().authorize(intent, _ledger())


def test_nan_quote_fails_closed():
    # A finite limit price gets past the notional gate so the NaN bid/ask
    # falls through to the quote sanity check itself.
    intent = _unvalidated_intent(
        limit_price=100.0, quote_bid=float("nan"), quote_ask=float("nan")
    )
    with pytest.raises(RiskRejected, match="invalid quote"):
        _engine().authorize(intent, _ledger())


# ─── L6: quote freshness must not be silently disabled ──────────────────────

def test_live_engine_without_fresh_quote_logs_loud_warning(caplog):
    with caplog.at_level("WARNING", logger="quantforge.risk.engine"):
        RiskEngine(RiskLimits(live_enabled=True))
    assert "require_fresh_quote" in caplog.text


def test_live_engine_with_fresh_quote_is_silent(caplog):
    with caplog.at_level("WARNING", logger="quantforge.risk.engine"):
        RiskEngine(RiskLimits(live_enabled=True, require_fresh_quote=True))
    assert "require_fresh_quote" not in caplog.text


# ─── NaN strike / cross-strike closing: fail-closed + no over-credit ──────

def test_nan_strike_option_is_rejected_at_construction():
    with pytest.raises(ValueError, match="finite"):
        EquityOption(
            id=InstrumentId("NVDA  260821C00200000", AssetClass.EQUITY_OPTION, "schwab"),
            underlying=_NVDA,
            expiration=date(2026, 8, 21),
            strike=float("nan"),
            right=OptionRight.CALL,
        )


def test_risk_rejects_nan_strike_naked_put_even_if_constructed():
    """A NaN-strike short put must NEVER pass the cash requirement — before
    this fix NaN > cash is False, so $1 of cash authorized a naked put."""
    import dataclasses

    put = _options(OrderSide.SELL, OptionRight.PUT)  # strike 200
    broken = EquityOption.__new__(EquityOption)
    for field in dataclasses.fields(EquityOption):
        object.__setattr__(broken, field.name, getattr(put, field.name))
    object.__setattr__(broken, "strike", float("nan"))

    intent = _intent(instrument=broken, side=OrderSide.SELL)
    with pytest.raises(RiskRejected, match="non-finite"):
        _engine().authorize(intent, _ledger())


def test_orphan_buy_reduce_cannot_mask_different_strike_naked_short():
    """closing legs must net against the SAME strike: an orphan BUY-reduce at
    strike 90 must not erase a naked short call at strike 100."""
    naked = _options(OrderSide.SELL, OptionRight.CALL)  # strike 200
    other = EquityOption(
        id=InstrumentId("NVDA  260821C00190000", AssetClass.EQUITY_OPTION, "schwab"),
        underlying=_NVDA,
        expiration=date(2026, 8, 21),
        strike=190.0,
        right=OptionRight.CALL,
    )
    ledger = _ledger((naked, -1, 4.0))
    orphan_close = _intent(
        instrument=other, side=OrderSide.BUY, reduce_only=True
    )
    with pytest.raises(RiskRejected, match="naked call"):
        _engine().authorize(orphan_close, ledger)


def test_long_put_at_different_strike_does_not_cover_short_put():
    """A long put only offsets a short put at the SAME strike: assignment
    still demands the full strike×multiplier in cash, a lower-strike long put
    merely recoups the spread. Before this fix the expiry-only aggregation
    let a $0-cash account sell a naked put @100 while merely holding a long
    put @90."""
    ledger = PortfolioLedger(cash={"USD": 0})
    long_90 = _options(OrderSide.BUY, OptionRight.PUT)  # strike 200 (helper)
    # Build a strike-90 long put at the same expiry as the short @200.
    import dataclasses

    long_90 = EquityOption.__new__(EquityOption)
    for field in dataclasses.fields(EquityOption):
        object.__setattr__(long_90, field.name, getattr(_options(OrderSide.BUY, OptionRight.PUT), field.name))
    object.__setattr__(long_90, "strike", 90.0)
    ledger.positions[long_90.id] = Position(long_90, quantity=1, average_price=2.0)

    short_200 = _intent(
        instrument=_options(OrderSide.SELL, OptionRight.PUT),
        side=OrderSide.SELL,
    )
    with pytest.raises(RiskRejected, match="uncovered short put"):
        _engine().authorize(short_200, ledger)

    # Same-strike long put DOES cover it: $0 cash is then fine.
    ledger2 = PortfolioLedger(cash={"USD": 0})
    same = _options(OrderSide.BUY, OptionRight.PUT)  # strike 200
    ledger2.positions[same.id] = Position(same, quantity=1, average_price=2.0)
    _engine().authorize(short_200, ledger2)


def test_multi_strike_short_puts_use_their_own_strikes():
    """Cash requirement is the SUM over each strike's own strike×multiplier
    (200×100 + 190×100 = 39_000), not max(strike)×total (40_000): the exact
    sum must authorize at 39_000 while the old max-strike upper bound would
    have falsely rejected."""
    high = _options(OrderSide.SELL, OptionRight.PUT)  # strike 200
    low = EquityOption(
        id=InstrumentId("NVDA  260821P00190000", AssetClass.EQUITY_OPTION, "schwab"),
        underlying=_NVDA,
        expiration=date(2026, 8, 21),
        strike=190.0,
        right=OptionRight.PUT,
    )
    ledger = PortfolioLedger(cash={"USD": 38_999})
    ledger.positions[high.id] = Position(high, quantity=-1, average_price=5.0)
    intent = _intent(instrument=low, side=OrderSide.SELL)
    with pytest.raises(RiskRejected, match="uncovered short put"):
        _engine().authorize(intent, ledger)
    ledger.cash["USD"] = 39_000
    _engine().authorize(intent, ledger)
