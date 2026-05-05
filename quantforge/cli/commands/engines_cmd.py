"""`quantforge-cli engines ...` — live engine management.

Read-only ops (`list`, `performance`) work standalone by reading the
persistence files the web server writes:
  ~/.quantforge/live/engines.json     — engine configs
  ~/.quantforge/live/<strategy>/live_performance.json   — bar-by-bar P&L

Write ops (`stop`) require the running web server because the asyncio
task lives in the server process. Set QF_API_URL to point at a non-default
server.

`engines start` is currently a thin wrapper around `python -m
quantforge.pine.cli live` so a CLI-started engine doesn't show up in the
web's engine list. Use `engines start --via-server` to start through
the web API instead, which gets you appearance in `engines list`.
"""

from __future__ import annotations

import json
import os
import subprocess
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


def _read_perf(strategy: str) -> dict | None:
    path = LIVE_DIR / strategy / "live_performance.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


@click.group("engines")
def engines_group():
    """Manage live trading engines."""


@engines_group.command("list")
@click.option("--via-server", is_flag=True,
              help="Query the web server's in-memory engine state instead of the persist file.")
@click.option("--json", "as_json", is_flag=True)
def list_cmd(via_server: bool, as_json: bool):
    """List engines (their configs + last persisted performance)."""
    if via_server:
        try:
            engines = _http.get("/live/engines")
        except _http.ServerUnreachable as e:
            click.echo(str(e), err=True)
            sys.exit(2)
    else:
        engines = []
        for cfg in _read_engines():
            perf = _read_perf(cfg["strategy"])
            engines.append({**cfg, "performance": perf})

    if as_json:
        click.echo(json.dumps(engines, indent=2, default=str))
        return
    if not engines:
        click.echo(f"(no engines registered in {ENGINES_FILE})")
        return
    click.echo(f"{'engine_id':<10}  {'strategy':<22}  {'symbol':<18}  {'tf':<4}  status   trades  return%")
    click.echo("-" * 88)
    for e in engines:
        perf = e.get("performance") or {}
        status = e.get("status", "?")
        trades = perf.get("total_trades", "—")
        ret = perf.get("return_pct")
        ret_s = f"{ret*100:+.2f}" if isinstance(ret, (int, float)) else "—"
        click.echo(
            f"{e.get('engine_id', '?'):<10}  {e.get('strategy', '?'):<22}  "
            f"{e.get('symbol', '?'):<18}  {e.get('timeframe', '?'):<4}  "
            f"{status:<7}  {trades:>5}   {ret_s:>6}"
        )


@engines_group.command("performance")
@click.argument("strategy", required=False)
@click.option("--json", "as_json", is_flag=True)
def performance_cmd(strategy: str | None, as_json: bool):
    """Print live performance for one strategy (or all if omitted)."""
    if strategy:
        perf = _read_perf(strategy)
        if perf is None:
            click.echo(f"no live_performance.json for strategy '{strategy}'", err=True)
            sys.exit(2)
        targets = {strategy: perf}
    else:
        targets = {}
        if LIVE_DIR.exists():
            for p in sorted(LIVE_DIR.glob("*/live_performance.json")):
                d = _read_perf(p.parent.name)
                if d:
                    targets[p.parent.name] = d
    if as_json:
        click.echo(json.dumps(targets, indent=2, default=str))
        return
    if not targets:
        click.echo("(no live_performance.json files found)")
        return
    for name, perf in targets.items():
        click.echo(f"\n=== {name} ===")
        click.echo(f"  trades:    {perf.get('total_trades', 0)}  "
                   f"win_rate:  {perf.get('win_rate', 0):.1%}  "
                   f"PF: {perf.get('profit_factor', 0):.2f}")
        click.echo(f"  return:    {perf.get('return_pct', 0)*100:+.2f}%  "
                   f"max_dd:    {perf.get('max_drawdown', 0)*100:.2f}%")
        last_bar = perf.get("last_bar_at")
        if last_bar:
            click.echo(f"  last bar:  {last_bar}")


@engines_group.command("start")
@click.argument("pine_file")
@click.option("--symbol", default="BTC/USDT:USDT")
@click.option("--exchange", default="bitget")
@click.option("--timeframe", default="1h")
@click.option("--demo/--no-demo", default=True)
@click.option("--leverage", type=int, default=1)
@click.option("--position-size", type=float, default=100.0)
@click.option("--warmup-bars", type=int, default=500)
@click.option("--via-server", is_flag=True,
              help="Start through the web API (engine appears in `engines list`). "
                   "Without this flag, runs in the foreground via `pine.cli live`.")
def start_cmd(pine_file, symbol, exchange, timeframe, demo, leverage,
              position_size, warmup_bars, via_server):
    """Start a live engine. Without --via-server runs in foreground."""
    if via_server:
        try:
            res = _http.post(
                "/live/start",
                json={
                    "strategy": Path(pine_file).stem,
                    "symbol": symbol,
                    "exchange": exchange,
                    "timeframe": timeframe,
                    "demo": demo,
                    "leverage": leverage,
                    "position_size_usdt": position_size,
                    "warmup_bars": warmup_bars,
                },
            )
            click.echo(f"started engine_id={res.get('engine_id')} status={res.get('status')}")
        except _http.ServerUnreachable as e:
            click.echo(str(e), err=True)
            sys.exit(2)
        return

    # Foreground via pine.cli live
    cmd = [
        sys.executable, "-m", "quantforge.pine.cli", "live",
        pine_file,
        "--symbol", symbol,
        "--exchange", exchange,
        "--timeframe", timeframe,
        "--leverage", str(leverage),
        "--position-size", str(position_size),
        "--warmup-bars", str(warmup_bars),
    ]
    cmd.append("--demo" if demo else "--no-demo")
    if not demo:
        cmd.append("--confirm-live")
    os.execvp(cmd[0], cmd)


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
