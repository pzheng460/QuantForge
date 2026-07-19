"""Manage the local QuantForge web stack."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import click


ROOT = Path(__file__).resolve().parents[3]
WEB_DIR = ROOT / "apps" / "dashboard"
BACKEND_PID = WEB_DIR / ".backend.pid"
FRONTEND_PID = WEB_DIR / ".frontend.pid"
BACKEND_LOG = WEB_DIR / "backend.log"
FRONTEND_LOG = WEB_DIR / "frontend.log"


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _is_running(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _stop(pid_file: Path) -> bool:
    pid = _read_pid(pid_file)
    if not _is_running(pid):
        pid_file.unlink(missing_ok=True)
        return False
    assert pid is not None
    os.kill(pid, signal.SIGTERM)
    pid_file.unlink(missing_ok=True)
    return True


@click.group("web")
def web_group():
    """Start, stop, and inspect the web UI services."""


@web_group.command("start")
@click.option("--backend-port", default=8000, show_default=True)
@click.option("--frontend-port", default=5173, show_default=True)
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--no-frontend", is_flag=True, help="Start only the FastAPI backend.")
def start_cmd(backend_port: int, frontend_port: int, host: str, no_frontend: bool):
    """Start backend and frontend in the background."""
    stop_cmd.callback()
    WEB_DIR.mkdir(exist_ok=True)

    backend_log = BACKEND_LOG.open("w")
    backend = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "apps.dashboard.backend.main:app",
            "--host",
            host,
            "--port",
            str(backend_port),
            "--reload",
        ],
        cwd=str(ROOT),
        stdout=backend_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    BACKEND_PID.write_text(str(backend.pid))
    click.echo(f"backend pid={backend.pid} http://localhost:{backend_port}")

    if not no_frontend:
        frontend_log = FRONTEND_LOG.open("w")
        frontend = subprocess.Popen(
            ["npx", "vite", "--host", host, "--port", str(frontend_port)],
            cwd=str(ROOT / "apps" / "dashboard" / "frontend"),
            stdout=frontend_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        FRONTEND_PID.write_text(str(frontend.pid))
        click.echo(f"frontend pid={frontend.pid} http://localhost:{frontend_port}")


@web_group.command("stop")
def stop_cmd():
    """Stop locally started web services."""
    stopped = []
    if _stop(BACKEND_PID):
        stopped.append("backend")
    if _stop(FRONTEND_PID):
        stopped.append("frontend")
    click.echo("stopped: " + (", ".join(stopped) if stopped else "none"))


@web_group.command("status")
def status_cmd():
    """Show web service process status."""
    for name, pid_file, log in [
        ("backend", BACKEND_PID, BACKEND_LOG),
        ("frontend", FRONTEND_PID, FRONTEND_LOG),
    ]:
        pid = _read_pid(pid_file)
        state = "running" if _is_running(pid) else "stopped"
        click.echo(f"{name:<8} {state:<8} pid={pid or '-'} log={log}")
