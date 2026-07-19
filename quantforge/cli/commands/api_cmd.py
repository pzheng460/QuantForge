"""Generic HTTP access to the QuantForge web API."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click
import requests

from . import _http


def _normalise_path(path: str) -> str:
    return path if path.startswith("/") else f"/{path}"


def _load_json(value: str | None, file_path: str | None) -> Any:
    if value and file_path:
        raise click.ClickException("Use either --json or --json-file, not both.")
    if file_path:
        return json.loads(Path(file_path).read_text())
    if value:
        return json.loads(value)
    return None


def _print_response(data: Any) -> None:
    click.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))


@click.group("api")
def api_group():
    """Call any QuantForge web API route from the terminal."""


@api_group.command("base-url")
def base_url_cmd():
    """Print the effective API base URL."""
    click.echo(_http.base_url())


@api_group.command("get")
@click.argument("path")
def get_cmd(path: str):
    """GET a web API path, for example /health or /strategies."""
    try:
        _print_response(_http.get(_normalise_path(path)))
    except (requests.HTTPError, _http.ServerUnreachable) as e:
        click.echo(str(e), err=True)
        sys.exit(2)


@api_group.command("post")
@click.argument("path")
@click.option("--json", "json_value", help="JSON payload as a string.")
@click.option(
    "--json-file",
    type=click.Path(exists=True, dir_okay=False),
    help="Read JSON payload from a file.",
)
def post_cmd(path: str, json_value: str | None, json_file: str | None):
    """POST to a web API path with an optional JSON payload."""
    try:
        payload = _load_json(json_value, json_file)
        kwargs = {"json": payload} if payload is not None else {}
        _print_response(_http.post(_normalise_path(path), **kwargs))
    except json.JSONDecodeError as e:
        click.echo(f"Invalid JSON: {e}", err=True)
        sys.exit(2)
    except (requests.HTTPError, _http.ServerUnreachable) as e:
        click.echo(str(e), err=True)
        sys.exit(2)
