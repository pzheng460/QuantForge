"""The raw Schwab account_hash credential must never leave the server.

Previously /brokers/schwab/accounts returned account.__dict__ verbatim, and
/account selected by that raw hash — leaking the auth credential to any API-key
holder in the UI. Now the API exposes only a one-way account_ref (SHA-256
prefix); the server resolves the ref back to the real hash internally.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from apps.dashboard.backend.main import app
from quantforge.brokers.schwab import SchwabAccount


class _FakeAccountsConnector:
    def __init__(self, accounts):
        self._accounts = accounts

    def get_accounts(self):
        return self._accounts


def test_accounts_are_masked_and_selectable_by_ref(monkeypatch, tmp_path):
    import apps.dashboard.backend.routers.brokers as brokers

    secret_hash = "ultra-secret-schwab-account-hash-1234567890"
    accounts = [
        SchwabAccount(
            account_hash=secret_hash,
            account_type="INDIVIDUAL",
            display_id="4321",
        ),
        SchwabAccount(
            account_hash="another-account-hash",
            account_type="MARGIN",
            display_id="1111",
        ),
    ]
    monkeypatch.setattr(
        brokers, "_connector", lambda: _FakeAccountsConnector(accounts)
    )
    monkeypatch.setattr(brokers, "_CONFIG_PATH", tmp_path / "config.json")

    with TestClient(app) as client:
        listed = client.get("/api/brokers/schwab/accounts").json()

    assert len(listed) == 2
    for entry in listed:
        assert "account_hash" not in entry
        assert entry["account_ref"]
        assert entry["display_id"]

    first = listed[0]
    assert first["account_type"] == "INDIVIDUAL"
    ref = first["account_ref"]
    # The ref is a one-way digest — not a transformed echo of the raw value.
    assert ref != secret_hash
    assert secret_hash not in json.dumps(listed)

    with TestClient(app) as client:
        resp = client.post("/api/brokers/schwab/account", json={"account_ref": ref})
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"selected": True, "display_id": "4321"}
        bad = client.post(
            "/api/brokers/schwab/account", json={"account_ref": "deadbeef"}
        )
        assert bad.status_code == 400

    # The server persisted the REAL hash — it just never crossed the wire.
    persisted = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert persisted == {"account_hash": secret_hash}
