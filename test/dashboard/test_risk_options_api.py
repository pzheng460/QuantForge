from __future__ import annotations

from fastapi.testclient import TestClient

from apps.dashboard.backend.main import app


def test_global_risk_api_and_options_analysis(monkeypatch, tmp_path):
    import apps.dashboard.backend.routers.risk_options as router
    from quantforge.risk.control import GlobalRiskControl

    monkeypatch.setattr(
        router, "_GLOBAL_RISK", GlobalRiskControl(tmp_path / "global.json")
    )
    with TestClient(app) as client:
        halted = client.put(
            "/api/risk/global",
            json={"halted": True, "reason": "operator test"},
        )
        assert halted.status_code == 200
        assert client.get("/api/risk/global").json()["halted"] is True

        report = client.post(
            "/api/options/analyze",
            json={
                "ticker": "NVDA",
                "as_of": "2026-07-25",
                "shares": 100,
                "minimum_core_shares": 0,
                "maximum_covered_ratio": 1,
                "stock_price": 170,
                "trend_state": "横盘",
                "earnings_date": "2026-09-20",
                "earnings_confirmed": True,
                "candidates": [
                    {
                        "symbol": "NVDA_CALL",
                        "strike": 190,
                        "expiration": "2026-08-21",
                        "bid": 3,
                        "ask": 3.1,
                        "delta": 0.2,
                        "open_interest": 1000,
                        "volume": 100,
                    }
                ],
            },
        )
        assert report.status_code == 200
        assert report.json()["action"] == "开 Covered Call"


def test_run_once_notional_ceiling_is_server_enforced(monkeypatch, tmp_path):
    """max_order_notional above the server-side ceiling must be rejected with
    422 before any order path is reached (the client may not raise its own
    notional cap on a real-money endpoint)."""
    from apps.dashboard.backend.routers import risk_options as router

    base = {
        "ticker": "NVDA",
        "as_of": "2026-07-25",
        "minimum_core_shares": 0,
        "maximum_covered_ratio": 1,
        "trend_state": "横盘",
        "earnings_date": "2026-09-20",
        "earnings_confirmed": True,
        "demo": True,
    }
    over = dict(base, max_order_notional=router._RUN_ONCE_MAX_NOTIONAL_USD + 1)
    with TestClient(app) as client:
        response = client.post("/api/options/schwab/run-once", json=over)
    assert response.status_code == 422

    under = dict(base, max_order_notional=router._RUN_ONCE_MAX_NOTIONAL_USD)
    assert under["max_order_notional"] > 0


def test_ticker_that_could_escape_report_path_is_rejected(monkeypatch, tmp_path):
    """A ticker with path separators must be refused with 422 before any
    analysis/store/order path is reached (OptionReportStore keys reports as
    <root>/<ticker>/<stamp>.json)."""
    from apps.dashboard.backend.routers import risk_options as router

    monkeypatch.setattr(
        router, "_GLOBAL_RISK", router.GlobalRiskControl(tmp_path / "global.json")
    )
    base = {
        "ticker": "../evil",
        "as_of": "2026-07-25",
        "minimum_core_shares": 0,
        "maximum_covered_ratio": 1,
        "trend_state": "横盘",
        "earnings_date": "2026-09-20",
        "earnings_confirmed": True,
        "config": {},
    }
    with TestClient(app) as client:
        assert (
            client.post("/api/options/analyze", json={**base, "shares": 100, "stock_price": 170}).status_code
            == 422
        )
        assert (
            client.post("/api/options/schwab/run-once", json={**base, "demo": True}).status_code
            == 422
        )
    # Defense in depth: even a caller that skips the API models cannot make
    # the store write outside its root.
    from quantforge.options import OptionsDailyReport, OptionReportStore

    store = OptionReportStore(tmp_path / "reports")
    bad = OptionsDailyReport(
        strategy="tsla_nvda_options",
        ticker="..\\escape",
        action="HOLD",
        reasons=(),
        generated_at="2026-07-25T10:00:00Z",
        data_quality="ok",
    )
    try:
        store.save(bad)
    except ValueError as exc:
        assert "invalid ticker" in str(exc)
    else:
        raise AssertionError("OptionReportStore.save must reject path-escape tickers")
    assert not (tmp_path / "reports" / "..").exists()
