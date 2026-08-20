from datetime import date

import pytest

from quantforge.domain.instruments import (
    AssetClass,
    Equity,
    EquityOption,
    InstrumentId,
    OptionRight,
)
from quantforge.domain.intents import (
    MultiLegOrderIntent,
    OrderIntent,
    OrderSide,
    OrderType,
)
from quantforge.execution import ExecutionService
from quantforge.options import OptionLifecycle
from quantforge.portfolio import PortfolioLedger
from quantforge.risk import RiskEngine, RiskLimits


def _instruments():
    equity = Equity(InstrumentId("NVDA", AssetClass.EQUITY, "SCHWAB"))
    call = EquityOption(
        id=InstrumentId("NVDA_CALL", AssetClass.EQUITY_OPTION, "SCHWAB"),
        underlying=equity.id,
        expiration=date(2026, 8, 21),
        strike=200,
        right=OptionRight.CALL,
    )
    return equity, call


def test_short_itm_call_assignment_updates_stock_and_cash():
    equity, call = _instruments()
    ledger = PortfolioLedger(cash={"USD": 100_000})
    ledger.apply_fill(equity, OrderSide.BUY, 100, 180)
    ledger.apply_fill(call, OrderSide.SELL, 1, 5)

    result = OptionLifecycle().expire(call, equity, 220, ledger)

    assert result.assignment is not None
    assert result.assignment.share_quantity == -100
    assert ledger.quantity(equity.id) == 0
    assert ledger.quantity(call.id) == 0


class _Adapter:
    def __init__(self):
        self.calls = 0

    def submit(self, intent):
        self.calls += 1
        return "broker-1"


@pytest.mark.critical
def test_multileg_execution_is_risk_checked_and_idempotent():
    equity, short_call = _instruments()
    long_call = EquityOption(
        id=InstrumentId("NVDA_LONG_CALL", AssetClass.EQUITY_OPTION, "SCHWAB"),
        underlying=equity.id,
        expiration=short_call.expiration,
        strike=210,
        right=OptionRight.CALL,
    )
    plan = MultiLegOrderIntent(
        strategy_id="spread",
        legs=(
            OrderIntent(
                "spread",
                short_call,
                OrderSide.SELL,
                1,
                OrderType.LIMIT,
                limit_price=5,
                quote_bid=5,
                quote_ask=5.1,
            ),
            OrderIntent(
                "spread",
                long_call,
                OrderSide.BUY,
                1,
                OrderType.LIMIT,
                limit_price=2,
                quote_bid=1.9,
                quote_ask=2,
            ),
        ),
        net_limit_price=-3,
    )
    adapter = _Adapter()
    service = ExecutionService(
        risk=RiskEngine(
            RiskLimits(live_enabled=True, max_order_notional=100_000)
        ),
        ledger=PortfolioLedger(cash={"USD": 100_000}),
        adapter=adapter,
    )

    first = service.execute(plan)
    second = service.execute(plan)

    assert first == second
    assert adapter.calls == 1
