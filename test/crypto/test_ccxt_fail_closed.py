"""Fail-closed leverage/margin-mode and submission-outcome semantics of the
CCXT connector."""

from __future__ import annotations

from datetime import datetime, timezone

import ccxt
import pytest

from quantforge.adapters.ccxt import CcxtConnector, CcxtExecutionAdapter
from quantforge.domain.instruments import (
    AssetClass,
    CryptoPerpetual,
    InstrumentId,
)
from quantforge.domain.intents import OrderIntent, OrderSide
from quantforge.execution import ExecutionService, SubmissionOutcomeUnknown
from quantforge.portfolio.ledger import PortfolioLedger
from quantforge.risk import RiskEngine, RiskLimits


class _StubExchange:
    def __init__(self):
        self.set_leverage_calls: list = []
        self.set_margin_mode_calls: list = []
        self.fail_leverage = False
        self.fail_margin = False
        self.create_order_raises: BaseException | None = None
        self.create_order_result: dict = {"id": "order-1", "status": "filled"}
        # fetch_quote support: a ticker dict to return, or an exception to
        # raise. Defaults to an empty dict (no usable price -> None).
        self.fetch_ticker_result: dict | BaseException = {}

    def set_leverage(self, leverage, symbol):
        self.set_leverage_calls.append((leverage, symbol))
        if self.fail_leverage:
            raise RuntimeError("exchange boom")

    def set_margin_mode(self, mode, symbol):
        self.set_margin_mode_calls.append((mode, symbol))
        if self.fail_margin:
            raise RuntimeError("exchange boom")

    def create_order(self, *args, **kwargs):
        if self.create_order_raises is not None:
            raise self.create_order_raises
        return self.create_order_result

    def fetch_ticker(self, symbol):
        if isinstance(self.fetch_ticker_result, BaseException):
            raise self.fetch_ticker_result
        return self.fetch_ticker_result


@pytest.fixture
def stub():
    return _StubExchange()


def _connector(
    stub: _StubExchange,
    monkeypatch,
    *,
    exchange_id="okx",
    symbol="BTC/USDT:USDT",
    demo=True,
    margin_mode="cross",
) -> CcxtConnector:
    monkeypatch.setattr(CcxtConnector, "_create_exchange", lambda self: stub)
    return CcxtConnector(
        exchange_id=exchange_id,
        symbol=symbol,
        demo=demo,
        margin_mode=margin_mode,
    )


def test_margin_mode_must_be_cross_or_isolated(stub, monkeypatch):
    with pytest.raises(ValueError):
        _connector(stub, monkeypatch, margin_mode="hedge")


def test_ensure_leverage_fails_closed_for_live_derivative(stub, monkeypatch):
    stub.fail_leverage = True
    conn = _connector(stub, monkeypatch, demo=False)
    with pytest.raises(RuntimeError, match="refusing to trade"):
        conn.ensure_leverage(2)
    assert conn._leverage_set is None


def test_ensure_leverage_tolerates_failure_in_demo(stub, monkeypatch):
    stub.fail_leverage = True
    conn = _connector(stub, monkeypatch, demo=True)
    conn.ensure_leverage(2)  # must not raise
    assert conn._leverage_set is None  # not memoized: retried next submit
    conn.ensure_leverage(2)
    assert len(stub.set_leverage_calls) == 2


def test_ensure_leverage_spot_never_fails_closed(stub, monkeypatch):
    stub.fail_leverage = True
    conn = _connector(stub, monkeypatch, symbol="BTC/USDT", demo=False)
    conn.ensure_leverage(1)  # spot has no leverage; must not raise
    assert conn._leverage_set is None


def test_ensure_margin_mode_fails_closed_for_live_derivative(stub, monkeypatch):
    stub.fail_margin = True
    conn = _connector(stub, monkeypatch, demo=False)
    with pytest.raises(RuntimeError, match="refusing to trade at unknown margin mode"):
        conn.ensure_margin_mode()
    assert conn._margin_mode_set is None


