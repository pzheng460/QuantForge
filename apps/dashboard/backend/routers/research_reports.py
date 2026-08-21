"""Research reports API: serve the latest multi-asset research reports and
trigger an on-demand refresh of the daily pipeline (analysis only — email and
order submission are never triggered from this endpoint).

GET  /api/research/reports  -> {reports: [...], refreshing, last_refresh, last_error}
POST /api/research/refresh  -> {started: bool}  (runs in a background thread)
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(tags=["research"])

_REPORTS_DIR = None


def _reports_dir() -> Path:
    global _REPORTS_DIR
    if _REPORTS_DIR is None:
        from apps.research import config

        _REPORTS_DIR = Path(config.ROOT) / "reports"
    return _REPORTS_DIR


# kind -> filename prefix of the daily report.
_REPORT_KINDS = [
    ("crypto", "crypto_research_"),
    ("options", "options_research_"),
    ("technical", "technical_screen_"),
]

_state: dict = {
    "refreshing": False,
    "last_error": None,
    "last_refresh": None,
    "finished_at": None,
}


def _latest_md(kind: str, prefix: str) -> dict | None:
    root = _reports_dir()
    files = sorted(root.glob(f"{prefix}*.md"))
    if not files:
        return None
    p = files[-1]
    try:
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return {
        "kind": kind,
        "name": p.name,
        "updated_at": mtime,
        "markdown": text,
    }


def _run_refresh() -> None:
    try:
        from apps.research import daily

        summary = daily.run_daily(send_email=False)
        _state["last_refresh"] = summary
        _state["finished_at"] = datetime.now(timezone.utc).isoformat()
        _state["last_error"] = None
    except Exception as exc:  # noqa: BLE001
        _state["last_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _state["refreshing"] = False


@router.get("/research/reports")
def research_reports():
    reports = [
        md for (kind, prefix) in _REPORT_KINDS
        if (md := _latest_md(kind, prefix)) is not None
    ]
    return {
        "reports": reports,
        "refreshing": _state["refreshing"],
        "last_refresh": _state["finished_at"],
        "last_error": _state["last_error"],
    }


@router.post("/research/refresh")
def research_refresh():
    if _state["refreshing"]:
        return {"started": False, "detail": "refresh already running"}
    _state["refreshing"] = True
    _state["last_error"] = None
    threading.Thread(target=_run_refresh, daemon=True).start()
    return {"started": True}
