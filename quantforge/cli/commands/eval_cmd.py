"""CLI wrappers for evaluation harnesses."""

from __future__ import annotations

import os
import sys

import click


def _exec_module(module: str, extra: tuple[str, ...]) -> None:
    cmd = [sys.executable, "-m", module, *extra]
    os.execvp(cmd[0], cmd)


@click.group("eval")
def eval_group():
    """Run QuantForge evaluation harnesses."""


@eval_group.group("optimizer-ab")
def optimizer_ab_group():
    """Run the TiMi-loop optimizer A/B harness."""


@eval_group.group("auto-tune")
def auto_tune_group():
    """Evaluate strategy health and optionally launch re-optimization."""


@auto_tune_group.command(
    "run", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True)
)
@click.argument("extra", nargs=-1, type=click.UNPROCESSED)
def auto_tune_run_cmd(extra):
    """Run eval.auto_tune."""
    _exec_module("eval.auto_tune", ("run", *extra))


@optimizer_ab_group.command(
    "orchestrate",
    context_settings=dict(ignore_unknown_options=True, allow_extra_args=True),
)
@click.argument("extra", nargs=-1, type=click.UNPROCESSED)
def orchestrate_cmd(extra):
    """Run eval.optimizer_ab.orchestrate."""
    _exec_module("eval.optimizer_ab.orchestrate", extra)


@optimizer_ab_group.command(
    "runner", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True)
)
@click.argument("extra", nargs=-1, type=click.UNPROCESSED)
def runner_cmd(extra):
    """Run eval.optimizer_ab.runner."""
    _exec_module("eval.optimizer_ab.runner", extra)


@optimizer_ab_group.command(
    "analyze", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True)
)
@click.argument("extra", nargs=-1, type=click.UNPROCESSED)
def analyze_cmd(extra):
    """Run eval.optimizer_ab.analyze."""
    _exec_module("eval.optimizer_ab.analyze", extra)


@optimizer_ab_group.command(
    "cross-review",
    context_settings=dict(ignore_unknown_options=True, allow_extra_args=True),
)
@click.argument("extra", nargs=-1, type=click.UNPROCESSED)
def cross_review_cmd(extra):
    """Run eval.optimizer_ab.cross_review."""
    _exec_module("eval.optimizer_ab.cross_review", extra)


@optimizer_ab_group.command(
    "holdout", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True)
)
@click.argument("extra", nargs=-1, type=click.UNPROCESSED)
def holdout_cmd(extra):
    """Run eval.optimizer_ab.holdout_eval."""
    _exec_module("eval.optimizer_ab.holdout_eval", extra)


@optimizer_ab_group.command(
    "plot", context_settings=dict(ignore_unknown_options=True, allow_extra_args=True)
)
@click.argument("extra", nargs=-1, type=click.UNPROCESSED)
def plot_cmd(extra):
    """Run eval.optimizer_ab.plot_results."""
    _exec_module("eval.optimizer_ab.plot_results", extra)


@optimizer_ab_group.command(
    "rebuild-csv",
    context_settings=dict(ignore_unknown_options=True, allow_extra_args=True),
)
@click.argument("extra", nargs=-1, type=click.UNPROCESSED)
def rebuild_csv_cmd(extra):
    """Run eval.optimizer_ab.rebuild_csv."""
    _exec_module("eval.optimizer_ab.rebuild_csv", extra)
