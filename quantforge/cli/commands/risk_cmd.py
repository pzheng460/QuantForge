"""CLI for runtime risk checks."""

from __future__ import annotations

import json
from pathlib import Path

import click

from quantforge.risk_control import RiskConfig, check_risk
from quantforge.execution_risk import ExecutionRiskConfig, assess_execution_risk
from quantforge.live_policy import evaluate_live_policy


@click.group("risk")
def risk_group():
    """Runtime risk checks and kill-switch controls."""


@risk_group.command("check")
@click.argument("strategy_id")
@click.option("--role", default="promoted", type=click.Choice(["promoted", "paper", "shadow"]))
@click.option("--ledger", "ledger_path", default=None, type=click.Path(path_type=Path))
@click.option("--control-state", default=None, type=click.Path(path_type=Path))
@click.option("--registry", "registry_path", default=None, type=click.Path(path_type=Path))
@click.option("--out", "out_path", default=None, type=click.Path(path_type=Path))
@click.option("--auto-rollback", is_flag=True)
@click.option("--max-drawdown", default=0.05, type=float)
@click.option("--max-daily-loss", default=500.0, type=float)
@click.option("--reduce-daily-loss", default=250.0, type=float)
@click.option("--max-single-loss", default=250.0, type=float)
@click.option("--max-consecutive-losses", default=3, type=int)
@click.option("--initial-equity", default=100_000.0, type=float)
def check_cmd(
    strategy_id,
    role,
    ledger_path,
    control_state,
    registry_path,
    out_path,
    auto_rollback,
    max_drawdown,
    max_daily_loss,
    reduce_daily_loss,
    max_single_loss,
    max_consecutive_losses,
    initial_equity,
):
    """Check ledger risk and update trading control state."""
    report = check_risk(
        strategy_id,
        role=role,
        ledger_path=ledger_path,
        control_state=control_state,
        registry_path=registry_path,
        auto_rollback=auto_rollback,
        out=out_path,
        config=RiskConfig(
            max_drawdown=max_drawdown,
            max_daily_loss=max_daily_loss,
            reduce_daily_loss=reduce_daily_loss,
            max_single_loss=max_single_loss,
            max_consecutive_losses=max_consecutive_losses,
            initial_equity=initial_equity,
        ),
    )
    click.echo(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["decision"]["action"] == "observe" else 2)


@risk_group.command("execution")
@click.argument("orders", type=click.Path(exists=True, path_type=Path))
@click.option("--strategy-id", default=None)
@click.option("--control-state", default=None, type=click.Path(path_type=Path))
@click.option("--out", "out_path", default=None, type=click.Path(path_type=Path))
@click.option("--max-slippage-bps", default=50.0, type=float)
@click.option("--max-latency-ms", default=1000, type=int)
@click.option("--max-spread-bps", default=20.0, type=float)
@click.option("--min-fill-ratio", default=0.95, type=float)
@click.option("--max-reject-rate", default=0.05, type=float)
def execution_cmd(
    orders,
    strategy_id,
    control_state,
    out_path,
    max_slippage_bps,
    max_latency_ms,
    max_spread_bps,
    min_fill_ratio,
    max_reject_rate,
):
    """Assess execution quality risk from order JSON/JSONL."""
    report = assess_execution_risk(
        orders,
        strategy_id=strategy_id,
        control_state=control_state,
        out=out_path,
        config=ExecutionRiskConfig(
            max_slippage_bps=max_slippage_bps,
            max_latency_ms=max_latency_ms,
            max_spread_bps=max_spread_bps,
            min_fill_ratio=min_fill_ratio,
            max_reject_rate=max_reject_rate,
        ),
    )
    click.echo(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["decision"]["action"] == "observe" else 2)


@risk_group.command("live-policy")
@click.argument("policy", type=click.Path(exists=True, path_type=Path))
@click.argument("request", type=click.Path(exists=True, path_type=Path))
@click.option("--approvals", "approvals_path", default=None, type=click.Path(path_type=Path))
@click.option("--out", "out_path", default=None, type=click.Path(path_type=Path))
def live_policy_cmd(policy, request, approvals_path, out_path):
    """Check live launch/order permission policy."""
    report = evaluate_live_policy(
        policy,
        request,
        approvals_path=approvals_path,
        out=out_path,
    )
    click.echo(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["allowed"] else 2)
