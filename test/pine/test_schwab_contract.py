from __future__ import annotations

import pytest

from quantforge.brokers.schwab import SchwabConnector, SchwabCredentials


class _Response:
    def __init__(self, status_code: int, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return self._payload


class _Session:
    def __init__(self):
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if method == "POST":
            return _Response(
                201,
                headers={
                    "Location": (
                        "https://api.schwabapi.com/trader/v1/accounts/"
                        "HASH123/orders/987654"
                    )
                },
            )
        return _Response(200, {"status": "FILLED", "orderId": 987654})


@pytest.mark.critical
def test_equity_order_contract_submits_and_normalizes_filled_status(tmp_path):
    session = _Session()
    connector = SchwabConnector(
        credentials=SchwabCredentials(
            app_key="app-key",
            app_secret="app-secret",
            callback_url="https://127.0.0.1:8182/callback",
        ),
        account_hash="HASH123",
        token_path=tmp_path / "tokens.json",
        session=session,
        access_token="access-token",
    )

    submitted = connector.place_order(
        symbol="AAPL",
        instruction="BUY",
        quantity=2,
        order_type="LIMIT",
        price=190.25,
    )
    order = connector.get_order(submitted.order_id)

    assert submitted.order_id == "987654"
    assert order.status == "filled"
    assert session.calls[0] == (
        "POST",
        "https://api.schwabapi.com/trader/v1/accounts/HASH123/orders",
        {
            "headers": {
                "Authorization": "Bearer access-token",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            "json": {
                "orderType": "LIMIT",
                "session": "NORMAL",
                "duration": "DAY",
                "price": "190.25",
                "orderStrategyType": "SINGLE",
                "orderLegCollection": [
                    {
                        "instruction": "BUY",
                        "quantity": 2,
                        "instrument": {"symbol": "AAPL", "assetType": "EQUITY"},
                    }
                ],
            },
            "timeout": 30.0,
        },
    )
