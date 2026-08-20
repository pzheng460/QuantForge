from click.testing import CliRunner

from quantforge.cli.main import cli


def test_cli_exposes_python_strategy_workflows():
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    for command in [
        "api",
        "backtest",
        "engines",
        "live",
        "optimize",
        "schwab",
        "strategies",
        "web",
    ]:
        assert command in result.output
    for removed in [
        "agent",
        "auto-tune",
        "control",
        "deployment",
        "dsl",
        "paper",
        "risk",
    ]:
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


def test_web_start_refuses_non_loopback_without_api_key(monkeypatch):
    """Binding the backend to 0.0.0.0 without QUANTFORGE_API_KEY must abort:
    the backend would otherwise expose unauthenticated live-trading controls."""
    monkeypatch.delenv("QUANTFORGE_API_KEY", raising=False)
    result = CliRunner().invoke(
        cli, ["web", "start", "--host", "0.0.0.0", "--no-frontend"]
    )
    assert result.exit_code != 0
    assert "QUANTFORGE_API_KEY" in result.output


def test_web_start_refuses_non_loopback_with_whitespace_only_key(monkeypatch):
    """A whitespace-only key must NOT satisfy the bind guard: the backend
    strips the value and would run with auth DISABLED."""
    monkeypatch.setenv("QUANTFORGE_API_KEY", "   ")
    result = CliRunner().invoke(
        cli, ["web", "start", "--host", "0.0.0.0", "--no-frontend"]
    )
    assert result.exit_code != 0
    assert "QUANTFORGE_API_KEY" in result.output
