"""Trading control CLI for pause/reduce/resume state."""

from __future__ import annotations

import json
from pathlib import Path

import click

from quantforge.trading_control import TradingControl, apply_auto_tune_report


@click.group("control")
def control_group():
    """Manage QuantForge trading control state."""


@control_group.command("apply-report")
@click.option("--report", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--strategy-id", required=True)
@click.option("--state", "state_path", default=None, type=click.Path(path_type=Path))
def apply_report_cmd(report, strategy_id, state_path):
    """Apply an auto-tune report to trading control state."""
    decision = apply_auto_tune_report(
        report, state_path=state_path, strategy_id=strategy_id
    )
    click.echo(json.dumps(decision.__dict__, indent=2))


@control_group.command("set")
@click.argument("strategy_id")
@click.argument(
    "action", type=click.Choice(["observe", "pause", "reduce", "resume", "reoptimize"])
)
@click.option("--state", "state_path", default=None, type=click.Path(path_type=Path))
def set_cmd(strategy_id, action, state_path):
    """Set control action manually."""
    decision = TradingControl(state_path).set_action(strategy_id, action)
    click.echo(json.dumps(decision.__dict__, indent=2))


@control_group.command("status")
@click.argument("strategy_id")
@click.option("--state", "state_path", default=None, type=click.Path(path_type=Path))
def status_cmd(strategy_id, state_path):
    """Show current control action."""
    click.echo(json.dumps(TradingControl(state_path).get_action(strategy_id), indent=2))
