"""SchwabExecutionAdapter — the real-money translation boundary.

Locks the invariant that a canonical intent maps to the EXACT Schwab trade
instruction, especially around reduce-only (BUY_TO_COVER / SELL vs BUY /
SELL_SHORT / option *_TO_OPEN / *_TO_CLOSE): flipping one of these would
silently open the opposite position instead of closing. Also locks the
ambiguous-POST → SubmissionOutcomeUnknown mapping so the execution layer
never releases the reservation (no double-fill on retry).
"""

from __future__ import annotations

from datetime import date

import pytest

from quantforge.adapters.schwab import SchwabExecutionAdapter
from quantforge.brokers.schwab import (
    SchwabConnector,
    SchwabCredentials,
    SchwabOrderError,
)
from quantforge.domain.instruments import (
    AssetClass,
    Equity,
    EquityOption,
    InstrumentId,
    OptionRight,
)
from quantforge.domain.intents import (
    MultiLegOrderIntent,
    OrderIntent,
    OrderSide,
    OrderType,
)
from quantforge.execution.service import SubmissionOutcomeUnknown


class _Response:
    def __init__(self, status_code: int, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = ""

    def json(self):
        return {}


class _Session:
    def __init__(self, post_status: int = 201):
        self.calls: list = []
        self.post_status = post_status

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if method == "POST":
            if self.post_status != 201:
                return _Response(self.post_status)
            return _Response(
                201,
                headers={
                    "Location": (
                        "https://api.schwabapi.com/trader/v1/accounts/"
                        "HASH123/orders/987654"
                    )
                },
            )
        return _Response(200)


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
        symbol="AAPL",
    )


def _equity():
    return Equity(id=InstrumentId("AAPL", AssetClass.EQUITY, "schwab"))


def _option(which: str):
    return EquityOption(
        id=InstrumentId(
            f"NVDA  260821{which}", AssetClass.EQUITY_OPTION, "schwab"
        ),
        expiration=date(2026, 8, 21),
        strike=200.0,
        right=OptionRight.CALL,
    )


def _intent(side: OrderSide, *, reduce_only=False, option=None):
    return OrderIntent(
        strategy_id="test",
        instrument=option or _equity(),
        side=side,
        quantity=2,
        order_type=OrderType.LIMIT,
        limit_price=190.25,
        reduce_only=reduce_only,
    )


# ─── Equity instruction mapping (reduce-only is sacred) ──────────────────────

def test_equity_buy_open_maps_to_BUY(tmp_path):
    session = _Session()
    adapter = SchwabExecutionAdapter(_connector(tmp_path, session))
    order_id = adapter.submit(_intent(OrderSide.BUY))

    assert order_id == "987654"
    leg = session.calls[0][2]["json"]["orderLegCollection"][0]
    assert leg["instruction"] == "BUY"
    assert leg["quantity"] == 2


def test_equity_buy_reduce_only_maps_to_BUY_TO_COVER(tmp_path):
    session = _Session()
    adapter = SchwabExecutionAdapter(_connector(tmp_path, session))
    adapter.submit(_intent(OrderSide.BUY, reduce_only=True))

    leg = session.calls[0][2]["json"]["orderLegCollection"][0]
    assert leg["instruction"] == "BUY_TO_COVER"


def test_equity_sell_reduce_only_maps_to_SELL_close_long(tmp_path):
    session = _Session()
    adapter = SchwabExecutionAdapter(_connector(tmp_path, session))
    adapter.submit(_intent(OrderSide.SELL, reduce_only=True))

    leg = session.calls[0][2]["json"]["orderLegCollection"][0]
    assert leg["instruction"] == "SELL"


def test_equity_sell_open_maps_to_SELL_SHORT(tmp_path):
    session = _Session()
    adapter = SchwabExecutionAdapter(_connector(tmp_path, session))
    adapter.submit(_intent(OrderSide.SELL))

    leg = session.calls[0][2]["json"]["orderLegCollection"][0]
    assert leg["instruction"] == "SELL_SHORT"


# ─── Option instruction mapping ──────────────────────────────────────────────

