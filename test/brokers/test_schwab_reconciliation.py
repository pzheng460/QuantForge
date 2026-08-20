from __future__ import annotations

import pytest

from quantforge.brokers.reconciliation import reconcile_schwab_account


def test_schwab_reconciliation_loads_cash_stock_and_option_positions():
    snapshot = {
        "currentBalances": {"cashBalance": 12_345.67},
        "positions": [
            {
                "longQuantity": 120,
                "shortQuantity": 0,
                "averagePrice": 210,
                "instrument": {"assetType": "EQUITY", "symbol": "NVDA"},
            },
            {
                "longQuantity": 0,
                "shortQuantity": 1,
                "averagePrice": 4.5,
                "instrument": {
                    "assetType": "OPTION",
                    "symbol": "NVDA  260821C00250000",
                    "underlyingSymbol": "NVDA",
                    "putCall": "CALL",
                },
            },
        ],
    }

    ledger = reconcile_schwab_account(snapshot)

    assert ledger.cash["USD"] == 12_345.67
    quantities = {key.symbol: position.quantity for key, position in ledger.positions.items()}
    assert quantities["NVDA"] == 120
    assert quantities["NVDA  260821C00250000"] == -1


def _row(asset_type: str, symbol: str, **extra) -> dict:
    instrument = {"assetType": asset_type, "symbol": symbol}
    instrument.update(extra)
    return {"longQuantity": 0, "shortQuantity": 0, "instrument": instrument}


def test_reconciliation_skips_unsupported_and_zero_quantity_positions():
    ledger = reconcile_schwab_account(
        {
            "positions": [
                _row("MUTUAL_FUND", "VFIAX"),  # unsupported asset type
                _row("EQUITY", "NVDA", longQuantity=5, shortQuantity=5),  # net zero
            ],
        }
    )
    assert ledger.positions == {}


def test_reconciliation_defaults_cash_when_balances_absent():
    ledger = reconcile_schwab_account({})
    assert ledger.cash == {"USD": 0.0}


def test_reconciliation_short_stock_is_negative_quantity():
    ledger = reconcile_schwab_account(
        {
            "positions": [
                {
                    "longQuantity": 0,
                    "shortQuantity": 40,
                    "averagePrice": 300,
                    "instrument": {"assetType": "EQUITY", "symbol": "TSLA"},
                }
            ],
        }
    )
    (key, position), = ledger.positions.items()
    assert key.symbol == "TSLA"
    assert position.quantity == -40


def test_reconciliation_put_option_uses_put_field():
    ledger = reconcile_schwab_account(
        {
            "positions": [
                {
                    "longQuantity": 2,
                    "shortQuantity": 0,
                    "instrument": {
                        "assetType": "OPTION",
                        "symbol": "NVDA  260821P00250000",
                        "underlyingSymbol": "NVDA",
                        "putCall": "PUT",
                    },
                }
            ],
        }
    )
    (key, position), = ledger.positions.items()
    assert position.quantity == 2
    assert position.instrument.right.value == "put"


def test_reconciliation_malformed_option_raises_clear_error():
    with pytest.raises(ValueError, match="expiration/strike/type"):
        reconcile_schwab_account(
            {
                "positions": [
                    {
                        "longQuantity": 1,
                        "shortQuantity": 0,
                        "instrument": {
                            "assetType": "OPTION",
                            "symbol": "WEIRD_SYMBOL",
                            "underlyingSymbol": "UVXY",
                            # missing expirationDate / strikePrice / putCall
                        },
                    }
                ],
            }
        )


def test_reconciliation_equity_without_symbol_raises_clear_error():
    """An EQUITY position row with a missing/blank symbol must fail with a
    clear ValueError, never a raw KeyError mid-reconciliation."""
    with pytest.raises(ValueError, match="lacks a symbol"):
        reconcile_schwab_account(
            {
                "positions": [
                    {
                        "longQuantity": 1,
                        "shortQuantity": 0,
                        "instrument": {"assetType": "EQUITY"},  # no symbol
                    }
                ],
            }
        )


def test_reconciliation_non_occ_option_falls_back_to_explicit_fields():
    ledger = reconcile_schwab_account(
        {
            "positions": [
                {
                    "longQuantity": 1,
                    "shortQuantity": 0,
                    "instrument": {
                        "assetType": "OPTION",
                        "symbol": "DIY_OPTION",
                        "underlyingSymbol": "AAPL",
                        "expirationDate": "2026-06-19T00:00:00Z",
                        "strikePrice": 210.5,
                        "putCall": "CALL",
                    },
                }
            ],
        }
    )
    (key, position), = ledger.positions.items()
    assert position.instrument.strike == 210.5
    assert position.instrument.expiration.isoformat() == "2026-06-19"
