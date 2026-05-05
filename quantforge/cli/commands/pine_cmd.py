"""`quantforge-cli {backtest,optimize,live,transpile}` — wrappers around
quantforge.pine.cli so users have a single CLI entry point that mirrors
every web route.

These commands shell out to `python -m quantforge.pine.cli <sub>` so the
existing argparse-based handler stays the source of truth.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click


def _resolve_pine(name_or_path: str) -> str:
    """Accept either a .pine file path or a bare strategy name."""
    p = Path(name_or_path)
    if p.exists():
        return str(p)
    pine_dir = Path(__file__).resolve().parents[3] / "quantforge" / "pine" / "strategies"
    candidate = pine_dir / f"{name_or_path}.pine"
    if candidate.exists():
        return str(candidate)
    return name_or_path


def _exec_pine(*args: str) -> None:
    cmd = [sys.executable, "-m", "quantforge.pine.cli", *args]
    os.execvp(cmd[0], cmd)


@click.command("backtest", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.argument("pine", required=True)
@click.argument("extra", nargs=-1, type=click.UNPROCESSED)
def backtest_cmd(pine, extra):
    """Run a Pine backtest. Accepts strategy name or .pine file path."""
    _exec_pine("backtest", _resolve_pine(pine), *extra)


@click.command("optimize", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.argument("pine", required=True)
@click.argument("extra", nargs=-1, type=click.UNPROCESSED)
def optimize_cmd(pine, extra):
    """Grid search over input parameters."""
    _exec_pine("optimize", _resolve_pine(pine), *extra)


@click.command("live", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True))
@click.argument("pine", required=True)
@click.argument("extra", nargs=-1, type=click.UNPROCESSED)
def live_cmd(pine, extra):
    """Run a Pine strategy live (paper or real)."""
    _exec_pine("live", _resolve_pine(pine), *extra)
