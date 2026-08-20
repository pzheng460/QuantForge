from __future__ import annotations

from datetime import datetime, timezone

import pytest

from quantforge.domain.instruments import AssetClass, Equity, InstrumentId
from quantforge.domain.intents import OrderIntent, OrderSide
from quantforge.portfolio.ledger import PortfolioLedger
from quantforge.risk.control import GlobalRiskControl
from quantforge.risk.engine import RiskEngine, RiskLimits, RiskRejected


@pytest.mark.critical
def test_persisted_global_halt_blocks_existing_risk_engine(tmp_path):
    control = GlobalRiskControl(tmp_path / "risk.json")
    engine = RiskEngine(
        RiskLimits(live_enabled=True, require_fresh_quote=True),
        global_control=control,
    )
    equity = Equity(InstrumentId("TSLA", AssetClass.EQUITY, "schwab"))
    intent = OrderIntent(
        strategy_id="test",
        instrument=equity,
        side=OrderSide.BUY,
        quantity=1,
        quote_bid=300,
        quote_ask=301,
        quote_timestamp=datetime.now(timezone.utc),
    )

    control.update(halted=True, reason="operator emergency stop")

    with pytest.raises(RiskRejected, match="global risk control is halted"):
        engine.authorize(intent, PortfolioLedger())

    reloaded = GlobalRiskControl(tmp_path / "risk.json")
    assert reloaded.get().halted is True
    assert reloaded.get().reason == "operator emergency stop"
