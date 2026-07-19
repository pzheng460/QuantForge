"""CLI for unified autonomous-operation audit reports."""

from __future__ import annotations

import json
from pathlib import Path

import click

from quantforge.evolving.audit_report import build_audit_report


@click.group("audit")
def audit_group():
    """Build unified audit reports from QuantForge operation artifacts."""


@audit_group.command("build")
@click.argument("strategy_id")
@click.option(
    "--auto-tune", "auto_tune_path", default=None, type=click.Path(path_type=Path)
)
@click.option(
    "--promotion", "promotion_path", default=None, type=click.Path(path_type=Path)
)
@click.option("--shadow", "shadow_path", default=None, type=click.Path(path_type=Path))
@click.option("--risk", "risk_path", default=None, type=click.Path(path_type=Path))
@click.option("--json-out", default=None, type=click.Path(path_type=Path))
@click.option("--markdown-out", default=None, type=click.Path(path_type=Path))
def build_cmd(
    strategy_id,
    auto_tune_path,
    promotion_path,
    shadow_path,
    risk_path,
    json_out,
    markdown_out,
):
    """Build a JSON/Markdown audit report."""
    report = build_audit_report(
        strategy_id,
        auto_tune_path=auto_tune_path,
        promotion_path=promotion_path,
        shadow_path=shadow_path,
        risk_path=risk_path,
        json_out=json_out,
        markdown_out=markdown_out,
    )
    click.echo(json.dumps(report, indent=2, sort_keys=True))
