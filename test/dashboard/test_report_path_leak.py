"""The options report endpoints must not leak the server's absolute disk path.

OptionReportStore.save returns an absolute Path under the operator's home
directory; echoing it to the client leaks the filesystem layout (and the
home directory name). The endpoints now return only the file's basename, which
is all the frontend uses as an opaque audit label.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from apps.dashboard.backend.main import app
from apps.dashboard.backend.routers import risk_options as router
from quantforge.options import OptionsDailyReport


def _fake_report() -> OptionsDailyReport:
    return OptionsDailyReport(
        strategy="tsla_nvda_options",
        ticker="NVDA",
        action="HOLD",
        reasons=("no actionable signal",),
        generated_at="2026-07-25T10:00:00Z",
        data_quality="ok",
    )


def test_report_name_returns_basename_only():
    """_report_name must reduce an absolute home-dir path to its file name."""
    abs_path = Path("/home/secretuser/.quantforge/options/reports/NVDA/20260725T100000000000Z.json")
    name = router._report_name(abs_path)
    assert name == "20260725T100000000000Z.json"
    # No path separators and no home/user leakage.
    assert "/" not in name
    assert "secretuser" not in name
    assert "quantforge" not in name


def test_report_name_accepts_string_input():
    name = router._report_name(
        "/home/secretuser/.quantforge/options/reports/NVDA/20260725T100000000000Z.json"
    )
    assert name == "20260725T100000000000Z.json"


def test_analyze_endpoint_does_not_leak_absolute_report_path(monkeypatch, tmp_path):
    """The /options/schwab/analyze response's report_path must be a bare file
    name, never the absolute on-disk path (which contains the home dir)."""
    # Redirect the report store root into a temp dir so no real disk state is
    # touched and the saved path is deterministic.
    monkeypatch.setattr(
        "quantforge.options.engine.OptionReportStore.__init__",
        lambda self, root=None: setattr(self, "root", tmp_path / "reports") or None,
    )

    # Stub _schwab_analysis so no real Schwab connection is made: return a
    # report that will be persisted, plus throwaway candidates/ledger/connector.
    def fake_analysis(request):
        report = _fake_report()
        return report, [], None, None, None

    monkeypatch.setattr(router, "_schwab_analysis", fake_analysis)

    with TestClient(app) as client:
        r = client.post(
            "/api/options/schwab/analyze",
            json={
                "ticker": "NVDA",
                "as_of": "2026-07-25",
                "minimum_core_shares": 0,
                "maximum_covered_ratio": 1,
                "trend_state": "横盘",
            },
        )
    assert r.status_code == 200
    body = r.json()
    report_path = body["report_path"]
    # Must be a bare filename, not an absolute path.
    assert "/" not in report_path
    assert report_path.endswith(".json")
    assert "home" not in report_path
    assert ".quantforge" not in report_path
    assert report_path == Path(report_path).name


def test_run_once_no_op_path_does_not_leak_report_path(monkeypatch, tmp_path):
    """When run-once has no executable action it still persists a report and
    returns report_path — that path must also be a bare filename."""
    monkeypatch.setattr(
        "quantforge.options.engine.OptionReportStore.__init__",
        lambda self, root=None: setattr(self, "root", tmp_path / "reports") or None,
    )

    def fake_analysis(request):
        report = _fake_report()  # action="HOLD" -> not executable
        return report, [], None, None, None

    monkeypatch.setattr(router, "_schwab_analysis", fake_analysis)

    with TestClient(app) as client:
        r = client.post(
            "/api/options/schwab/run-once",
            json={
                "ticker": "NVDA",
                "as_of": "2026-07-25",
                "minimum_core_shares": 0,
                "maximum_covered_ratio": 1,
                "trend_state": "横盘",
                "demo": True,
            },
        )
    assert r.status_code == 200
    report_path = r.json()["report_path"]
    assert "/" not in report_path
    assert report_path.endswith(".json")
    assert ".quantforge" not in report_path
    assert r.json()["receipt"] is None  # HOLD -> no order submitted
