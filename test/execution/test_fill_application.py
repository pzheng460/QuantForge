"""ExecutionService keeps the ledger in sync when a fill price is known.

Regression: ``PortfolioLedger`` was only applied at startup/reconciliation and
by the options lifecycle, so mid-session risk checks (reduce_only netting,
option coverage, cash) ran against a stale startup snapshot. ``execute`` now
accepts ``fill_price`` to apply fills on receipt; callers that only route
(speculative paths, options lifecycle doing its own settlement) leave it
unset and the ledger untouched.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from quantforge.domain.instruments import AssetClass, Equity, InstrumentId
from quantforge.domain.intents import OrderIntent, OrderSide
from quantforge.execution.service import ExecutionService
from quantforge.portfolio.ledger import PortfolioLedger
from quantforge.risk.engine import RiskEngine, RiskLimits


class _Adapter:
    def __init__(self):
        self.calls = []

    def submit(self, intent):
        self.calls.append(intent)
        return "order-1"


def _equity() -> Equity:
    return Equity(InstrumentId("TSLA", AssetClass.EQUITY, "schwab"))


def _service() -> tuple[ExecutionService, PortfolioLedger, _Adapter]:
    ledger = PortfolioLedger(cash={"USD": 100_000})
    adapter = _Adapter()
    service = ExecutionService(
        risk=RiskEngine(
            RiskLimits(live_enabled=True, require_fresh_quote=True)
        ),
        ledger=ledger,
        adapter=adapter,
    )
    return service, ledger, adapter


def _intent():
    return OrderIntent(
        strategy_id="test",
        instrument=_equity(),
        side=OrderSide.BUY,
        quantity=10,
        quote_bid=300,
        quote_ask=301,
        quote_timestamp=datetime.now(timezone.utc),
    )


def test_execute_with_fill_price_applies_fill_to_ledger():
    service, ledger, adapter = _service()
    intent = _intent()

    receipt = service.execute(intent, fill_price=300.0)

    assert receipt.broker_order_id == "order-1"
    assert ledger.quantity(intent.instrument.id) == 10
    assert ledger.cash["USD"] == pytest.approx(100_000 - 10 * 300)
    assert service._receipts[intent.intent_id] is receipt


def test_execute_without_fill_price_leaves_ledger_untouched():
    service, ledger, _adapter = _service()
    service.execute(_intent())

    assert ledger.quantity(_equity().id) == 0
    assert ledger.cash["USD"] == 100_000


def test_execute_fill_applies_to_multi_leg_plan():
    """A multi-leg intent with fill_price applies every leg to the ledger."""
    from quantforge.domain.intents import MultiLegOrderIntent

    service, ledger, _adapter = _service()
    close = OrderIntent(
        strategy_id="test",
        instrument=_equity(),
        side=OrderSide.SELL,
        quantity=5,
        reduce_only=True,
        quote_bid=300,
        quote_ask=301,
        quote_timestamp=datetime.now(timezone.utc),
    )
    open_ = OrderIntent(
        strategy_id="test",
        instrument=_equity(),
        side=OrderSide.BUY,
        quantity=3,
        quote_bid=300,
        quote_ask=301,
        quote_timestamp=datetime.now(timezone.utc),
    )
    plan = MultiLegOrderIntent(
        strategy_id="test", legs=(close, open_), net_limit_price=1.0
    )
    # Ledger starts flat for this instrument (reduce_only allowed pre-ledger).
    # Both legs are applied: SELL 5 (+5 position, cash up), then BUY 3
    # (position -2, cash down by 3*300).
    service.execute(plan, fill_price=300.0)
    assert ledger.quantity(_equity().id) == -2
    assert ledger.cash["USD"] == pytest.approx(100_000 + 5 * 300 - 3 * 300)
