from __future__ import annotations

import pytest

from quantforge.brokers.schwab import (
    SchwabAmbiguousOrderError,
    SchwabConnector,
    SchwabCredentials,
    SchwabOrderError,
)


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


@pytest.mark.critical
def test_atomic_option_spread_contract(tmp_path):
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

    order = connector.place_option_strategy(
        legs=[
            {
                "symbol": "NVDA  260821C00200000",
                "instruction": "SELL_TO_OPEN",
                "quantity": 1,
            },
            {
                "symbol": "NVDA  260821C00210000",
                "instruction": "BUY_TO_OPEN",
                "quantity": 1,
            },
        ],
        net_limit_price=-2.5,
    )

    assert order.order_id == "987654"
    payload = session.calls[0][2]["json"]
    # Sign convention: net_limit_price is (credit - debit), so a NEGATIVE
    # value is a net DEBIT to the account.
    assert payload["orderType"] == "NET_DEBIT"
    assert payload["price"] == "2.5"
    assert payload["complexOrderStrategyType"] == "CUSTOM"
    assert len(payload["orderLegCollection"]) == 2


@pytest.mark.critical
def test_atomic_option_spread_contract_net_credit(tmp_path):
    """A POSITIVE net_limit_price (net credit to the account) must map to
    the NET_CREDIT orderType (regression: the sign convention was previously
    inverted, so every roll order carried the wrong type)."""
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

    order = connector.place_option_strategy(
        legs=[
            {
                "symbol": "NVDA  260821C00200000",
                "instruction": "SELL_TO_OPEN",
                "quantity": 1,
            },
            {
                "symbol": "NVDA  260821C00210000",
                "instruction": "BUY_TO_OPEN",
                "quantity": 1,
            },
        ],
        net_limit_price=2.5,
    )

    assert order.order_id == "987654"
    payload = session.calls[0][2]["json"]
    assert payload["orderType"] == "NET_CREDIT"
    assert payload["price"] == "2.5"
    assert payload["complexOrderStrategyType"] == "CUSTOM"
    assert len(payload["orderLegCollection"]) == 2


def _connector(tmp_path, session):
    return SchwabConnector(
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


@pytest.mark.critical
@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_order_post_ambiguous_http_status_is_unknown_outcome(tmp_path, status):
    """A rate-limit 429 or gateway 5xx on an order POST must raise
    SchwabAmbiguousOrderError (never a definitive rejection), so the execution
    layer keeps the reservation and cannot double-fill by re-submitting.
    The order may already be live at the broker when such a response is sent.
    """

    class _FailSession(_Session):
        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            if method == "POST":
                return _Response(status)
            return _Response(200, {"status": "FILLED", "orderId": 1})

    session = _FailSession()
    connector = _connector(tmp_path, session)
    with pytest.raises(SchwabAmbiguousOrderError):
        connector.place_order(
            symbol="AAPL",
            instruction="BUY",
            quantity=2,
            order_type="LIMIT",
            price=190.25,
        )


@pytest.mark.critical
def test_order_post_4xx_is_definitive_rejection(tmp_path):
    """A 4xx validation response means Schwab refused the order; the execution
    layer may release the reservation."""

    class _RejectSession(_Session):
        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            if method == "POST":
                return _Response(400)
            return _Response(200, {"status": "FILLED", "orderId": 1})

    session = _RejectSession()
    connector = _connector(tmp_path, session)
    with pytest.raises(SchwabOrderError):
        connector.place_order(
            symbol="AAPL",
            instruction="BUY",
            quantity=2,
            order_type="LIMIT",
            price=190.25,
        )
