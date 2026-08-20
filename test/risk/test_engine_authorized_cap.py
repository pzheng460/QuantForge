from __future__ import annotations

from datetime import datetime, timezone

import pytest

from quantforge.domain.instruments import AssetClass, Equity, InstrumentId
from quantforge.domain.intents import OrderIntent, OrderSide
from quantforge.portfolio.ledger import PortfolioLedger
from quantforge.risk.engine import RiskEngine, RiskLimits, RiskRejected


def _intent() -> OrderIntent:
    equity = Equity(InstrumentId("TSLA", AssetClass.EQUITY, "schwab"))
    return OrderIntent(
        strategy_id="test",
        instrument=equity,
        side=OrderSide.BUY,
        quantity=1,
        quote_bid=300,
        quote_ask=301,
        quote_timestamp=datetime.now(timezone.utc),
    )


def test_authorized_set_is_bounded(monkeypatch):
    # Successful authorizations must not grow without bound on a long-running
    # engine; the oldest ids are dropped and a fresh id can still be approved.
    monkeypatch.setattr(RiskEngine, "MAX_AUTHORIZED_IDS", 3)
    engine = RiskEngine(RiskLimits(live_enabled=True, max_daily_new_positions=10))
    ledger = PortfolioLedger()

    ids = []
    for _ in range(6):
        intent = _intent()
        engine.authorize(intent, ledger)
        ids.append(intent.intent_id)

    assert len(engine._authorized) == 3
    # Insertion order: newest kept, oldest forgotten.
    assert list(engine._authorized) == list(ids[-3:])

    fresh = _intent()
    engine.authorize(fresh, ledger)
    assert len(engine._authorized) == 3
    assert fresh.intent_id in engine._authorized


def test_authorized_dropped_id_cannot_be_replayed_as_duplicate(monkeypatch):
    # A forgotten id must never count as "duplicate" (it represents a finished
    # submission, and ids are minted fresh per attempt anyway).
    monkeypatch.setattr(RiskEngine, "MAX_AUTHORIZED_IDS", 1)
    engine = RiskEngine(RiskLimits(live_enabled=True, max_daily_new_positions=10))
    ledger = PortfolioLedger()

    first = _intent()
    engine.authorize(first, ledger)
    second = _intent()
    engine.authorize(second, ledger)

    assert first.intent_id not in engine._authorized
    # Explicitly replaying the SAME (still-retained) id is still rejected as a
    # duplicate — idempotency protection for in-flight submissions.
    with pytest.raises(RiskRejected, match="duplicate"):
        engine.authorize(second, ledger)
