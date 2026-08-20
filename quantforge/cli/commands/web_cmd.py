"""Manage the local QuantForge web stack."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
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
    # The services are launched with start_new_session=True, so pid is their
    # process-group id. Kill the WHOLE group: uvicorn --reload runs a
    # reloader + worker, and signalling only the parent used to leave the
    # worker orphaned with the port still bound. Never signal our own group.
    def _signal_group(sig: int) -> None:
        try:
            if os.getpgid(pid) != os.getpgid(0):
                os.killpg(os.getpgid(pid), sig)
                return
        except (OSError, ProcessLookupError):
            pass
        try:
            os.kill(pid, sig)
        except (OSError, ProcessLookupError):
            pass

    _signal_group(signal.SIGTERM)
    pid_file.unlink(missing_ok=True)
    # Short grace period, then escalate: a reloader that ignores SIGTERM must
    # not keep the port bound.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and _is_running(pid):
        time.sleep(0.1)
    if _is_running(pid):
        _signal_group(signal.SIGKILL)
    return True


def _active_api_key() -> str:
    """The API key auth actually relies on: whitespace-only values are NOT a
    key. The backend matches on the trimmed value, so the bind guard must use
    the same definition or a blank key would pass here yet disable auth there.
    """
    return (os.environ.get("QUANTFORGE_API_KEY") or "").strip()


@click.group("web")
def web_group():
    """Start, stop, and inspect the web UI services."""


@web_group.command("start")
@click.option("--backend-port", default=8000, show_default=True)
@click.option("--frontend-port", default=5173, show_default=True)
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--no-frontend", is_flag=True, help="Start only the FastAPI backend.")
def start_cmd(backend_port: int, frontend_port: int, host: str, no_frontend: bool):
    """Start backend and frontend in the background."""
    # A non-loopback bind exposes live-trading controls to the network. The
    # backend only installs API-key auth when a NON-BLANK QUANTFORGE_API_KEY
    # is set (see apps/dashboard/backend/auth.py), so refuse the bind without
    # one (mirrors apps/dashboard/start.sh).
    if host not in ("127.0.0.1", "localhost") and not _active_api_key():
        click.echo(
            f"ERROR: binding to {host} exposes live-trading controls to the "
            "network. QUANTFORGE_API_KEY is empty; refuse to expose the "
            "dashboard unauthenticated. Set QUANTFORGE_API_KEY before binding "
            f"{host}.",
            err=True,
        )
        raise click.Abort()
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
