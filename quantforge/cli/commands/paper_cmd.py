"""CLI for the paper/shadow execution ledger."""

from __future__ import annotations

import json
from pathlib import Path

import click

from quantforge.paper_ledger import PaperLedger
from quantforge.paper_shadow_runner import run_shadow_observation


@click.group("paper")
def paper_group():
    """Record and inspect paper/shadow execution ledger events."""


@paper_group.command("signal")
@click.argument("strategy_id")
@click.option("--role", required=True, type=click.Choice(["promoted", "paper", "shadow"]))
@click.option("--side", required=True, type=click.Choice(["buy", "sell"]))
@click.option("--price", required=True, type=float)
@click.option("--quantity", required=True, type=float)
@click.option("--ts", default=None)
@click.option("--version-id", default="")
@click.option("--fee-rate", default=0.0, type=float)
@click.option("--slippage-bps", default=0.0, type=float)
@click.option("--ledger", "ledger_path", default=None, type=click.Path(path_type=Path))
@click.option("--initial-equity", default=100_000.0, type=float)
def signal_cmd(
    strategy_id,
    role,
    side,
    price,
    quantity,
    ts,
    version_id,
    fee_rate,
    slippage_bps,
    ledger_path,
    initial_equity,
):
    """Record one virtual paper/shadow signal and fill."""
    try:
        event = PaperLedger(ledger_path, initial_equity=initial_equity).record_signal(
            strategy_id=strategy_id,
            role=role,
            side=side,
            price=price,
            quantity=quantity,
            ts=ts,
            version_id=version_id,
            fee_rate=fee_rate,
            slippage_bps=slippage_bps,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(event, indent=2, sort_keys=True))


@paper_group.command("summary")
@click.argument("strategy_id")
@click.option("--role", default=None, type=click.Choice(["promoted", "paper", "shadow"]))
@click.option("--ledger", "ledger_path", default=None, type=click.Path(path_type=Path))
@click.option("--initial-equity", default=100_000.0, type=float)
def summary_cmd(strategy_id, role, ledger_path, initial_equity):
    """Print paper/shadow ledger summary."""
    summary = PaperLedger(ledger_path, initial_equity=initial_equity).summary(strategy_id, role=role)
    click.echo(json.dumps(summary, indent=2, sort_keys=True))


@paper_group.command("shadow-run")
@click.argument("strategy_id")
@click.option("--events", "events_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--registry", "registry_path", default=None, type=click.Path(path_type=Path))
@click.option("--ledger", "ledger_path", default=None, type=click.Path(path_type=Path))
@click.option("--initial-equity", default=100_000.0, type=float)
@click.option("--fee-rate", default=0.0, type=float)
@click.option("--slippage-bps", default=0.0, type=float)
@click.option("--out", "out_path", default=None, type=click.Path(path_type=Path))
def shadow_run_cmd(
    strategy_id,
    events_path,
    registry_path,
    ledger_path,
    initial_equity,
    fee_rate,
    slippage_bps,
    out_path,
):
    """Record promoted and shadow runtime signals into the ledger."""
    report = run_shadow_observation(
        strategy_id,
        events_path=events_path,
        registry_path=registry_path,
        ledger_path=ledger_path,
        initial_equity=initial_equity,
        fee_rate=fee_rate,
        slippage_bps=slippage_bps,
        out=out_path,
    )
    click.echo(json.dumps(report, indent=2, sort_keys=True))
