"""QuantForge CLI — every web feature available from the terminal.

Stateless commands (strategies, exchanges, engines list/perf, agent
skills/run) work without the web server. Stateful commands (engines stop,
agent status/stop) hit the web API at ``$QF_API_URL`` (default
``http://127.0.0.1:8000/api``).
"""

import click

from quantforge.cli.commands.agent_cmd import agent_group
from quantforge.cli.commands.api_cmd import api_group
from quantforge.cli.commands.audit_cmd import audit_group
from quantforge.cli.commands.auto_tune_cmd import auto_tune_group
from quantforge.cli.commands.bot_cmd import bot_group
from quantforge.cli.commands.control_cmd import control_group
from quantforge.cli.commands.deployment_cmd import deployment_group
from quantforge.cli.commands.dsl_cmd import dsl_cmd
from quantforge.cli.commands.engines_cmd import engines_group
from quantforge.cli.commands.eval_cmd import eval_group
from quantforge.cli.commands.examples_cmd import examples_group
from quantforge.cli.commands.exchanges_cmd import exchanges_group
from quantforge.cli.commands.news_cmd import news_group
from quantforge.cli.commands.paper_cmd import paper_group
from quantforge.cli.commands.pine_cmd import backtest_cmd, live_cmd, optimize_cmd
from quantforge.cli.commands.risk_cmd import risk_group
from quantforge.cli.commands.strategies_cmd import strategies_group
from quantforge.cli.commands.web_cmd import web_group


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    help=__doc__,
)
def cli():
    """QuantForge CLI root."""


# Register subgroups + thin wrappers around quantforge.pine.cli.
cli.add_command(strategies_group)
cli.add_command(exchanges_group)
cli.add_command(engines_group)
cli.add_command(agent_group)
cli.add_command(auto_tune_group)
cli.add_command(bot_group)
cli.add_command(control_group)
cli.add_command(deployment_group)
cli.add_command(audit_group)
cli.add_command(api_group)
cli.add_command(web_group)
cli.add_command(dsl_cmd)
cli.add_command(examples_group)
cli.add_command(news_group)
cli.add_command(paper_group)
cli.add_command(risk_group)
cli.add_command(eval_group)
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
