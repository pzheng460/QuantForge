from __future__ import annotations

import stat

import pytest

from quantforge.brokers.schwab import (
    SchwabConnector,
    SchwabCredentials,
    SchwabTokenStore,
)


def _credentials():
    return SchwabCredentials("key", "secret", "https://localhost/callback")


def test_token_store_uses_user_only_permissions(tmp_path):
    store = SchwabTokenStore(tmp_path / "schwab" / "tokens.json")
    store.save({"access_token": "sensitive", "refresh_token": "refresh"})

    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert store.load()["access_token"] == "sensitive"


@pytest.mark.parametrize(
    ("budget", "price", "shares"), [(1000, 190.25, 5), (190.25, 190.25, 1)]
)
def test_whole_share_sizing(budget, price, shares):
    assert SchwabConnector.shares_for_budget(budget, price) == shares


def test_budget_below_one_share_is_rejected():
    with pytest.raises(ValueError, match="smaller than one share"):
        SchwabConnector.shares_for_budget(100, 190.25)


def test_hour_bars_are_aggregated_from_thirty_minute_candles(tmp_path, monkeypatch):
    connector = SchwabConnector(
        _credentials(),
        token_path=tmp_path / "tokens.json",
        access_token="token",
    )
    monkeypatch.setattr(
        connector,
        "fetch_bars",
        lambda *_args, **_kwargs: [
            {"datetime": 0, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 2},
            {"datetime": 1_800_000, "open": 11, "high": 14, "low": 10, "close": 13, "volume": 3},
        ],
    )

    assert connector.fetch_chart_bars("AAPL", "1h") == [
        [0, 10.0, 14.0, 9.0, 13.0, 5.0]
    ]
