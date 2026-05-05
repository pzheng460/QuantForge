"""`quantforge-cli agent ...` — LLM agent (Claude Code) workflows.

Stateless ops (`skills`) walk ~/.openclaw/skills/ directly.

`agent run` spawns Claude Code as a subprocess, like the web `/agent/run`
endpoint does — but unlike the web flow, runs in the foreground and
streams events to stdout. For job-tracked async runs (status, stop)
that survive across CLI invocations, use --via-server which goes through
the web API and lets the server manage the subprocess.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import click

from . import _http

SKILLS_DIR = Path.home() / ".openclaw" / "skills"

try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # type: ignore[assignment]


def _load_yaml(path: Path) -> dict:
    if _yaml is None:
        # Best-effort fallback: handle simple key: value lines so we can at
        # least show the skill name + description without a hard dep.
        out: dict = {}
        for line in path.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or ":" not in line:
                continue
            k, _, v = line.partition(":")
            v = v.strip()
            if v and not v.startswith("|") and not v.startswith(">"):
                out[k.strip()] = v
        return out
    return _yaml.safe_load(path.read_text())


def _list_skills() -> list[dict]:
    out: list[dict] = []
    if not SKILLS_DIR.exists():
        return out
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir():
            continue
        wf = d / "workflow.yaml"
        if not wf.exists():
            continue
        try:
            data = _load_yaml(wf)
        except Exception:
            continue
        out.append(
            {
                "name": d.name,
                "description": data.get("description", "").strip(),
                "defaults": data.get("defaults", {}),
                "path": str(d),
            }
        )
    return out


@click.group("agent")
def agent_group():
    """Run and inspect LLM agent workflows."""


@agent_group.command("skills")
@click.option("--json", "as_json", is_flag=True)
def skills_cmd(as_json: bool):
    """List available agent skills (workflow.yaml found under ~/.openclaw/skills/)."""
    items = _list_skills()
    if as_json:
        click.echo(json.dumps(items, indent=2))
        return
    if not items:
        click.echo(f"(no workflow.yaml found under {SKILLS_DIR})")
        return
    click.echo(f"{'name':<28}  description")
    click.echo("-" * 88)
    for s in items:
        desc = (s["description"] or "").splitlines()[0][:60] if s["description"] else ""
        click.echo(f"{s['name']:<28}  {desc}")


@agent_group.command("run")
@click.option("--skill", required=True, help="Skill name (e.g. quantforge-optimizer)")
@click.option("--strategy", default=None, help="Pine strategy name (e.g. ema_crossover)")
@click.option("--symbol", default="BTC/USDT:USDT")
@click.option("--exchange", default="bitget")
@click.option("--timeframe", default="1h")
@click.option("--max-iterations", type=int, default=5)
@click.option("--model", default="claude-sonnet-4-20250514")
@click.option("--via-server", is_flag=True,
              help="Submit through web API (job tracked, recoverable). "
                   "Default runs Claude Code in foreground.")
def run_cmd(skill, strategy, symbol, exchange, timeframe, max_iterations, model, via_server):
    """Run an agent workflow (foreground by default, or --via-server)."""
    if via_server:
        try:
            res = _http.post(
                "/agent/run",
                json={
                    "skill_path": skill,
                    "strategy": strategy,
                    "symbol": symbol,
                    "exchange": exchange,
                    "timeframe": timeframe,
                    "max_iterations": max_iterations,
                    "model": model,
                },
            )
            click.echo(json.dumps(res, indent=2))
        except _http.ServerUnreachable as e:
            click.echo(str(e), err=True)
            sys.exit(2)
        return

    skill_dir = SKILLS_DIR / skill
    wf = skill_dir / "workflow.yaml"
    if not wf.exists():
        click.echo(f"skill not found: {skill_dir}", err=True)
        sys.exit(2)
    workflow = _load_yaml(wf)

    template = workflow.get("prompt_template", "")
    if not template:
        click.echo(f"workflow.yaml in {skill_dir} has no prompt_template", err=True)
        sys.exit(2)

    project_dir = Path("/home/pzheng46/QuantForge")
    work_path = ""
    output_path = ""
    strategy_path = ""
    if strategy:
        strategy_name = strategy.removesuffix(".pine")
        strategy_path = f"quantforge/pine/strategies/{strategy_name}.pine"
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        work_path = f"/tmp/{strategy_name}_work_{ts}.pine"
        output_path = f"quantforge/pine/strategies/optimized/{strategy_name}_optimized_{ts}.pine"

    prompt = template.format(
        skill_path=skill_dir,
        strategy_path=strategy_path,
        work_path=work_path,
        output_path=output_path,
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        max_iterations=max_iterations,
    )
    cmd = [
        "claude", "--print", "--verbose",
        "--permission-mode", "bypassPermissions",
        "--output-format", "stream-json",
        "--model", model,
        "--max-turns", "80",
        "-p", prompt,
    ]
    click.echo(f"[agent] running skill={skill} strategy={strategy or '(none)'} model={model}")
    proc = subprocess.run(cmd, cwd=str(project_dir))
    sys.exit(proc.returncode)


@agent_group.command("status")
@click.argument("job_id")
def status_cmd(job_id: str):
    """Get status of a server-tracked agent job. Requires --via-server submission."""
    try:
        res = _http.get(f"/agent/{job_id}")
        click.echo(json.dumps(res, indent=2))
    except _http.ServerUnreachable as e:
        click.echo(str(e), err=True)
        sys.exit(2)


@agent_group.command("stop")
@click.argument("job_id")
def stop_cmd(job_id: str):
    """Stop a server-tracked agent job."""
    try:
        res = _http.post(f"/agent/{job_id}/stop")
        click.echo(json.dumps(res, indent=2))
    except _http.ServerUnreachable as e:
        click.echo(str(e), err=True)
        sys.exit(2)
