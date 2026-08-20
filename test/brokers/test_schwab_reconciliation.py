from __future__ import annotations

import pytest

from quantforge.brokers.reconciliation import reconcile_schwab_account


def test_contiguous_occ_symbol_without_space_is_parsed():
    """Schwab returns OCC symbols WITHOUT whitespace (e.g.
    NVDA260821C00250000); the regex used to require a space between root and
    expiration and silently fell back to the explicit fields."""
    ledger = reconcile_schwab_account(
        {
            "positions": [
                {
                    "longQuantity": 1,
                    "shortQuantity": 0,
                    "instrument": {
                        "assetType": "OPTION",
                        "symbol": "NVDA260821C00250000",
                    },
                }
            ],
        }
    )
    (key, position), = ledger.positions.items()
    assert position.quantity == 1
    assert position.instrument.underlying.symbol == "NVDA"
    assert position.instrument.strike == 250.0
    assert position.instrument.right.value == "call"
    assert position.instrument.expiration.isoformat() == "2026-08-21"


def test_reconciliation_warns_on_unmodeled_asset_types(caplog):
    """Skipping crypto/fixed-income/forex positions must not be silent: the
    ledger understates exposure, so a warning is mandatory."""
    with caplog.at_level("WARNING", logger="quantforge.brokers.reconciliation"):
        ledger = reconcile_schwab_account(
            {
                "positions": [
                    _row("CASH_EQUIVALENT", "USD"),
                    _row("FOREX", "EUR/USD"),
                ],
            }
        )
    assert ledger.positions == {}
    assert "Reconciliation skipped unmodeled Schwab position" in caplog.text
    assert "CASH_EQUIVALENT" in caplog.text
    assert "FOREX" in caplog.text


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


# ─── L11: token directory permissions are tightened, not just created ───────

def test_token_store_tightens_existing_loose_directory(tmp_path):
    from quantforge.brokers.schwab import SchwabTokenStore

    loose = tmp_path / "loose-tokens"
    loose.mkdir(mode=0o711)
    store = SchwabTokenStore(loose / "tokens.json")
    store.save({"access_token": "opaque", "refresh_token": "opaque"})
    assert (loose.stat().st_mode & 0o777) == 0o700
    # The token file itself stays 0600.
    assert (store.path.stat().st_mode & 0o777) == 0o600


def test_token_store_keeps_already_strict_directory(tmp_path):
    from quantforge.brokers.schwab import SchwabTokenStore

    strict = tmp_path / "strict-tokens"
    strict.mkdir(mode=0o700)
    store = SchwabTokenStore(strict / "tokens.json")
    store.save({"access_token": "opaque"})
    assert (strict.stat().st_mode & 0o777) == 0o700