def test_ensure_margin_mode_tolerates_failure_in_demo(stub, monkeypatch):
    stub.fail_margin = True
    conn = _connector(stub, monkeypatch, demo=True)
    conn.ensure_margin_mode()  # must not raise
    assert conn._margin_mode_set is None


def test_ensure_margin_mode_on_spot_is_noop(stub, monkeypatch):
    conn = _connector(stub, monkeypatch, symbol="BTC/USDT")
    conn.ensure_margin_mode()
    assert stub.set_margin_mode_calls == []


def test_ensure_margin_mode_bitget_uta_is_per_order_not_api_call(stub, monkeypatch):
    """UTA carries marginMode per order; there is no setMarginMode call, so
    ensure_margin_mode must not touch the exchange at all."""
    conn = _connector(stub, monkeypatch, exchange_id="bitget")
    conn._uta_cached = True
    conn.ensure_margin_mode()
    assert stub.set_margin_mode_calls == []
    assert conn._margin_mode_set == "cross"


def _derivative() -> CryptoPerpetual:
    return CryptoPerpetual(
        id=InstrumentId("BTC/USDT:USDT", AssetClass.CRYPTO_PERPETUAL, "okx")
    )


def _intent() -> OrderIntent:
    return OrderIntent(
        strategy_id="test",
        instrument=_derivative(),
        side=OrderSide.BUY,
        quantity=1,
        quote_bid=60_000,
        quote_ask=60_100,
        quote_timestamp=datetime.now(timezone.utc),
    )


def test_live_network_error_is_submission_outcome_unknown(stub, monkeypatch):
    """A crypto order that dies on the wire may still have been accepted by the
    venue — it must surface as SubmissionOutcomeUnknown (no reservation
    release, no auto-retry), exactly like the Schwab fix."""
    stub.create_order_raises = ccxt.NetworkError("connection reset")
    conn = _connector(stub, monkeypatch, demo=False)
    with pytest.raises(SubmissionOutcomeUnknown):
        conn.submit_market_order("buy", 1, client_order_id="intent-1")


def test_request_timeout_is_submission_outcome_unknown(stub, monkeypatch):
    stub.create_order_raises = ccxt.RequestTimeout("slow venue")
    conn = _connector(stub, monkeypatch, demo=False)
    with pytest.raises(SubmissionOutcomeUnknown):
        conn.submit_limit_order("buy", 1, 60_000, client_order_id="intent-2")


def test_demo_network_error_is_also_unknown_outcome(stub, monkeypatch):
    stub.create_order_raises = ccxt.NetworkError("boom")
    conn = _connector(stub, monkeypatch, demo=True)
    with pytest.raises(SubmissionOutcomeUnknown):
        conn.submit_market_order("buy", 1, client_order_id="intent-3")


def test_definitive_ccxt_rejection_still_propagates(stub, monkeypatch):
    """A venue REJECTION (never sent) keeps propagating so the execution
    service can release the reservation on a genuinely-never-sent order."""
    stub.create_order_raises = ccxt.InvalidOrder("minimum notional 5 USDT")
    conn = _connector(stub, monkeypatch, demo=False)
    with pytest.raises(ccxt.InvalidOrder):
        conn.submit_market_order("buy", 1, client_order_id="intent-4")


def test_unknown_outcome_keeps_risk_reservation(stub, monkeypatch):
    """ExecutionService must NOT release the authorized id / daily-entry
    reservation when the outcome is unknown — a retry of the same intent is
    then blocked as a duplicate instead of double-filling."""
    stub.create_order_raises = ccxt.NetworkError("net")
    conn = _connector(stub, monkeypatch, demo=False)
    service = ExecutionService(
        risk=RiskEngine(RiskLimits(live_enabled=True, max_order_notional=1_000_000)),
        ledger=PortfolioLedger(cash={"USDT": 1_000_000}),
        adapter=CcxtExecutionAdapter(conn),
    )
    intent = _intent()
    with pytest.raises(SubmissionOutcomeUnknown):
        service.execute(intent)
    # Reserved: id still authorized (duplicate on retry) and daily count kept.
    assert intent.intent_id in service.risk._authorized
    day = datetime.now(timezone.utc).date().isoformat()
    assert service.risk._local_entries.get(day) == 1


