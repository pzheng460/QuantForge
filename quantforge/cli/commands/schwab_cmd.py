"""Read-only Schwab connectivity and data verification."""

from __future__ import annotations

import json

import click

from apps.dashboard.backend.routers.brokers import _connector
from quantforge.brokers.reconciliation import reconcile_schwab_account


@click.group("schwab")
def schwab_group():
    """Verify Schwab connectivity without creating or changing orders."""


@schwab_group.command("verify")
@click.option("--symbol", default="TSLA", show_default=True)
def verify_cmd(symbol: str):
    """Read account, quote, and option-chain data; never submit an order."""
    connector = _connector()
    ledger = reconcile_schwab_account(connector.get_account_snapshot())
    normalized = symbol.upper()
    quote = connector.get_quote_price(normalized)
    chain = connector.get_option_chain(
        normalized,
        contract_type="CALL",
        strike_count=5,
    )
    click.echo(
        json.dumps(
            {
                "mode": "read_only",
                "authenticated": connector.authenticated,
                "trading_authenticated": connector.trading_authenticated,
                "market_data_authenticated": connector.market_data_authenticated,
                "account_selected": bool(connector.account_hash),
                "position_count": len(ledger.positions),
                "cash_available": ledger.cash is not None,
                "symbol": normalized,
                "quote": quote,
                "option_chain_status": chain.get("status", "UNKNOWN"),
            },
            indent=2,
            sort_keys=True,
        )
    )
