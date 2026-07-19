"""Wrapper for the declarative strategy runner."""

from __future__ import annotations

import os
import sys

import click


@click.command(
    "dsl", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True)
)
@click.argument("extra", nargs=-1, type=click.UNPROCESSED)
def dsl_cmd(extra):
    """Run quantforge.dsl.runner through the main CLI."""
    cmd = [sys.executable, "-m", "quantforge.dsl.runner", *extra]
    os.execvp(cmd[0], cmd)
