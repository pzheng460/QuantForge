"""QuantForge-owned news/event collection CLI."""

from __future__ import annotations

import json
from pathlib import Path

import click

from quantforge.news_collector import (
    collect_events,
    collect_exchange_status_events,
    collect_microstructure_events,
    collect_rss_events,
)


@click.group("news")
def news_group():
    """Collect news/events for QuantForge auto-tune."""


@news_group.command("collect")
@click.argument(
    "sources", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path)
)
@click.option("--out", required=True, type=click.Path(path_type=Path))
def collect_cmd(sources, out):
    """Normalize local news/event files into auto-tune JSONL."""
    events = collect_events(list(sources), out)
    click.echo(json.dumps({"events": len(events), "out": str(out)}, indent=2))


@news_group.command("rss")
@click.argument("urls", nargs=-1, required=True)
@click.option("--out", required=True, type=click.Path(path_type=Path))
@click.option(
    "--symbols", default="", help="Comma-separated symbols to attach to each feed item."
)
def rss_cmd(urls, out, symbols):
    """Fetch RSS/Atom feeds into auto-tune JSONL."""
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    events = collect_rss_events(list(urls), out, symbols=symbol_list)
    click.echo(json.dumps({"events": len(events), "out": str(out)}, indent=2))


@news_group.command("exchange-status")
@click.argument("urls", nargs=-1, required=True)
@click.option("--exchange", required=True)
@click.option("--out", required=True, type=click.Path(path_type=Path))
@click.option(
    "--symbols", default="", help="Comma-separated symbols to attach to each event."
)
def exchange_status_cmd(urls, exchange, out, symbols):
    """Fetch exchange status or announcement JSON into auto-tune JSONL."""
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    events = collect_exchange_status_events(
        list(urls), out, exchange=exchange, symbols=symbol_list
    )
    click.echo(json.dumps({"events": len(events), "out": str(out)}, indent=2))


@news_group.command("microstructure")
@click.argument("sources", nargs=-1, required=True)
@click.option("--source-name", default="market")
@click.option("--out", required=True, type=click.Path(path_type=Path))
def microstructure_cmd(sources, source_name, out):
    """Collect funding/open-interest/liquidation JSON into auto-tune JSONL."""
    events = collect_microstructure_events(list(sources), out, source_name=source_name)
    click.echo(json.dumps({"events": len(events), "out": str(out)}, indent=2))
