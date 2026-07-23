from click.testing import CliRunner

from quantforge.cli.main import cli


def test_cli_exposes_python_strategy_workflows():
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    for command in [
        "agent",
        "api",
        "backtest",
        "control",
        "engines",
        "live",
        "optimize",
        "paper",
        "risk",
        "strategies",
        "web",
    ]:
        assert command in result.output
    for removed in ["dsl", "deployment", "auto-tune"]:
        assert f"  {removed:<11}" not in result.output


def test_strategy_commands_take_registry_names_not_source_files():
    runner = CliRunner()
    for command in ["backtest", "optimize", "live"]:
        result = runner.invoke(cli, [command, "--help"])
        assert result.exit_code == 0
        assert "STRATEGY" in result.output
        assert "pine" not in result.output.lower()


def test_strategy_registry_cli_is_available():
    result = CliRunner().invoke(cli, ["strategies", "list"])
    assert result.exit_code == 0
    assert "ema_crossover" in result.output
    assert "tsla_nvda_options" in result.output
