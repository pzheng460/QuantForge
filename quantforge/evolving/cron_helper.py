"""Manage a marker-bracketed block in the user's crontab.

Lifecycle is tied to Evolving Mode:
    - `bot evolving enable`  → install_cron() drops in scheduled lines
    - `bot evolving disable` → remove_cron() pulls them back out

Only lines between the markers below are managed by this module. Anything
else in the user's crontab is left untouched.

    # >>> quantforge-evolving-cron (managed; do not edit) >>>
    */30 * * * * cd /repo && /path/quantforge-cli bot cycle <strategy> ...
    # <<< quantforge-evolving-cron <<<
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


BEGIN_MARKER = "# >>> quantforge-evolving-cron (managed; do not edit) >>>"
END_MARKER = "# <<< quantforge-evolving-cron <<<"

DEFAULT_SCHEDULE = "*/30 * * * *"  # every 30 minutes


def _repo_root() -> Path:
    """Best guess at the QuantForge checkout root for `cd` in the cron line."""
    return Path(__file__).resolve().parents[2]


def _cli_binary() -> str:
    """Best guess at the absolute path of quantforge-cli on this machine."""
    found = shutil.which("quantforge-cli")
    if found:
        return found
    # Fallback: assume it's next to the current python interpreter (venv layout).
    candidate = Path(sys.executable).parent / "quantforge-cli"
    return str(candidate)


def read_crontab() -> str:
    """Return current crontab body, empty string if none."""
    try:
        r = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        raise RuntimeError("crontab binary not found — is cron installed?")
    # `crontab -l` returns exit code 1 + "no crontab for ..." on stderr if
    # the user has none yet. That's fine; we treat it as empty.
    return r.stdout if r.returncode == 0 else ""


def write_crontab(body: str) -> None:
    """Replace the user's crontab with ``body`` (ending in a newline)."""
    if not body.endswith("\n"):
        body += "\n"
    p = subprocess.run(["crontab", "-"], input=body, text=True, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"crontab install failed: {p.stderr.strip()}")


def _strip_block(body: str) -> str:
    """Remove the marker-bracketed block from a crontab body. Idempotent."""
    out: list[str] = []
    in_block = False
    for line in body.splitlines():
        if line.strip() == BEGIN_MARKER:
            in_block = True
            continue
        if line.strip() == END_MARKER:
            in_block = False
            continue
        if not in_block:
            out.append(line)
    return "\n".join(out).rstrip() + ("\n" if out else "")


def is_installed() -> bool:
    """True if our marker block is currently in the user's crontab."""
    return BEGIN_MARKER in read_crontab()


def status() -> dict:
    """Snapshot for `bot evolving status` / `bot cron status`."""
    body = read_crontab()
    if BEGIN_MARKER not in body:
        return {"installed": False, "lines": []}
    in_block = False
    lines: list[str] = []
    for line in body.splitlines():
        if line.strip() == BEGIN_MARKER:
            in_block = True
            continue
        if line.strip() == END_MARKER:
            in_block = False
            continue
        if in_block and line.strip() and not line.strip().startswith("#"):
            lines.append(line)
    return {"installed": True, "lines": lines}


def install(
    strategies: Iterable[str],
    *,
    schedule: str = DEFAULT_SCHEDULE,
    ops_dir: str | Path | None = None,
    alert_webhook_url: str | None = None,
) -> dict:
    """Replace any existing managed block with a fresh one covering ``strategies``.

    Returns the status() of the resulting crontab.
    """
    strategies = list(strategies)
    if not strategies:
        # Nothing to schedule — just remove any prior block.
        return remove()

    repo = _repo_root()
    cli = _cli_binary()
    ops = (
        Path(ops_dir).expanduser() if ops_dir else (Path.home() / ".quantforge" / "ops")
    )
    log = ops / "cron.log"
    webhook = f" --alert-webhook-url {alert_webhook_url}" if alert_webhook_url else ""

    block_lines = [BEGIN_MARKER]
    for s in strategies:
        cmd = f"cd {repo} && {cli} bot cycle {s} --ops-dir {ops}{webhook} >> {log} 2>&1"
        block_lines.append(f"{schedule} {cmd}")
    block_lines.append(END_MARKER)
    block = "\n".join(block_lines) + "\n"

    current = read_crontab()
    cleaned = _strip_block(current)
    write_crontab(cleaned + block if cleaned else block)
    return status()


def remove() -> dict:
    """Remove the managed block, leave the rest of the crontab untouched."""
    current = read_crontab()
    if BEGIN_MARKER not in current:
        return {"installed": False, "lines": []}
    write_crontab(_strip_block(current))
    return {"installed": False, "lines": []}
