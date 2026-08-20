"""Server-backed commands for trusted Python strategies."""

from __future__ import annotations

import json
import sys

import click

from quantforge.cli.commands import _http


def _submit(path: str, payload: dict) -> None:
    try:
        click.echo(json.dumps(_http.post(path, json=payload), indent=2))
    except _http.ServerUnreachable as exc:
        click.echo(str(exc), err=True)
        sys.exit(2)


@click.command("backtest")
@click.argument("strategy")
@click.option("--exchange", default="bitget")
@click.option("--symbol", default="BTC/USDT:USDT")
@click.option("--timeframe", default="1h")
@click.option("--period", default="1y")
def backtest_cmd(strategy, exchange, symbol, timeframe, period):
    """Queue a Python strategy backtest."""
    _submit(
        "/backtest/run",
        {
            "strategy": strategy,
            "exchange": exchange,
            "symbol": symbol,
            "timeframe": timeframe,
            "period": period,
        },
    )


@click.command("optimize")
@click.argument("strategy")
@click.option("--exchange", default="bitget")
@click.option("--symbol", default="BTC/USDT:USDT")
@click.option("--timeframe", default="1h")
@click.option("--period", default="1y")
def optimize_cmd(strategy, exchange, symbol, timeframe, period):
    """Queue schema-driven parameter optimization."""
    _submit(
        "/optimize/run",
        {
            "strategy": strategy,
            "exchange": exchange,
            "symbol": symbol,
            "timeframe": timeframe,
            "period": period,
            "mode": "grid",
        },
    )


@click.command("live")
@click.argument("strategy")
@click.option("--exchange", default="bitget")
@click.option("--symbol", default="BTC/USDT:USDT")
@click.option("--timeframe", default="1h")
@click.option("--demo/--no-demo", default=True)
@click.option("--position-size-usdt", default=100, type=float)
@click.option("--leverage", default=1, type=float)
@click.option("--warmup-bars", default=500, type=int)
def live_cmd(strategy, exchange, symbol, timeframe, demo, position_size_usdt, leverage, warmup_bars):
    """Start a registered Python strategy through the risk-controlled server."""
    _submit(
        "/live/start",
        {
            "strategy": strategy,
            "exchange": exchange,
            "symbol": symbol,
            "timeframe": timeframe,
            "demo": demo,
            "position_size_usdt": position_size_usdt,
            "leverage": leverage,
            "warmup_bars": warmup_bars,
        },
    )
