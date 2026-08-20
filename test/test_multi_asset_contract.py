from datetime import date

import pytest


@pytest.mark.critical
def test_multi_asset_intents_share_one_risk_boundary():
    from quantforge.domain.instruments import (
        AssetClass,
        Equity,
        EquityOption,
        InstrumentId,
        OptionRight,
    )
    from quantforge.domain.intents import OrderIntent, OrderSide, OrderType
    from quantforge.portfolio.ledger import PortfolioLedger
    from quantforge.risk.engine import RiskEngine, RiskLimits, RiskRejected

    nvda = Equity(InstrumentId("NVDA", AssetClass.EQUITY, "SCHWAB"))
    call = EquityOption(
        id=InstrumentId(
            "NVDA  260821C00200000", AssetClass.EQUITY_OPTION, "SCHWAB"
        ),
        underlying=nvda.id,
        expiration=date(2026, 8, 21),
        strike=200,
        right=OptionRight.CALL,
    )
    ledger = PortfolioLedger(cash={"USD": 100_000})
    risk = RiskEngine(RiskLimits(live_enabled=True, max_order_notional=100_000))

    naked_call = OrderIntent(
        strategy_id="contract-test",
        instrument=call,
        side=OrderSide.SELL,
        quantity=1,
        order_type=OrderType.LIMIT,
        limit_price=5,
        quote_bid=5,
        quote_ask=5.1,
    )
    with pytest.raises(RiskRejected, match="naked call"):
        risk.authorize(naked_call, ledger)

    ledger.apply_fill(nvda, OrderSide.BUY, 100, 180)
    decision = risk.authorize(naked_call, ledger)
    assert decision.allowed is True
