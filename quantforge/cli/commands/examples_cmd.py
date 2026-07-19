"""Discover and run bundled exchange examples."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click


ROOT = Path(__file__).resolve().parents[3]
EXAMPLES_DIR = ROOT / "examples"


def _examples() -> list[Path]:
    if not EXAMPLES_DIR.exists():
        return []
    return sorted(EXAMPLES_DIR.glob("*/*.py"))


def _resolve(name: str) -> Path | None:
    p = Path(name)
    if p.exists():
        return p
    normalized = name.removesuffix(".py")
    for example in _examples():
        rel = example.relative_to(EXAMPLES_DIR).with_suffix("")
        if str(rel) == normalized or rel.name == normalized:
            return example
    return None


@click.group("examples")
def examples_group():
    """List and run bundled exchange examples."""


@examples_group.command("list")
@click.argument("exchange", required=False)
def list_cmd(exchange: str | None):
    """List example scripts, optionally filtered by exchange."""
    for example in _examples():
        rel = example.relative_to(EXAMPLES_DIR)
        if exchange and rel.parts[0] != exchange:
            continue
        click.echo(str(rel.with_suffix("")))


@examples_group.command(
    "run", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True)
)
@click.argument("name")
@click.argument("extra", nargs=-1, type=click.UNPROCESSED)
def run_cmd(name: str, extra):
    """Run an example by path, stem, or exchange/name."""
    example = _resolve(name)
    if example is None:
        raise click.ClickException(f"example not found: {name}")
    cmd = [sys.executable, str(example), *extra]
    os.execvp(cmd[0], cmd)
