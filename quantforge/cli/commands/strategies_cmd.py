"""Inspect registered Python strategies and their published schemas."""

from __future__ import annotations

import json
import sys

import click


def _list_all() -> list[dict]:
    import quantforge.strategies  # noqa: F401
    from quantforge.strategy import list_strategies

    return list_strategies()


@click.group("strategies")
def strategies_group():
    """List and inspect trusted Python strategies."""


@strategies_group.command("list")
@click.option("--json", "as_json", is_flag=True)
def list_cmd(as_json: bool):
    items = _list_all()
    if as_json:
        click.echo(json.dumps(items, indent=2))
        return
    for strategy in items:
        params = ", ".join(f["name"] for f in strategy["config_fields"]) or "(none)"
        click.echo(
            f"{strategy['name']:<24} {strategy['version']:<10} {params}"
        )


@strategies_group.command("show")
@click.argument("name")
@click.option("--json", "as_json", is_flag=True)
def show_cmd(name: str, as_json: bool):
    item = next((value for value in _list_all() if value["name"] == name), None)
    if item is None:
        click.echo(f"strategy '{name}' not found", err=True)
        sys.exit(2)
    if as_json:
        click.echo(json.dumps(item, indent=2))
        return
    click.echo(f"name: {item['name']}")
    click.echo(f"engine: {item['engine']}")
    click.echo(f"version: {item['version']}")
    for field in item["config_fields"]:
        click.echo(f"  {field['name']}: default={field['default']}")
