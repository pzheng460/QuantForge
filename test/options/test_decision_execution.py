from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from quantforge.domain.instruments import AssetClass, Equity, InstrumentId
from quantforge.execution import ExecutionService, PaperExecutionAdapter
from quantforge.options.execution import intent_from_option_decision
from quantforge.options.manager import OptionCandidate, OptionDecision
from quantforge.portfolio.ledger import PortfolioLedger, Position
from quantforge.risk import RiskEngine, RiskLimits


def test_covered_call_decision_becomes_risk_checked_order_intent():
    equity = Equity(InstrumentId("TSLA", AssetClass.EQUITY, "schwab"))
    ledger = PortfolioLedger(cash={"USD": 1000})
    ledger.positions[equity.id] = Position(equity, 100, 300)
    execution = ExecutionService(
        risk=RiskEngine(RiskLimits(live_enabled=True)),
        ledger=ledger,
        adapter=PaperExecutionAdapter(),
    )
    candidate = OptionCandidate(
        "TSLA  260821C00400000",
        400,
        date(2026, 8, 21),
        5,
        5.2,
        0.2,
        1000,
        100,
    )

    intent = intent_from_option_decision(
        OptionDecision(
            "开 Covered Call",
            ("eligible",),
            candidate.symbol,
            1,
            candidate.bid,
        ),
        candidate=candidate,
        ticker="TSLA",
        strategy_id="tsla_nvda_options",
        quote_time=datetime.now(timezone.utc),
    )
    receipt = execution.execute(intent)

    assert receipt.status == "submitted"
    assert intent.side.value == "sell"
    assert intent.reduce_only is False


def test_roll_decision_becomes_atomic_multi_leg_intent():
    """A ROLL decision maps to ONE MultiLegOrderIntent: BUY-reduce close on
    the old contract + SELL open on the replacement, net credit as the
    aggregate limit price — submitted as a single Schwab option strategy."""
    from quantforge.domain.intents import MultiLegOrderIntent, OrderSide

    close_candidate = OptionCandidate(
        "TSLA  260821C00340000",
        340,
        date(2026, 8, 21),
        1,
        1.1,
        0.55,
        1000,
        100,
    )
    roll_candidate = OptionCandidate(
        "TSLA  260925C00400000",
        400,
        date(2026, 9, 25),
        6,
        6.2,
        0.19,
        5000,
        300,
    )
    decision = OptionDecision(
        action="滚动 Covered Call",
        reasons=("roll",),
        contract_symbol=close_candidate.symbol,
        contracts=1,
        limit_price=1.1,
        roll_to_symbol=roll_candidate.symbol,
        roll_to_price=6,
    )
    now = datetime.now(timezone.utc)
    intent = intent_from_option_decision(
        decision,
        candidate=close_candidate,
        ticker="TSLA",
        strategy_id="tsla_nvda_options",
        quote_time=now,
        roll_to_candidate=roll_candidate,
    )
    assert isinstance(intent, MultiLegOrderIntent)
    assert len(intent.legs) == 2
    close_leg, open_leg = intent.legs
    assert close_leg.side is OrderSide.BUY and close_leg.reduce_only
    assert close_leg.instrument.id.symbol == close_candidate.symbol
    assert close_leg.limit_price == 1.1
    assert open_leg.side is OrderSide.SELL and not open_leg.reduce_only
    assert open_leg.instrument.id.symbol == roll_candidate.symbol
    assert open_leg.limit_price == 6
    assert intent.net_limit_price == pytest.approx(4.9)  # 6 - 1.1

    # The roll is risk-checked as a whole: covered by the held shares, the
    # close-and-reopen nets to one new short — authorized.
    equity = Equity(InstrumentId("TSLA", AssetClass.EQUITY, "schwab"))
    ledger = PortfolioLedger(cash={"USD": 1000})
    ledger.positions[equity.id] = Position(equity, 100, 300)
    execution = ExecutionService(
        risk=RiskEngine(RiskLimits(live_enabled=True)),
        ledger=ledger,
        adapter=PaperExecutionAdapter(),
    )
    receipt = execution.execute(intent)
    assert receipt.broker_order_id


def test_roll_decision_without_replacement_candidate_raises():
    from datetime import datetime, timezone

    close_candidate = OptionCandidate(
        "TSLA  260821C00340000",
        340,
        date(2026, 8, 21),
        1,
        1.1,
        0.55,
        1000,
        100,
    )
    decision = OptionDecision(
        action="滚动 Covered Call",
        reasons=("roll",),
        contract_symbol=close_candidate.symbol,
        contracts=1,
        limit_price=1.1,
        roll_to_symbol="TSLA  260925C00400000",
        roll_to_price=6,
    )
    with pytest.raises(ValueError, match="roll_to_candidate"):
        intent_from_option_decision(
            decision,
            candidate=close_candidate,
            ticker="TSLA",
            strategy_id="tsla_nvda_options",
            quote_time=datetime.now(timezone.utc),
        )
