from quantforge.domain.instruments import AssetClass, Equity, InstrumentId
from quantforge.domain.intents import OrderSide
from quantforge.portfolio.ledger import PortfolioLedger


def _equity():
    return Equity(InstrumentId("TSLA", AssetClass.EQUITY, "schwab"))


def test_ledger_handles_reduce_close_and_reversal_average_price():
    equity = _equity()
    ledger = PortfolioLedger(cash={"USD": 10_000})
    ledger.apply_fill(equity, OrderSide.BUY, 10, 100)
    ledger.apply_fill(equity, OrderSide.SELL, 4, 120)
    assert ledger.positions[equity.id].average_price == 100

    ledger.apply_fill(equity, OrderSide.SELL, 10, 90)
    assert ledger.quantity(equity.id) == -4
    assert ledger.positions[equity.id].average_price == 90

    ledger.apply_fill(equity, OrderSide.BUY, 4, 80)
    assert equity.id not in ledger.positions
