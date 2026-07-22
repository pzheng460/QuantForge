from __future__ import annotations

from fastapi.testclient import TestClient

from apps.dashboard.backend.main import app
from apps.dashboard.backend.routers import brokers


def test_oauth_callback_rejects_unknown_state():
    response = TestClient(app).get(
        "/api/brokers/schwab/auth/callback", params={"code": "x", "state": "bad"}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid or expired OAuth state"


def test_status_does_not_expose_environment_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("SCHWAB_APP_KEY", "secret-key")
    monkeypatch.setenv("SCHWAB_APP_SECRET", "secret-value")
    monkeypatch.setenv("SCHWAB_CALLBACK_URL", "https://localhost/callback")
    monkeypatch.setattr(brokers, "_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(
        "quantforge.brokers.schwab.SchwabTokenStore.__init__",
        lambda self, path=None: setattr(self, "path", tmp_path / "tokens.json"),
    )

    response = TestClient(app).get("/api/brokers/schwab/status")

    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "authenticated": False,
        "trading_authenticated": False,
        "market_data_authenticated": False,
        "account_selected": False,
    }
    assert "secret" not in response.text
