"""`quantforge-cli engines ...` — live engine management.

Read-only ops (`list`) work standalone by reading the persistence file the
web server writes: ~/.quantforge/live/engines.json — engine configs. Per-trade
live performance telemetry no longer exists (its writer was removed in the
Python-first migration), so `engines list` shows config + status only.

Write ops (`start`, `stop`) require the running web server because the
asyncio task lives in the server process. Set QF_API_URL to point at a
non-default server.

Starting and stopping engines goes through the web API so the persisted
registry and hard risk controls remain the single source of truth.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from . import _http

LIVE_DIR = Path.home() / ".quantforge" / "live"
ENGINES_FILE = LIVE_DIR / "engines.json"


def _read_engines() -> list[dict]:
    if not ENGINES_FILE.exists():
        return []
    try:
        return json.loads(ENGINES_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return []


@click.group("engines")
def engines_group():
    """Manage live trading engines."""


@engines_group.command("list")
@click.option(
    "--via-server",
    is_flag=True,
    help="Query the web server's in-memory engine state instead of the persist file.",
)
@click.option("--json", "as_json", is_flag=True)
def list_cmd(via_server: bool, as_json: bool):
    """List engines (their configs + persisted status)."""
    if via_server:
        try:
            engines = _http.get("/live/engines")
        except _http.ServerUnreachable as e:
            click.echo(str(e), err=True)
            sys.exit(2)
    else:
        engines = list(_read_engines())

    if as_json:
        click.echo(json.dumps(engines, indent=2, default=str))
        return
    if not engines:
        click.echo(f"(no engines registered in {ENGINES_FILE})")
        return
    click.echo(
        f"{'engine_id':<10}  {'strategy':<22}  {'symbol':<18}  {'tf':<4}  status"
    )
    click.echo("-" * 78)
    for e in engines:
        status = e.get("status", "?")
        click.echo(
            f"{e.get('engine_id', '?'):<10}  {e.get('strategy', '?'):<22}  "
            f"{e.get('symbol', '?'):<18}  {e.get('timeframe', '?'):<4}  "
            f"{status:<7}"
        )


@engines_group.command("start")
@click.argument("strategy")
@click.option("--symbol", default="BTC/USDT:USDT")
@click.option("--exchange", default="bitget")
@click.option("--timeframe", default="1h")
@click.option("--demo/--no-demo", default=True)
@click.option("--leverage", type=int, default=1)
@click.option("--position-size", type=float, default=100.0)
@click.option("--warmup-bars", type=int, default=500)
@click.option(
    "--max-order-notional",
    type=float,
    default=None,
    help="Max single-order notional (USD). The server applies its own ceiling "
    "and rejects above that with 422. Omit to use the server default.",
)
@click.option(
    "--max-spread-pct",
    type=float,
    default=None,
    help="Max bid/ask spread as a fraction (0.15 = 15%). Omit to use the "
    "server default.",
)
@click.option(
    "--max-leverage",
    type=int,
    default=None,
    help="Max leverage enforced by risk checks. Omit to use the server default.",
)
@click.option(
    "--max-daily-new-positions",
    type=int,
    default=None,
    help="Per-day cap on new positions (shared with run-once and other "
    "engines). Omit to use the server default.",
)
def start_cmd(
    strategy,
    symbol,
    exchange,
    timeframe,
    demo,
    leverage,
    position_size,
    warmup_bars,
    max_order_notional,
    max_spread_pct,
    max_leverage,
    max_daily_new_positions,
):
    """Start a registered Python strategy through the server.

    Risk limits you do not pass explicitly fall back to the server-side
    defaults (and can never exceed the server's hard caps — over-cap values
    are rejected with 422).
    """
    payload = {
        "strategy": strategy,
        "symbol": symbol,
        "exchange": exchange,
        "timeframe": timeframe,
        "demo": demo,
        "leverage": leverage,
        "position_size_usdt": position_size,
        "warmup_bars": warmup_bars,
    }
    for key, value in (
        ("max_order_notional", max_order_notional),
        ("max_spread_pct", max_spread_pct),
        ("max_leverage", max_leverage),
        ("max_daily_new_positions", max_daily_new_positions),
    ):
        if value is not None:
            payload[key] = value
    try:
        res = _http.post("/live/start", json=payload)
        click.echo(
            f"started engine_id={res.get('engine_id')} status={res.get('status')}"
        )
    except _http.ServerUnreachable as e:
        click.echo(str(e), err=True)
        sys.exit(2)


@engines_group.command("stop")
@click.argument("engine_id")
def stop_cmd(engine_id: str):
    """Stop a server-managed engine. Requires the web server running."""
    try:
        res = _http.post(f"/live/stop/{engine_id}")
        click.echo(f"stopped: {res}")
    except _http.ServerUnreachable as e:
        click.echo(str(e), err=True)
        sys.exit(2)
