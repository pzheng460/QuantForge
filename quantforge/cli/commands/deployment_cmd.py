"""CLI for the QuantForge strategy deployment registry."""

from __future__ import annotations

import json
from pathlib import Path

import click

from quantforge.approvals import ApprovalQueue
from quantforge.deployment import DeploymentError, DeploymentRegistry, DeploymentStatus, build_live_command
from quantforge.deployment_pipeline import run_promotion_pipeline
from quantforge.shadow import run_shadow_comparison


@click.group("deployment")
def deployment_group():
    """Manage the QuantForge strategy deployment registry."""


@deployment_group.command("register")
@click.option("--strategy-id", required=True)
@click.option("--pine", "pine_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--evidence", "evidence_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--source", default="manual")
@click.option("--registry", "registry_path", default=None, type=click.Path(path_type=Path))
def register_cmd(strategy_id, pine_path, evidence_path, source, registry_path):
    """Register a candidate strategy artifact."""
    version = DeploymentRegistry(registry_path).register_candidate(
        strategy_id=strategy_id,
        pine_path=pine_path,
        evidence_path=evidence_path,
        source=source,
    )
    click.echo(json.dumps({"version_id": version.version_id, "status": version.status.value}, indent=2))


@deployment_group.command("transition")
@click.argument("version_id")
@click.argument("status", type=click.Choice([s.value for s in DeploymentStatus]))
@click.option("--registry", "registry_path", default=None, type=click.Path(path_type=Path))
def transition_cmd(version_id, status, registry_path):
    """Move a version through candidate/paper/shadow states."""
    try:
        version = DeploymentRegistry(registry_path).transition(version_id, DeploymentStatus(status))
    except (DeploymentError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({"version_id": version.version_id, "status": version.status.value}, indent=2))


@deployment_group.command("promote")
@click.argument("version_id")
@click.option("--registry", "registry_path", default=None, type=click.Path(path_type=Path))
def promote_cmd(version_id, registry_path):
    """Promote a shadow version if its evidence gate passes."""
    try:
        version = DeploymentRegistry(registry_path).promote(version_id)
    except (DeploymentError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({
        "version_id": version.version_id,
        "status": version.status.value,
        "previous_version_id": version.previous_version_id,
    }, indent=2))


@deployment_group.command("rollback")
@click.argument("strategy_id")
@click.option("--registry", "registry_path", default=None, type=click.Path(path_type=Path))
def rollback_cmd(strategy_id, registry_path):
    """Restore the previous promoted version for a strategy."""
    try:
        version = DeploymentRegistry(registry_path).rollback(strategy_id)
    except (DeploymentError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({"version_id": version.version_id, "status": version.status.value}, indent=2))


@deployment_group.command("list")
@click.option("--strategy-id", default=None)
@click.option("--registry", "registry_path", default=None, type=click.Path(path_type=Path))
def list_cmd(strategy_id, registry_path):
    """List registered strategy versions."""
    versions = DeploymentRegistry(registry_path).list(strategy_id)
    rows = [
        {
            "strategy_id": v.strategy_id,
            "version_id": v.version_id,
            "status": v.status.value,
            "pine_path": v.pine_path,
            "evidence_path": v.evidence_path,
            "previous_version_id": v.previous_version_id,
        }
        for v in versions
    ]
    click.echo(json.dumps(rows, indent=2))


@deployment_group.command("live-command")
@click.argument("strategy_id")
@click.option("--mode", default="paper", type=click.Choice(["paper", "shadow", "live"]))
@click.option("--control-state", default="eval/optimizer_ab/results/trading_control.json")
@click.option("--approvals", "approvals_path", default=None, type=click.Path(path_type=Path))
@click.option("--approval-id", default=None)
@click.option("--policy", "policy_path", default=None, type=click.Path(path_type=Path))
@click.option("--request", "request_path", default=None, type=click.Path(path_type=Path))
@click.option("--registry", "registry_path", default=None, type=click.Path(path_type=Path))
def live_command_cmd(strategy_id, mode, control_state, approvals_path, approval_id, policy_path, request_path, registry_path):
    """Print the live command for the promoted registry version."""
    try:
        cmd = build_live_command(
            strategy_id,
            registry_path=registry_path,
            mode=mode,
            control_state=control_state,
            approvals_path=approvals_path,
            approval_id=approval_id,
            policy_path=policy_path,
            request_path=request_path,
        )
    except (DeploymentError, KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps({"command": cmd}, indent=2))


