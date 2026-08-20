"""QuantForge CLI — every web feature available from the terminal.

Stateless commands (strategies, exchanges, and engine inspection) work
without the web server. Stateful commands hit the web API at
``$QF_API_URL`` (default ``http://127.0.0.1:8000/api``).
"""

import click

from quantforge.cli.commands.api_cmd import api_group
from quantforge.cli.commands.engines_cmd import engines_group
from quantforge.cli.commands.exchanges_cmd import exchanges_group
from quantforge.cli.commands.schwab_cmd import schwab_group
from quantforge.cli.commands.strategies_cmd import strategies_group
from quantforge.cli.commands.strategy_run_cmd import (
    backtest_cmd,
    live_cmd,
    optimize_cmd,
)
from quantforge.cli.commands.web_cmd import web_group


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    help=__doc__,
)
def cli():
    """QuantForge CLI root."""


# Register supported command groups.
cli.add_command(strategies_group)
cli.add_command(exchanges_group)
cli.add_command(engines_group)
cli.add_command(api_group)
cli.add_command(web_group)
cli.add_command(schwab_group)
cli.add_command(backtest_cmd)
cli.add_command(optimize_cmd)
cli.add_command(live_cmd)


# `quantforge` binary (pyproject [project.scripts]) is an alias for the
# top-level help. The CLI body lives on `quantforge-cli`.
quantforge = cli


def main():
    """Entry point for the ``quantforge-cli`` console script."""
    cli()


if __name__ == "__main__":
    main()
