"""`quantforge-cli exchanges list` — supported exchanges + fees."""

from __future__ import annotations

import json

import click

EXCHANGES = [
    {"id": "bitget",      "name": "Bitget",      "default_symbol": "BTC/USDT:USDT", "maker_fee": 0.0002, "taker_fee": 0.0005},
    {"id": "binance",     "name": "Binance",     "default_symbol": "BTC/USDT:USDT", "maker_fee": 0.0002, "taker_fee": 0.0004},
    {"id": "okx",         "name": "OKX",         "default_symbol": "BTC/USDT:USDT", "maker_fee": 0.0002, "taker_fee": 0.0005},
    {"id": "bybit",       "name": "Bybit",       "default_symbol": "BTC/USDT:USDT", "maker_fee": 0.0002, "taker_fee": 0.0005},
    {"id": "hyperliquid", "name": "Hyperliquid", "default_symbol": "BTC/USDT:USDT", "maker_fee": 0.0002, "taker_fee": 0.0005},
]


@click.group("exchanges")
def exchanges_group():
    """List supported exchanges."""


@exchanges_group.command("list")
@click.option("--json", "as_json", is_flag=True)
def list_cmd(as_json: bool):
    if as_json:
        click.echo(json.dumps(EXCHANGES, indent=2))
        return
    click.echo(f"{'id':<14}  {'name':<14}  {'default symbol':<20}  maker      taker")
    click.echo("-" * 72)
    for e in EXCHANGES:
        click.echo(
            f"{e['id']:<14}  {e['name']:<14}  {e['default_symbol']:<20}  "
            f"{e['maker_fee']*100:>5.3f}%   {e['taker_fee']*100:>5.3f}%"
        )