@deployment_group.command("shadow-compare")
@click.argument("strategy_id")
@click.option("--start", required=True)
@click.option("--end", required=True)
@click.option("--symbol", default="BTC/USDT:USDT")
@click.option("--exchange", default="bitget")
@click.option("--timeframe", default="1h")
@click.option("--out", "out_path", default=None, type=click.Path(path_type=Path))
@click.option("--registry", "registry_path", default=None, type=click.Path(path_type=Path))
def shadow_compare_cmd(strategy_id, start, end, symbol, exchange, timeframe, out_path, registry_path):
    """Compare the latest shadow candidate against the promoted version."""
    try:
        report = run_shadow_comparison(
            strategy_id,
            registry_path=registry_path,
            start=start,
            end=end,
            symbol=symbol,
            exchange=exchange,
            timeframe=timeframe,
            out=out_path,
        )
    except (DeploymentError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["passed"] else 1)


@deployment_group.command("auto-promote")
@click.argument("strategy_id")
@click.option("--pine", "candidate_pine", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--evidence", "evidence_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--start", required=True)
@click.option("--end", required=True)
@click.option("--symbol", default="BTC/USDT:USDT")
@click.option("--exchange", default="bitget")
@click.option("--timeframe", default="1h")
@click.option("--source", default="auto_tune")
@click.option("--shadow-report", "shadow_report_path", default=None, type=click.Path(path_type=Path))
@click.option("--out", "report_path", default=None, type=click.Path(path_type=Path))
@click.option("--registry", "registry_path", default=None, type=click.Path(path_type=Path))
@click.option("--ledger", "ledger_path", default=None, type=click.Path(path_type=Path))
@click.option("--min-runtime-fills", default=2, type=int)
@click.option("--min-runtime-pnl-delta", default=0.0, type=float)
@click.option("--max-runtime-drawdown-delta", default=0.02, type=float)
def auto_promote_cmd(
    strategy_id,
    candidate_pine,
    evidence_path,
    start,
    end,
    symbol,
    exchange,
    timeframe,
    source,
    shadow_report_path,
    report_path,
    registry_path,
    ledger_path,
    min_runtime_fills,
    min_runtime_pnl_delta,
    max_runtime_drawdown_delta,
):
    """Run register -> paper -> shadow compare -> promote/reject."""
    try:
        report = run_promotion_pipeline(
            strategy_id,
            candidate_pine=candidate_pine,
            evidence_path=evidence_path,
            registry_path=registry_path,
            start=start,
            end=end,
            symbol=symbol,
            exchange=exchange,
            timeframe=timeframe,
            source=source,
            shadow_report_path=shadow_report_path,
            report_path=report_path,
            ledger_path=ledger_path,
            min_runtime_fills=min_runtime_fills,
            min_runtime_pnl_delta=min_runtime_pnl_delta,
            max_runtime_drawdown_delta=max_runtime_drawdown_delta,
        )
    except (DeploymentError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(report, indent=2, sort_keys=True))
    raise SystemExit(0 if report["promoted"] else 1)


@deployment_group.group("approval")
def approval_group():
    """Manage approvals for high-risk deployment actions."""


@approval_group.command("request")
@click.argument("action")
@click.option("--strategy-id", required=True)
@click.option("--approvals", "approvals_path", default=None, type=click.Path(path_type=Path))
def approval_request_cmd(action, strategy_id, approvals_path):
    """Request approval for a high-risk action."""
    req = ApprovalQueue(approvals_path).request(action, {"strategy_id": strategy_id})
    click.echo(json.dumps(req.__dict__, indent=2))


@approval_group.command("approve")
@click.argument("approval_id")
@click.option("--approver", required=True)
@click.option("--approvals", "approvals_path", default=None, type=click.Path(path_type=Path))
def approval_approve_cmd(approval_id, approver, approvals_path):
    """Approve a pending deployment action."""
    try:
        req = ApprovalQueue(approvals_path).approve(approval_id, approver=approver)
    except KeyError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(req.__dict__, indent=2))


@approval_group.command("list")
@click.option("--status", default=None)
@click.option("--approvals", "approvals_path", default=None, type=click.Path(path_type=Path))
def approval_list_cmd(status, approvals_path):
    """List approval requests."""
    rows = [r.__dict__ for r in ApprovalQueue(approvals_path).list(status=status)]
    click.echo(json.dumps(rows, indent=2))
