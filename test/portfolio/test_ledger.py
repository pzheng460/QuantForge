from __future__ import annotations

from datetime import date

import pytest

from quantforge.domain.instruments import (
    AssetClass,
    CryptoFuture,
    CryptoPerpetual,
    Equity,
    InstrumentId,
)
from quantforge.domain.intents import OrderSide
from quantforge.portfolio.ledger import InsufficientCash, PortfolioLedger


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


# ─── M5: contract multiplier unity + cash sufficiency ───────────────────────

def _future() -> CryptoFuture:
    return CryptoFuture.from_symbol(
        "BTC/USDT:USDT-260925",
        venue="okx",
        expiration=date(2026, 9, 25),
        contract_size=0.001,
    )


def test_fill_and_settlement_use_same_contract_size():
    """Fill notional and settlement P&L must both use contract_size; the old
    code debited the fill on the default multiplier (=1) but settled on
    contract_size, so a buy at 60_000 with contract_size=0.001 used to cost
    the ledger 60_000 instead of 120."""
    from quantforge.crypto.lifecycle import settle_crypto_future

    future = _future()
    ledger = PortfolioLedger(cash={"USDT": 100_000})
    ledger.apply_fill(future, OrderSide.BUY, 2, 60_000)

    assert ledger.cash["USDT"] == pytest.approx(100_000 - 2 * 60_000 * 0.001)
    result = settle_crypto_future(future, settlement_price=65_000, ledger=ledger)
    assert result.realized_pnl == pytest.approx(10.0)
    assert ledger.cash["USDT"] == pytest.approx(100_000 - 120 + 10)


def test_buy_beyond_cash_is_rejected_for_cash_instruments():
    ledger = PortfolioLedger(cash={"USD": 5_000})
    with pytest.raises(InsufficientCash):
        ledger.apply_fill(_equity(), OrderSide.BUY, 200, 100)  # 20_000 needed


def test_margin_instrument_may_use_ledger_as_cash_pool():
    """Leveraged derivatives draw the cash pool down (margin model): the
    sufficiency guard does not apply to them."""
    perp = CryptoPerpetual(
        id=InstrumentId("BTC/USDT:USDT", AssetClass.CRYPTO_PERPETUAL, "okx"),
        max_leverage=10,
    )
    ledger = PortfolioLedger(cash={"USDT": 1_000})
    ledger.apply_fill(perp, OrderSide.BUY, 1, 60_000)  # full notional debit
    assert ledger.cash["USDT"] == pytest.approx(1_000 - 60_000)


def test_fill_rejects_nan_values():
    ledger = PortfolioLedger(cash={"USD": 100_000})
    with pytest.raises(ValueError, match="finite"):
        ledger.apply_fill(_equity(), OrderSide.BUY, float("nan"), 100)
    with pytest.raises(ValueError, match="finite"):
        ledger.apply_fill(_equity(), OrderSide.BUY, 1, float("nan"))


def test_crypto_derivative_rejects_nan_max_leverage():
    with pytest.raises(ValueError, match="finite"):
        CryptoPerpetual(
            id=InstrumentId("BTC/USDT:USDT", AssetClass.CRYPTO_PERPETUAL, "okx"),
            max_leverage=float("nan"),
        )


def test_nan_max_leverage_cannot_bypass_cash_guard(monkeypatch):
    """A NaN max_leverage must fail closed toward the cash guard, not grant
    margin privileges (NaN <= 1 is False, which used to defeat the check)."""
    import dataclasses

    perp = CryptoPerpetual(
        id=InstrumentId("BTC/USDT:USDT", AssetClass.CRYPTO_PERPETUAL, "okx"),
        max_leverage=3,
    )
    broken = CryptoPerpetual.__new__(CryptoPerpetual)
    for field in dataclasses.fields(CryptoPerpetual):
        object.__setattr__(
            broken, field.name, getattr(perp, field.name)
        )
    object.__setattr__(broken, "max_leverage", float("nan"))

    ledger = PortfolioLedger(cash={"USDT": 1_000})
    with pytest.raises(InsufficientCash):
        ledger.apply_fill(broken, OrderSide.BUY, 1, 60_000)
