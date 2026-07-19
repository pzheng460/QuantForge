"""Bot subsystem CLI — evolving-mode autonomous cycle.

All bot subcommands are gated by ``quantforge.evolving.is_enabled()``. Evolving
Mode is OFF by default so a fresh install can't accidentally start touching
strategies; the user must explicitly opt in via ``bot evolving enable``.

Layout::

    quantforge-cli bot evolving status / enable [--strategy X] / disable [--remove-strategy X]
    quantforge-cli bot status   [<strategy>]
    quantforge-cli bot cycle    <strategy> [--job-file ...] [--alert-webhook-url ...]

The cycle wraps ``quantforge.bot_cycle.run_bot_cycle``: preflight → auto-tune →
risk gate → write trading_control.json → audit report. See CLAUDE.md
"Evolving Mode" section for the full pipeline picture.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from quantforge import evolving


DEFAULT_OPS_DIR = Path.home() / ".quantforge" / "ops"


def _require_evolving(strategy: str | None = None) -> None:
    """Refuse to proceed unless evolving mode is on for this strategy."""
    if evolving.is_enabled(strategy):
        return
    state = evolving.load_state()
    msg = ["Evolving Mode is OFF — bot subsystem is disabled by default."]
    if not state["enabled"]:
        msg.append(
            "Enable with:  quantforge-cli bot evolving enable"
            + (f" --strategy {strategy}" if strategy else "")
        )
    elif strategy and strategy not in state["strategies"]:
        msg.append(
            f"Master switch is on but '{strategy}' is not in the allow-list. "
            f"Add it with:  quantforge-cli bot evolving enable --strategy {strategy}"
        )
    raise click.ClickException("\n".join(msg))


# ─── bot evolving … ──────────────────────────────────────────────────────────


@click.group("bot")
def bot_group():
    """Autonomous strategy auto-tune-and-deploy loop (Evolving Mode)."""


@bot_group.group("evolving")
def evolving_group():
    """Inspect and toggle the Evolving Mode master switch."""


@evolving_group.command("status")
@click.option("--json", "as_json", is_flag=True, help="machine-readable output")
def evolving_status(as_json: bool):
    """Show current Evolving Mode state, including cron install state."""
    state = evolving.load_state()
    cron_state: dict = {"installed": False, "lines": []}
    try:
        from quantforge.evolving import cron_helper

        cron_state = cron_helper.status()
    except (RuntimeError, FileNotFoundError):
        pass

    if as_json:
        click.echo(json.dumps({**state, "cron": cron_state}, indent=2))
        return

    on = "\033[32mON\033[0m" if state["enabled"] else "\033[31mOFF\033[0m"
    click.echo(f"Evolving Mode: {on}")
    if state["strategies"]:
        click.echo(f"  Strategies under control: {', '.join(state['strategies'])}")
    else:
        click.echo("  Strategies under control: (none — applies to all when on)")
    click.echo(f"  State file: {evolving.STATE_PATH}")
    click.echo(f"  Last updated: {state['updated_at']}")
    cron_label = (
        f"\033[32minstalled\033[0m ({len(cron_state['lines'])} line(s))"
        if cron_state["installed"]
        else "\033[33mnot installed\033[0m"
    )
    click.echo(f"  Cron block: {cron_label}")


@evolving_group.command("enable")
@click.option(
    "--strategy",
    "strategies",
    multiple=True,
    help="Strategy to add to allow-list (repeatable)",
)
@click.option(
    "--clear-strategies", is_flag=True, help="Wipe the allow-list before enabling"
)
@click.option("--no-cron", is_flag=True, help="Skip auto-installing the cron block")
@click.option(
    "--schedule",
    default="*/30 * * * *",
    show_default=True,
    help="Cron schedule expression for the cycle",
)
@click.option(
    "--alert-webhook-url", help="Slack/webhook URL to thread into the cron line"
)
def evolving_enable(strategies, clear_strategies, no_cron, schedule, alert_webhook_url):
    """Turn on Evolving Mode and (by default) install the cron block.

    Pass --no-cron to skip the crontab install (the master switch is still
    flipped on; you'd just have to invoke `bot cycle` manually).
    """
    # Validate any new strategy names BEFORE flipping the switch, so a typo
    # like "ema_cross" surfaces immediately instead of silently never matching
    # at Pine engine startup.
    for s in strategies:
        try:
            evolving.validate_strategy_name(s)
        except evolving.UnknownStrategyError as exc:
            raise click.ClickException(str(exc)) from exc

    existing = evolving.load_state()["strategies"]
    if clear_strategies:
        new = list(strategies)
    elif strategies:
        new = list(set(existing) | set(strategies))
    else:
        new = existing
    state = evolving.enable(new if new else None)
    click.echo("\033[32m[✓]\033[0m Evolving Mode is now ON.")
    if state["strategies"]:
        click.echo(f"    Strategies under control: {', '.join(state['strategies'])}")

    if no_cron:
        click.echo("    Cron: not touched (--no-cron). Schedule manually if needed.")
        return
    try:
        from quantforge.evolving import cron_helper

        cron_status = cron_helper.install(
            state["strategies"],
            schedule=schedule,
            alert_webhook_url=alert_webhook_url,
        )
    except RuntimeError as exc:
        click.echo(f"\033[33m[!]\033[0m Could not install cron: {exc}", err=True)
        click.echo(
            "    Evolving Mode is still ON; install cron manually with "
            "'quantforge-cli bot cron install'."
        )
        return
    if cron_status["installed"]:
        click.echo(
            f"\033[32m[✓]\033[0m Installed {len(cron_status['lines'])} cron line(s)"
            f" on schedule '{schedule}'."
        )


@evolving_group.command("disable")
@click.option(
    "--remove-strategy",
    "remove_strategies",
    multiple=True,
    help="Strategy to drop from allow-list",
)
@click.option("--keep-cron", is_flag=True, help="Don't uninstall the cron block")
def evolving_disable(remove_strategies, keep_cron):
    """Turn off Evolving Mode and (by default) remove the cron block.

    Pass --keep-cron if you want to leave the cron lines in place (they'll
    error-exit each tick since the master switch is off — harmless but
    noisy in the cron log).
    """
    for name in remove_strategies:
        evolving.remove_strategy(name)
    state = evolving.disable()
    click.echo("\033[33m[✓]\033[0m Evolving Mode is now OFF.")
    if state["strategies"]:
        click.echo(f"    Allow-list preserved: {', '.join(state['strategies'])}")

    if keep_cron:
        click.echo("    Cron: left in place (--keep-cron).")
        return
    try:
        from quantforge.evolving import cron_helper

        cron_helper.remove()
    except RuntimeError as exc:
        click.echo(f"\033[33m[!]\033[0m Could not touch cron: {exc}", err=True)
        return
    click.echo("\033[32m[✓]\033[0m Cron block removed.")


# ─── bot cron … (explicit lifecycle, optional) ───────────────────────────────


@bot_group.group("cron")
def cron_group():
    """Manage the marker-bracketed cron block independently of evolving toggle."""


@cron_group.command("status")
def cron_status():
    """Show whether our managed cron block is installed."""
    from quantforge.evolving import cron_helper

    s = cron_helper.status()
    if s["installed"]:
        click.echo(f"\033[32mInstalled\033[0m — {len(s['lines'])} line(s):")
        for line in s["lines"]:
            click.echo(f"    {line}")
    else:
        click.echo(
            "\033[33mNot installed\033[0m. Run `bot cron install` or `bot evolving enable`."
        )


@cron_group.command("install")
@click.option("--schedule", default="*/30 * * * *", show_default=True)
@click.option("--alert-webhook-url")
def cron_install_cmd(schedule, alert_webhook_url):
    """Install the cron block for whatever strategies the allow-list currently has."""
    from quantforge.evolving import cron_helper

    state = evolving.load_state()
    if not state["strategies"]:
        raise click.ClickException(
            "No strategies in the Evolving allow-list yet. Run "
            "`bot evolving enable --strategy <name>` first."
        )
    s = cron_helper.install(
        state["strategies"],
        schedule=schedule,
        alert_webhook_url=alert_webhook_url,
    )
    click.echo(f"\033[32m[✓]\033[0m Installed {len(s['lines'])} cron line(s).")


@cron_group.command("uninstall")
def cron_uninstall_cmd():
    """Remove the managed cron block. Leaves the rest of crontab alone."""
    from quantforge.evolving import cron_helper

    cron_helper.remove()
    click.echo("\033[32m[✓]\033[0m Cron block removed.")


# ─── bot status / cycle ──────────────────────────────────────────────────────


@bot_group.command("status")
@click.argument("strategy_id", required=False)
@click.option(
    "--ops-dir",
    type=click.Path(file_okay=False),
    default=str(DEFAULT_OPS_DIR),
    show_default=True,
)
@click.option("--json", "as_json", is_flag=True, help="machine-readable output")
def bot_status(strategy_id, ops_dir, as_json):
    """Snapshot the current bot subsystem state for a strategy.

    Shows: promoted version, paper/shadow candidates, last cycle action,
    pending approvals, ledger summary. Requires Evolving Mode to be on.
    """
    _require_evolving(strategy_id)
    from quantforge.evolving.bot_status import build_bot_status

    ops = Path(ops_dir)
    try:
        report = build_bot_status(
            strategy_id or "",
            registry_path=ops / "deployments.json",
            ledger_path=ops / "paper_ledger.json",
            request_path=ops / "auto_tune_jobs.json",
            approvals_path=ops / "approvals.json",
            control_state_path=ops / "trading_control.json",
        )
    except TypeError:
        # Fallback for older signature variations.
        report = build_bot_status(strategy_id or "")  # type: ignore[call-arg]
    if as_json:
        click.echo(json.dumps(report, indent=2, default=str))
        return
    click.echo(json.dumps(report, indent=2, default=str))


@bot_group.command("cycle")
@click.argument("strategy_id")
@click.option(
    "--ops-dir",
    type=click.Path(file_okay=False),
    default=str(DEFAULT_OPS_DIR),
    show_default=True,
)
@click.option(
    "--mode",
    default="paper",
    show_default=True,
    type=click.Choice(["paper", "shadow", "live"]),
)
@click.option("--alert-jsonl", "alert_jsonl_path", type=click.Path(dir_okay=False))
@click.option("--alert-webhook-url", "alert_webhook_url")
@click.option("--alert-on-success", is_flag=True)
def bot_cycle(
    strategy_id, ops_dir, mode, alert_jsonl_path, alert_webhook_url, alert_on_success
):
    """Run one full preflight → auto-tune → risk → audit cycle.

    Refuses if Evolving Mode is off for this strategy. Writes
    ops_dir/{audit,status,cycle}.json and appends to alerts.jsonl.
    """
    _require_evolving(strategy_id)
    from quantforge.evolving.bot_cycle import run_bot_cycle

    ops = Path(ops_dir)
    ops.mkdir(parents=True, exist_ok=True)
    report = run_bot_cycle(
        strategy_id,
        job_file=ops / "auto_tune_jobs.json",
        mode=mode,
        policy_path=ops / "live_policy.json",
        request_path=ops / "auto_tune_jobs.json",
        approvals_path=ops / "approvals.json",
        registry_path=ops / "deployments.json",
        ledger_path=ops / "paper_ledger.json",
        risk_json_out=ops / "risk.json",
        audit_json_out=ops / "audit.json",
        audit_markdown_out=ops / "audit.md",
        status_json_out=ops / "status.json",
        cycle_json_out=ops / "cycle.json",
        alert_jsonl_path=alert_jsonl_path or (ops / "alerts.jsonl"),
        alert_webhook_url=alert_webhook_url,
        alert_on_success=alert_on_success,
    )
    if not report.get("passed", False):
        click.echo(json.dumps(report, indent=2, default=str), err=True)
        sys.exit(report.get("returncode", 1) or 1)
    click.echo(json.dumps(report, indent=2, default=str))