def test_option_sell_to_open_maps_to_SELL_TO_OPEN(tmp_path):
    session = _Session()
    adapter = SchwabExecutionAdapter(_connector(tmp_path, session))
    adapter.submit(_intent(OrderSide.SELL, option=_option("C00200000")))

    payload = session.calls[0][2]["json"]
    assert payload["orderLegCollection"][0]["instruction"] == "SELL_TO_OPEN"
    assert payload["orderLegCollection"][0]["instrument"]["assetType"] == "OPTION"


def test_option_buy_to_close_maps_to_BUY_TO_CLOSE(tmp_path):
    session = _Session()
    adapter = SchwabExecutionAdapter(_connector(tmp_path, session))
    adapter.submit(
        _intent(OrderSide.BUY, reduce_only=True, option=_option("C00200000"))
    )

    leg = session.calls[0][2]["json"]["orderLegCollection"][0]
    assert leg["instruction"] == "BUY_TO_CLOSE"


# ─── Multi-leg option strategy: atomic submission ────────────────────────────

def test_multi_leg_option_strategy_submitted_atomically(tmp_path):
    session = _Session()
    adapter = SchwabExecutionAdapter(_connector(tmp_path, session))
    spread = MultiLegOrderIntent(
        strategy_id="test",
        net_limit_price=-2.5,
        legs=(
            _intent(OrderSide.SELL, option=_option("C00200000")),
            _intent(OrderSide.BUY, option=_option("C00210000")),
        ),
    )

    order_id = adapter.submit(spread)

    assert order_id == "987654"
    payload = session.calls[0][2]["json"]
    assert payload["orderType"] == "NET_CREDIT"
    assert payload["price"] == "2.5"
    assert payload["complexOrderStrategyType"] == "CUSTOM"
    instructions = [leg["instruction"] for leg in payload["orderLegCollection"]]
    assert instructions == ["SELL_TO_OPEN", "BUY_TO_OPEN"]


def test_multi_leg_rejects_non_option_leg(tmp_path):
    session = _Session()
    adapter = SchwabExecutionAdapter(_connector(tmp_path, session))
    spread = MultiLegOrderIntent(
        strategy_id="test",
        net_limit_price=-2.5,
        legs=(
            _intent(OrderSide.SELL, option=_option("C00200000")),
            _intent(OrderSide.BUY),  # equity leg — not allowed in a spread
        ),
    )

    with pytest.raises(ValueError, match="option legs only"):
        adapter.submit(spread)
    assert session.calls == []  # nothing hit the wire


# ─── Outcome mapping: ambiguous POST must become SubmissionOutcomeUnknown ────

def test_ambiguous_post_raises_unknown_outcome(tmp_path):
    session = _Session(post_status=502)
    adapter = SchwabExecutionAdapter(_connector(tmp_path, session))

    with pytest.raises(SubmissionOutcomeUnknown):
        adapter.submit(_intent(OrderSide.BUY))


def test_definitive_rejection_propagates(tmp_path):
    class _RejectSession(_Session):
        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return _Response(400)

    adapter = SchwabExecutionAdapter(_connector(tmp_path, _RejectSession()))

    with pytest.raises(SchwabOrderError):
        adapter.submit(_intent(OrderSide.BUY))


def test_submission_outcome_unknown_is_not_released_by_execution_service(tmp_path):
    """Wire the adapter through ExecutionService: an ambiguous POST must keep
    the risk reservation so a retry of the same intent is blocked as a
    duplicate (double-fill protection)."""
    from quantforge.execution.service import ExecutionService
    from quantforge.portfolio.ledger import PortfolioLedger
    from quantforge.risk.engine import RiskEngine, RiskLimits

    session = _Session(post_status=502)
    adapter = SchwabExecutionAdapter(_connector(tmp_path, session))
    service = ExecutionService(
        risk=RiskEngine(RiskLimits(live_enabled=True, max_order_notional=1_000_000)),
        ledger=PortfolioLedger(cash={"USD": 1_000_000}),
        adapter=adapter,
    )
    intent = _intent(OrderSide.BUY)

    with pytest.raises(SubmissionOutcomeUnknown):
        service.execute(intent)
    assert intent.intent_id in service.risk._authorized
