from click.testing import CliRunner

from quantforge.agent_providers import build_agent_command, resolve_model
from quantforge.cli.commands import _http
from quantforge.cli.main import cli


def test_cli_registers_project_workflows():
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    for command in [
        "agent",
        "api",
        "audit",
        "backtest",
        "dsl",
        "engines",
        "eval",
        "examples",
        "live",
        "optimize",
        "paper",
        "risk",
        "strategies",
        "web",
    ]:
        assert command in result.output


def test_default_http_base_url_points_at_api_prefix(monkeypatch):
    monkeypatch.delenv("QF_API_URL", raising=False)

    assert _http.base_url() == "http://127.0.0.1:8000/api"


def test_api_post_help_is_available():
    result = CliRunner().invoke(cli, ["api", "post", "--help"])

    assert result.exit_code == 0
    assert "JSON payload" in result.output


def test_eval_optimizer_ab_cross_review_help_is_available():
    result = CliRunner().invoke(cli, ["eval", "optimizer-ab", "cross-review", "--help"])

    assert result.exit_code == 0
    assert "cross-review" in result.output


def test_eval_auto_tune_help_is_available():
    result = CliRunner().invoke(cli, ["eval", "auto-tune", "run", "--help"])

    assert result.exit_code == 0
    assert "auto-tune" in result.output


def test_deployment_help_is_available():
    result = CliRunner().invoke(cli, ["deployment", "--help"])

    assert result.exit_code == 0
    assert "strategy deployment registry" in result.output.lower()


def test_deployment_promote_failure_is_clean():
    result = CliRunner().invoke(cli, ["deployment", "promote", "missing"])

    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_quantforge_auto_tune_scheduler_help_is_available():
    result = CliRunner().invoke(cli, ["auto-tune", "run-once", "--help"])

    assert result.exit_code == 0
    assert "QuantForge-owned" in result.output
    assert "--job-file" in result.output
    assert "--auto-deploy" in result.output


def test_web_status_uses_dashboard_app_paths():
    result = CliRunner().invoke(cli, ["web", "status"])

    assert result.exit_code == 0
    assert "apps/dashboard/backend.log" in result.output
    assert "apps/dashboard/frontend.log" in result.output


def test_news_and_control_and_live_promoted_help_are_available():
    for args in [
        ["news", "collect", "--help"],
        ["news", "rss", "--help"],
        ["news", "exchange-status", "--help"],
        ["news", "microstructure", "--help"],
        ["paper", "signal", "--help"],
        ["paper", "shadow-run", "--help"],
        ["paper", "summary", "--help"],
        ["risk", "check", "--help"],
        ["risk", "execution", "--help"],
        ["risk", "live-policy", "--help"],
        ["control", "apply-report", "--help"],
        ["deployment", "approval", "--help"],
        ["deployment", "live-command", "--help"],
        ["deployment", "shadow-compare", "--help"],
        ["deployment", "auto-promote", "--help"],
        ["audit", "build", "--help"],
    ]:
        result = CliRunner().invoke(cli, args)
        assert result.exit_code == 0


def test_risk_check_help_exposes_auto_rollback():
    result = CliRunner().invoke(cli, ["risk", "check", "--help"])

    assert result.exit_code == 0
    assert "--auto-rollback" in result.output
    assert "--registry" in result.output


def test_auto_promote_help_exposes_runtime_ledger_gate():
    result = CliRunner().invoke(cli, ["deployment", "auto-promote", "--help"])

    assert result.exit_code == 0
    assert "--ledger" in result.output
    assert "--min-runtime-fills" in result.output


def test_live_command_help_exposes_live_policy():
    result = CliRunner().invoke(cli, ["deployment", "live-command", "--help"])

    assert result.exit_code == 0
    assert "--policy" in result.output
    assert "--request" in result.output


def test_risk_execution_help_exposes_control_state():
    result = CliRunner().invoke(cli, ["risk", "execution", "--help"])

    assert result.exit_code == 0
    assert "--strategy-id" in result.output
    assert "--control-state" in result.output


def test_codex_agent_command_reads_prompt_from_stdin():
    cmd = build_agent_command(
        "codex",
        None,
        project_dir="/repo",
        max_turns=80,
    )

    assert cmd[:4] == ["codex", "--ask-for-approval", "never", "exec"]
    assert "--json" in cmd
    assert ["--cd", "/repo"] == cmd[cmd.index("--cd") : cmd.index("--cd") + 2]
    assert "--model" not in cmd
    assert cmd[-1] == "-"


def test_claude_model_defaults_to_none():
    assert resolve_model("claude", None) is None
    assert resolve_model("claude", "claude-opus-4-7") == "claude-opus-4-7"


def test_claude_agent_command_omits_model_when_unset():
    cmd = build_agent_command("claude", None, project_dir="/repo", max_turns=80)
    assert "--model" not in cmd


def test_claude_agent_command_includes_model_when_set():
    cmd = build_agent_command(
        "claude", "claude-opus-4-7", project_dir="/repo", max_turns=80
    )
    idx = cmd.index("--model")
    assert cmd[idx + 1] == "claude-opus-4-7"
