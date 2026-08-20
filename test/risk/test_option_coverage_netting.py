"""Regressions for risk-engine fixes:

1. SELL-reduce legs that CLOSE existing LONG options must be subtracted from
   the coverage pools — otherwise a plan that closes a long and opens a short
   at the same strike/expiry would count the long it is simultaneously closing
   as still-covering (bypassing the nakedness/cash-coverage gate).
2. The notional cap must be sized on ``contract_size`` for crypto derivatives
   (their ``multiplier`` defaults to 1), matching the ledger/settlement.
3. A MARKET order without any price reference must not silently skip the
   notional cap when ``require_fresh_quote`` is on.
4. Naked-call coverage must use the option's own multiplier, not a hardcoded 100.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from quantforge.domain.instruments import (
    AssetClass,
    CryptoPerpetual,
    Equity,
    EquityOption,
    InstrumentId,
    OptionRight,
)
from quantforge.domain.intents import MultiLegOrderIntent, OrderIntent, OrderSide
from quantforge.portfolio.ledger import PortfolioLedger, Position
from quantforge.risk.engine import RiskEngine, RiskLimits, RiskRejected

_NVDA = InstrumentId("NVDA", AssetClass.EQUITY, "schwab")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _option(
    right: OptionRight,
    *,
    strike: float = 200.0,
    multiplier: float = 100,
) -> EquityOption:
    return EquityOption(
        id=InstrumentId(
            f"NVDA  260821{right.value}{int(strike) * 1000:08d}",
            AssetClass.EQUITY_OPTION,
            "schwab",
        ),
        underlying=_NVDA,
        expiration=date(2026, 8, 21),
        strike=strike,
        right=right,
        multiplier=multiplier,
    )


def _intent(
    *,
    instrument,
    side: OrderSide,
    quantity: float = 1,
    reduce_only: bool = False,
    limit_price: float | None = None,
    quote_timestamp: datetime | None = None,
) -> OrderIntent:
    return OrderIntent(
        strategy_id="test",
        instrument=instrument,
        side=side,
        quantity=quantity,
        reduce_only=reduce_only,
        quote_bid=1.0,
        quote_ask=1.1,
        quote_timestamp=quote_timestamp or _now(),
        limit_price=limit_price,
    )


def _engine(limits: RiskLimits | None = None) -> RiskEngine:
    return RiskEngine(
        limits or RiskLimits(live_enabled=True, max_daily_new_positions=10)
    )


def _ledger(*positions, cash: dict | None = None) -> PortfolioLedger:
    ledger = PortfolioLedger(cash=cash or {"USD": 1_000_000})
    for instrument, qty, price in positions:
        ledger.positions[instrument.id] = Position(instrument, qty, price)
    return ledger


# ─── SELL-reduce closing longs must not count as coverage ───────────────────


def test_sell_reduce_closing_long_call_then_opening_short_is_rejected():
    """Exploit regression: ledger holds a long call; a plan {SELL-reduce close
    long, SELL open short} at the same strike must NOT pass the naked-call gate
    (the long is being closed, it cannot cover the new short)."""
    option = _option(OptionRight.CALL)
    ledger = _ledger((option, 1, 4.0))
    plan = MultiLegOrderIntent(
        strategy_id="test",
        legs=[
            _intent(
                instrument=option, side=OrderSide.SELL, reduce_only=True
            ),
            _intent(instrument=option, side=OrderSide.SELL),
        ],
    )
    with pytest.raises(RiskRejected, match="naked call"):
        _engine().authorize(plan, ledger)


def test_sell_reduce_closing_long_put_then_opening_short_requires_cash():
    """Exploit regression for puts: closing the long put and opening a short
    put at the same strike must not erase the cash requirement."""
    option = _option(OptionRight.PUT)
    ledger = _ledger((option, 1, 4.0), cash={"USD": 0.0})
    plan = MultiLegOrderIntent(
        strategy_id="test",
        legs=[
            _intent(
                instrument=option, side=OrderSide.SELL, reduce_only=True
            ),
            _intent(instrument=option, side=OrderSide.SELL),
        ],
    )
    # 1 short put × 200 strike × 100 mult = 20_000 required; 0 cash available.
    with pytest.raises(RiskRejected, match="short put"):
        _engine().authorize(plan, ledger)


def test_sell_reduce_closing_long_only_is_authorized():
    """Closing a long without re-opening a short is fine (no new exposure)."""
    option = _option(OptionRight.CALL)
    ledger = _ledger((option, 1, 4.0))
    plan = MultiLegOrderIntent(
        strategy_id="test",
        legs=[
            _intent(
                instrument=option, side=OrderSide.SELL, reduce_only=True
            ),
        ],
    )
    decision = _engine().authorize(plan, ledger)
    assert decision.allowed


def test_sell_reduce_closing_long_at_different_strike_does_not_mask_short():
    """A SELL-reduce closing a long put at strike 190 must not offset a short
    put at strike 200 (put coverage is strike-exact)."""
    long_option = _option(OptionRight.PUT, strike=190.0)
    short_option = _option(OptionRight.PUT, strike=200.0)
    ledger = _ledger((long_option, 1, 4.0), cash={"USD": 0.0})
    plan = MultiLegOrderIntent(
        strategy_id="test",
        legs=[
            _intent(
                instrument=long_option, side=OrderSide.SELL, reduce_only=True
            ),
            _intent(instrument=short_option, side=OrderSide.SELL),
        ],
    )
    # The short at 200 is uncovered: 20_000 cash required, 0 available.
    with pytest.raises(RiskRejected, match="short put"):
        _engine().authorize(plan, ledger)


def test_short_call_covered_by_unrelated_ledger_long_is_authorized():
    """A normal covered position (long call still held, no closing leg) must
    keep working — the netting only subtracts legs that actually close it."""
    option = _option(OptionRight.CALL)
    ledger = _ledger((option, 1, 4.0))
    plan = MultiLegOrderIntent(
        strategy_id="test",
        legs=[
            _intent(instrument=option, side=OrderSide.SELL),
        ],
    )
    decision = _engine().authorize(plan, ledger)
    assert decision.allowed


def test_naked_call_coverage_uses_option_multiplier_not_hardcoded_100():
    """A short call needs ``multiplier`` shares of underlying per contract
    (not a hardcoded 100): with multiplier 10, 10 shares cover one contract
    while 9 do not (regression for the hardcoded '100')."""
    option = _option(OptionRight.CALL, multiplier=10)
    engine = _engine()
    # 10 shares cover 1 contract × multiplier 10 → authorized.
    ledger = _ledger(
        (Equity(_NVDA), 10, 200.0),
    )
    intent = _intent(instrument=option, side=OrderSide.SELL)
    decision = engine.authorize(intent, ledger)
    assert decision.allowed
    # 9 shares → naked, rejected (a hardcoded 100 would wrongly accept this).
    bare = _ledger(
        (Equity(_NVDA), 9, 200.0),
    )
    with pytest.raises(RiskRejected, match="naked call"):
        engine.authorize(_intent(instrument=option, side=OrderSide.SELL), bare)


# ─── Notional must use contract_size for crypto derivatives ─────────────────


def test_notional_cap_uses_contract_size_for_crypto_derivative():
    """A perp with contract_size=100 and multiplier=1 must be capped at
    price × qty × 100, not price × qty × 1 (regression: the risk engine sized
    notional on ``multiplier`` while the ledger/settlement use
    ``contract_size``, under-enforcing the cap by 100×)."""
    perp = CryptoPerpetual(
        id=InstrumentId("BTC/USDT:USDT", AssetClass.CRYPTO_PERPETUAL, "bitget"),
        base_currency="BTC",
        quote_currency="USDT",
        settlement_currency="USDT",
        contract_size=100,
        max_leverage=50,
    )
    engine = _engine(
        RiskLimits(
            live_enabled=True,
            require_fresh_quote=True,
            max_order_notional=10_000,
            max_daily_new_positions=10,
            max_leverage=50,
        )
    )
    # price 2.0 × qty 100 × contract_size 100 = 20_000 > 10_000 → rejected.
    intent = _intent(
        instrument=perp,
        side=OrderSide.BUY,
        quantity=100,
        limit_price=2.0,
    )
    with pytest.raises(RiskRejected, match="notional"):
        engine.authorize(intent, PortfolioLedger(cash={"USDT": 1_000_000}))
    # price 1.5 × qty 10 × contract_size 100 = 1_500 ≤ 10_000 → allowed.
    ok = _intent(
        instrument=perp,
        side=OrderSide.BUY,
        quantity=10,
        limit_price=1.5,
    )
    decision = engine.authorize(ok, PortfolioLedger(cash={"USDT": 1_000_000}))
    assert decision.allowed


def test_market_order_without_price_reference_rejected_when_fresh_quote():
    """A MARKET order with no limit_price and no quote_ask must fail the
    notional gate rather than silently skip it under require_fresh_quote."""
    engine = _engine(
        RiskLimits(live_enabled=True, require_fresh_quote=True)
    )
    equity = Equity(_NVDA)
    intent = OrderIntent(
        strategy_id="test",
        instrument=equity,
        side=OrderSide.BUY,
        quantity=1_000_000,
        quote_bid=None,
        quote_ask=None,
        quote_timestamp=_now(),
    )
    with pytest.raises(RiskRejected, match="no price reference"):
        engine.authorize(intent, PortfolioLedger())


# ─── Multi-leg net-cash pre-check ────────────────────────────────────────────


def _perp() -> CryptoPerpetual:
    return CryptoPerpetual(
        id=InstrumentId("BTC/USDT:USDT", AssetClass.CRYPTO_PERPETUAL, "bitget"),
        base_currency="BTC",
        quote_currency="USDT",
        settlement_currency="USDT",
        contract_size=100,
        max_leverage=50,
    )


def _buy_leg(perp: CryptoPerpetual, qty: float, price: float) -> OrderIntent:
    return _intent(
        instrument=perp,
        side=OrderSide.BUY,
        quantity=qty,
        limit_price=price,
    )


def test_multi_leg_plan_aggregated_debit_checked_against_cash():
    """A multi-leg plan's OPENING debit legs must be checked against cash as a
    whole — otherwise an atomic spread can submit and then hit a per-fill
    InsufficientCash mid-plan, leaving a partially-filled book."""
    perp = _perp()
    plan = MultiLegOrderIntent(
        strategy_id="test",
        legs=[
            _buy_leg(perp, 100, 2.0),  # 100 × 2.0 × 100 (contract_size) = 20_000
            _buy_leg(perp, 100, 2.0),  # 20_000
        ],
    )
    engine = _engine(
        RiskLimits(
            live_enabled=True,
            require_fresh_quote=True,
            max_order_notional=100_000,
            max_daily_new_positions=10,
            max_leverage=50,
        )
    )
    # Aggregated debit 40_000 > 10_000 cash → rejected before any submit.
    with pytest.raises(RiskRejected, match="exceeds available cash"):
        engine.authorize(plan, PortfolioLedger(cash={"USDT": 10_000}))
    # 40_000 ≤ 50_000 cash → authorized.
    decision = engine.authorize(plan, PortfolioLedger(cash={"USDT": 50_000}))
    assert decision.allowed


def test_multi_leg_reduce_only_close_legs_excluded_from_cash_check():
    """Closing (reduce_only) legs must not count toward the debit pre-check —
    they reduce exposure, so they cannot bankrupt the book."""
    perp = _perp()
    close = OrderIntent(
        strategy_id="test",
        instrument=perp,
        side=OrderSide.BUY,
        quantity=100,
        reduce_only=True,
        limit_price=2.0,
        quote_bid=1.0,
        quote_ask=1.1,
        quote_timestamp=_now(),
    )
    reopen = _buy_leg(perp, 100, 2.0)  # 20_000 debit (only counting leg)
    plan = MultiLegOrderIntent(
        strategy_id="test",
        legs=[close, reopen],
    )
    engine = _engine(
        RiskLimits(
            live_enabled=True,
            require_fresh_quote=True,
            max_order_notional=100_000,
            max_daily_new_positions=10,
            max_leverage=50,
        )
    )
    # 20_000 debit (close leg excluded) > 15_000 cash → rejected.
    with pytest.raises(RiskRejected, match="exceeds available cash"):
        engine.authorize(plan, PortfolioLedger(cash={"USDT": 15_000}))
    # With enough cash for just the opening leg, it passes.
    decision = engine.authorize(plan, PortfolioLedger(cash={"USDT": 25_000}))
    assert decision.allowed
