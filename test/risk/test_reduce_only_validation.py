"""reduce_only intents are self-attested, so risk must reconcile them with the
ledger: a reduce_only leg may never open, enlarge, flip, or exceed a position."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from quantforge.domain.instruments import AssetClass, Equity, InstrumentId
from quantforge.domain.intents import OrderIntent, OrderSide
from quantforge.portfolio.ledger import PortfolioLedger, Position
from quantforge.risk.engine import RiskEngine, RiskLimits, RiskRejected


def _equity() -> Equity:
    return Equity(InstrumentId("TSLA", AssetClass.EQUITY, "schwab"))


def _intent(side: OrderSide, quantity: float, *, reduce_only: bool) -> OrderIntent:
    return OrderIntent(
        strategy_id="test",
        instrument=_equity(),
        side=side,
        quantity=quantity,
        reduce_only=reduce_only,
        quote_bid=300,
        quote_ask=301,
        quote_timestamp=datetime.now(timezone.utc),
    )


def _engine() -> RiskEngine:
    return RiskEngine(RiskLimits(live_enabled=True, max_daily_new_positions=10))


def _ledger(held: float) -> PortfolioLedger:
    ledger = PortfolioLedger(cash={"USD": 1_000_000})
    if held:
        ledger.positions[_equity().id] = Position(_equity(), held, 300)
    return ledger


def test_reduce_only_sell_closing_long_is_authorized():
    intent = _intent(OrderSide.SELL, 5, reduce_only=True)
    decision = _engine().authorize(intent, _ledger(held=10))
    assert decision.allowed


def test_reduce_only_buy_closing_short_is_authorized():
    intent = _intent(OrderSide.BUY, 5, reduce_only=True)
    decision = _engine().authorize(intent, _ledger(held=-10))
    assert decision.allowed


def test_reduce_only_without_ledger_view_is_non_authoritative_and_allowed():
    # Engine-warmup flows submit reduce_only closes before the ledger reflects
    # fills; a zero ledger quantity is treated as "no adjudication" not "reject".
    intent = _intent(OrderSide.SELL, 5, reduce_only=True)
    decision = _engine().authorize(intent, _ledger(held=0))
    assert decision.allowed


def test_reduce_only_exceeding_position_is_rejected():
    intent = _intent(OrderSide.SELL, 15, reduce_only=True)
    with pytest.raises(RiskRejected, match="exceeds the position"):
        _engine().authorize(intent, _ledger(held=10))


def test_reduce_only_buy_adding_to_long_is_rejected():
    intent = _intent(OrderSide.BUY, 5, reduce_only=True)
    with pytest.raises(RiskRejected, match="cannot add to a long"):
        _engine().authorize(intent, _ledger(held=10))


def test_reduce_only_sell_adding_to_short_is_rejected():
    intent = _intent(OrderSide.SELL, 5, reduce_only=True)
    with pytest.raises(RiskRejected, match="cannot add to a short"):
        _engine().authorize(intent, _ledger(held=-10))


def test_reduce_only_aggregates_per_instrument_across_multi_leg_plan():
    """Per-leg checks would let two BUY-reduce legs on the same instrument
    each 'fit' a long of 10 while NETTING to +15 (flip). The whole plan must
    be reconciled per instrument."""
    from quantforge.domain.intents import MultiLegOrderIntent

    plan = MultiLegOrderIntent(
        strategy_id="test",
        legs=[
            _intent(OrderSide.BUY, 10, reduce_only=True),
            _intent(OrderSide.BUY, 5, reduce_only=True),
        ],
    )
    with pytest.raises(RiskRejected, match="reduce_only"):
        _engine().authorize(plan, _ledger(held=10))


def test_reduce_only_net_cancel_within_position_is_authorized():
    """Opposing reduce_only legs on one instrument net to zero and must NOT be
    treated as a flip."""
    from quantforge.domain.intents import MultiLegOrderIntent

    plan = MultiLegOrderIntent(
        strategy_id="test",
        legs=[
            _intent(OrderSide.BUY, 5, reduce_only=True),
            _intent(OrderSide.SELL, 5, reduce_only=True),
        ],
    )
    decision = _engine().authorize(plan, _ledger(held=10))
    assert decision.allowed
