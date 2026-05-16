"""Agent provider command construction for optimizer workflows."""

from __future__ import annotations

from pathlib import Path

AGENT_PROVIDERS = ("claude", "codex")

DEFAULT_MODELS = {
    "claude": None,
    "codex": None,
}


def normalize_provider(provider: str | None) -> str:
    value = (provider or "claude").strip().lower()
    if value not in AGENT_PROVIDERS:
        raise ValueError(f"agent provider must be one of {AGENT_PROVIDERS}")
    return value


def resolve_model(provider: str | None, model: str | None) -> str | None:
    provider = normalize_provider(provider)
    if model:
        return model
    return DEFAULT_MODELS[provider]


def build_agent_command(
    provider: str | None,
    model: str | None,
    *,
    project_dir: str | Path,
    max_turns: int = 80,
) -> list[str]:
    """Build a non-interactive coding-agent command that reads prompt on stdin."""
    provider = normalize_provider(provider)
    model = resolve_model(provider, model)
    if provider == "claude":
        cmd = [
            "claude",
            "--print",
            "--verbose",
            "--permission-mode",
            "bypassPermissions",
            "--output-format",
            "stream-json",
            "--max-turns",
            str(max_turns),
        ]
        if model:
            cmd.extend(["--model", model])
        return cmd
    cmd = [
        "codex",
        "--ask-for-approval",
        "never",
        "exec",
        "--json",
        "--cd",
        str(project_dir),
        "--sandbox",
        "danger-full-access",
    ]
    if model:
        cmd.extend(["--model", model])
    cmd.append("-")
    return cmd
