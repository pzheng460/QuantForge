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


def test_missing_global_control_file_fails_closed(tmp_path):
    """After initialization, deleting the master-halt file must HALT trading —
    never silently re-enable a previously-halted book."""
    control = GlobalRiskControl(tmp_path / "risk.json")
    assert control.get().halted is False  # initialized to allow by default
    control.update(halted=True, reason="operator halt")
    # Delete the file after an operator halt: it must stay halted.
    (tmp_path / "risk.json").unlink()
    assert control.get().halted is True
    assert control.get().reason == "risk control file missing"


def test_initialization_creates_default_allow_file(tmp_path):
    """First construction atomically creates a default (not-halted) state file,
    so a MISSING file is unambiguous (deleted -> halt)."""
    path = tmp_path / "risk.json"
    assert not path.exists()
    control = GlobalRiskControl(path)
    assert path.exists()
    assert control.get().halted is False
