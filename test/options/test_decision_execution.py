from __future__ import annotations

from datetime import date, datetime, timezone

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