def test_definitive_rejection_releases_risk_reservation(stub, monkeypatch):
    stub.create_order_raises = ccxt.InvalidOrder("nope")
    conn = _connector(stub, monkeypatch, demo=False)
    service = ExecutionService(
        risk=RiskEngine(RiskLimits(live_enabled=True, max_order_notional=1_000_000)),
        ledger=PortfolioLedger(cash={"USDT": 1_000_000}),
        adapter=CcxtExecutionAdapter(conn),
    )
    intent = _intent()
    with pytest.raises(ccxt.InvalidOrder):
        service.execute(intent)
    assert intent.intent_id not in service.risk._authorized
    day = datetime.now(timezone.utc).date().isoformat()
    assert service.risk._local_entries.get(day, 0) == 0


# ─── Thin-book quote: synthetic price must NOT look fresh ────────────────────

_TS_MS = 1_700_000_000_000


def test_thin_book_quote_carries_no_timestamp(stub, monkeypatch):
    """When the ticker's bid or ask is missing/zero (thin book), fetch_quote
    must NOT stamp the synthetic zero-spread quote with the exchange's real
    timestamp — that would let it sail through the quote-age gate (fresh) and
    the spread gate (spread=0 <= max_spread_pct). Instead carry time=None so
    require_fresh_quote fails closed, mirroring
    SchwabConnector.get_quote_bid_ask."""
    stub.fetch_ticker_result = {
        "bid": None,
        "ask": 60_100,
        "last": 60_050,
        "timestamp": _TS_MS,
    }
    conn = _connector(stub, monkeypatch)
    quote = conn.fetch_quote()
    assert quote is not None
    # Fell back to the last trade price as a price reference...
    assert quote["bid"] == 60_050.0
    assert quote["ask"] == 60_050.0
    # ...but the clincher: NO timestamp on a synthetic quote, so the
    # freshness gate rejects it instead of accepting a fake-fresh quote.
    assert quote["time"] is None


def test_thin_book_quote_zero_bid_carries_no_timestamp(stub, monkeypatch):
    """A zero bid (some venues send 0 instead of None) is the same thin book."""
    stub.fetch_ticker_result = {
        "bid": 0,
        "ask": 60_100,
        "last": 60_050,
        "timestamp": _TS_MS,
    }
    conn = _connector(stub, monkeypatch)
    quote = conn.fetch_quote()
    assert quote is not None
    assert quote["time"] is None
    assert quote["bid"] == quote["ask"] == 60_050.0


def test_real_book_quote_carries_exchange_timestamp(stub, monkeypatch):
    """A genuine two-sided book is stamped with the exchange time so the
    quote-age gate can enforce freshness (and the real spread is checked)."""
    stub.fetch_ticker_result = {
        "bid": 60_000,
        "ask": 60_100,
        "last": 60_050,
        "timestamp": _TS_MS,
    }
    conn = _connector(stub, monkeypatch)
    quote = conn.fetch_quote()
    assert quote is not None
    assert quote["bid"] == 60_000.0
    assert quote["ask"] == 60_100.0
    assert quote["time"] == datetime.fromtimestamp(_TS_MS / 1000, tz=timezone.utc)


def test_thin_book_with_no_last_returns_none(stub, monkeypatch):
    """A thin book with neither bid/ask nor a last price has nothing usable."""
    stub.fetch_ticker_result = {"bid": None, "ask": None, "last": None}
    conn = _connector(stub, monkeypatch)
    assert conn.fetch_quote() is None
