from __future__ import annotations

from datetime import date

from quantforge.adapters.ccxt import instrument_from_ccxt_market
from quantforge.crypto.lifecycle import settle_crypto_future
from quantforge.domain.instruments import AssetClass, CryptoFuture
from quantforge.portfolio.ledger import PortfolioLedger, Position


def test_ccxt_market_metadata_distinguishes_swap_and_delivery_future():
    swap = instrument_from_ccxt_market(
        {
            "symbol": "BTC/USDT:USDT",
            "type": "swap",
            "base": "BTC",
            "quote": "USDT",
            "settle": "USDT",
            "contractSize": 0.001,
        },
        venue="okx",
    )
    future = instrument_from_ccxt_market(
        {
            "symbol": "BTC/USDT:USDT-260925",
            "type": "future",
            "base": "BTC",
            "quote": "USDT",
            "settle": "USDT",
            "contractSize": 0.001,
            "expiry": 1_790_294_400_000,
        },
        venue="okx",
    )

    assert swap.id.asset_class is AssetClass.CRYPTO_PERPETUAL
    assert isinstance(future, CryptoFuture)
    assert future.id.asset_class is AssetClass.CRYPTO_FUTURE
    assert future.expiration == date(2026, 9, 25)


def test_delivery_future_cash_settlement_removes_position():
    future = CryptoFuture.from_symbol(
        "BTC/USDT:USDT-260925",
        venue="okx",
        expiration=date(2026, 9, 25),
        contract_size=0.001,
    )
    ledger = PortfolioLedger(cash={"USDT": 1000})
    ledger.positions[future.id] = Position(future, quantity=10, average_price=60_000)

    result = settle_crypto_future(future, settlement_price=65_000, ledger=ledger)

    assert result.realized_pnl == 50
    assert ledger.cash["USDT"] == 1050
    assert future.id not in ledger.positions
