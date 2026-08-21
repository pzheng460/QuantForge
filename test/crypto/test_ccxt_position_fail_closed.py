"""Position-query fail-closed semantics for real-money engine startup.

Two layers, two different answers:

* the CONNECTOR (``CcxtConnector.get_position``) is still fail-closed: a
  transient query failure raises ``CcxtPositionError`` instead of returning
  ``None``, so callers can never silently treat an unknown position as flat;

* the ENGINE build path (``_build_runtime``) seeds the ledger from the
  engine's OWN recorded trade history — NOT from the venue account. On a
  shared-account deployment (several engines on one venue account) an engine
  must never claim a holding another engine or a manual operator placed,
  otherwise it could reduce-only close someone else's position. A flat engine
  therefore starts flat even when the account holds a position, and a broker
  position-query failure no longer blocks startup (the engine's position does
  not come from that query).
"""

from __future__ import annotations

import ccxt
import pytest

from quantforge.adapters.ccxt import CcxtConnector, CcxtPositionError

from apps.dashboard.backend.live_engines import _build_runtime

_SYMBOL = "BTC/USDT:USDT"


class _StubExchange:
    def __init__(self, market: dict | None = None):
        self._market = market or {
            "id": "BTCUSDT",
            "symbol": _SYMBOL,
            "base": "BTC",
            "quote": "USDT",
            "settle": "USDT",
            "type": "swap",
            "swap": True,
            "linear": True,
            "contractSize": 1,
            "precision": {"amount": 0.0, "price": 1.0},
            "limits": {"amount": {"min": 0.001}, "leverage": {"max": 3}},
            "active": True,
        }
        self.fetch_positions_result: list | BaseException = []
        self.uta_position_raises: BaseException | None = None
        self.uta_position_data: dict | None = None

    def market(self, symbol: str) -> dict:
        return self._market

    def fetch_positions(self, symbols=None):
        if isinstance(self.fetch_positions_result, BaseException):
            raise self.fetch_positions_result
        return list(self.fetch_positions_result)

    def privateUtaGetV3PositionCurrentPosition(self, params: dict) -> dict:
        if self.uta_position_raises is not None:
            raise self.uta_position_raises
        return {"data": self.uta_position_data}


@pytest.fixture
def stub():
    return _StubExchange()


def _connector(stub, monkeypatch, *, exchange_id="okx", demo=True) -> CcxtConnector:
    monkeypatch.setattr(CcxtConnector, "_create_exchange", lambda self: stub)
    return CcxtConnector(
        exchange_id=exchange_id,
        symbol=_SYMBOL,
        demo=demo,
    )


# ─── Connector level: query failure ≠ flat ───────────────────────────────────

def test_position_query_failure_is_not_flat(stub, monkeypatch):
    stub.fetch_positions_result = ccxt.NetworkError("venue unreachable")
    conn = _connector(stub, monkeypatch)

    with pytest.raises(CcxtPositionError, match="cannot treat as flat"):
        conn.get_position()


def test_position_query_failure_is_explicit_runtime_error(stub, monkeypatch):
    """Callers catching Exception above CcxtPositionError still see a
    RuntimeError subclass — never a bare return of None."""
    assert issubclass(CcxtPositionError, RuntimeError)


def test_position_query_empty_is_flat(stub, monkeypatch):
    conn = _connector(stub, monkeypatch)
    assert conn.get_position() is None


def test_position_query_zero_contract_rows_are_flat(stub, monkeypatch):
    stub.fetch_positions_result = [
        {"contracts": 0.0, "side": "long", "entryPrice": 60_000, "unrealizedPnl": 0},
    ]
    conn = _connector(stub, monkeypatch)
    assert conn.get_position() is None


def test_position_query_long_is_parsed(stub, monkeypatch):
    stub.fetch_positions_result = [
        {"contracts": 0.5, "side": "long", "entryPrice": 60_000, "unrealizedPnl": 12.5},
    ]
    conn = _connector(stub, monkeypatch)
    pos = conn.get_position()
    assert pos == {
        "side": "long",
        "contracts": 0.5,
        "entryPrice": 60_000.0,
        "unrealizedPnl": 12.5,
    }


def test_uta_position_query_failure_is_not_flat(stub, monkeypatch):
    stub.uta_position_raises = ccxt.RequestTimeout("uta timeout")
    conn = _connector(stub, monkeypatch, exchange_id="bitget")
    conn._uta_cached = True  # force the UTA path

    with pytest.raises(CcxtPositionError, match="cannot treat as flat"):
        conn.get_position()


def test_uta_position_query_parses_hold_side(stub, monkeypatch):
    stub.uta_position_data = {
        "list": [
            {
                "total": 0.3,
                "holdSide": "short",
                "openPriceAvg": "61000",
                "unrealisedPL": "-40",
            }
        ]
    }
    conn = _connector(stub, monkeypatch, exchange_id="bitget")
    conn._uta_cached = True

    pos = conn.get_position()
    assert pos["side"] == "short"
    assert pos["contracts"] == 0.3
    assert pos["entryPrice"] == 61000.0
    assert pos["unrealizedPnl"] == -40.0


def test_uta_position_query_empty_data_is_flat(stub, monkeypatch):
    stub.uta_position_data = None
    conn = _connector(stub, monkeypatch, exchange_id="bitget")
    conn._uta_cached = True

    assert conn.get_position() is None


# ─── Build-time reconciliation: own trades only, never claim the account ────

def test_build_runtime_is_flat_without_own_trades(stub, monkeypatch):
    """With no trade history the engine owns nothing."""
    monkeypatch.setattr(CcxtConnector, "_create_exchange", lambda self: stub)

    engine = _build_runtime(
        strategy_name="ema_crossover",
        config_override={},
        exchange="okx",
        symbol=_SYMBOL,
        timeframe="1h",
        demo=False,
        position_size=500,
        leverage=2,
        warmup_bars=100,
        risk_limits={"max_order_notional": 1000},
        trades=[],
    )

    assert engine.execution.ledger.positions == {}


def test_build_runtime_ignores_account_position_without_own_trades(
    stub, monkeypatch
):
    """The broker holding a position (e.g. another engine's on a shared
    account) must NOT seed this engine's ledger — that would let it
    reduce-only close someone else's position."""
    stub.fetch_positions_result = [
        {"contracts": 0.5, "side": "long", "entryPrice": 60_000, "unrealizedPnl": 0}
    ]
    monkeypatch.setattr(CcxtConnector, "_create_exchange", lambda self: stub)

    engine = _build_runtime(
        strategy_name="ema_crossover",
        config_override={},
        exchange="okx",
        symbol=_SYMBOL,
        timeframe="1h",
        demo=False,
        position_size=500,
        leverage=2,
        warmup_bars=100,
        risk_limits={"max_order_notional": 1000},
        trades=[],
    )

    assert engine.execution.ledger.positions == {}


def test_build_runtime_position_query_failure_still_starts(stub, monkeypatch):
    """Broker position-query failures no longer block startup: the engine's
    position comes from its OWN trade history, not the venue query."""
    stub.fetch_positions_result = ccxt.NetworkError("venue unreachable")
    monkeypatch.setattr(CcxtConnector, "_create_exchange", lambda self: stub)

    engine = _build_runtime(
        strategy_name="ema_crossover",
        config_override={},
        exchange="okx",
        symbol=_SYMBOL,
        timeframe="1h",
        demo=False,
        position_size=500,
        leverage=2,
        warmup_bars=100,
        risk_limits={"max_order_notional": 1000},
        trades=[],
    )

    assert engine.execution.ledger.positions == {}


def test_build_runtime_seeds_ledger_from_long_trades(stub, monkeypatch):
    trades = [
        {
            "time": "2026-01-01T00:00:00+00:00",
            "side": "buy",
            "quantity": 0.3,
            "price": 60_000,
            "order_id": "a",
            "close": False,
        },
        {
            "time": "2026-01-01T01:00:00+00:00",
            "side": "buy",
            "quantity": 0.2,
            "price": 61_000,
            "order_id": "b",
            "close": False,
        },
    ]
    monkeypatch.setattr(CcxtConnector, "_create_exchange", lambda self: stub)

    engine = _build_runtime(
        strategy_name="ema_crossover",
        config_override={},
        exchange="okx",
        symbol=_SYMBOL,
        timeframe="1h",
        demo=False,
        position_size=500,
        leverage=2,
        warmup_bars=100,
        risk_limits={"max_order_notional": 1000},
        trades=trades,
    )

    pos = next(iter(engine.execution.ledger.positions.values()))
    assert pos.quantity == 0.5
    assert pos.average_price == pytest.approx(60_400)


def test_build_runtime_seeds_ledger_from_short_trades(stub, monkeypatch):
    trades = [
        {
            "time": "2026-01-01T00:00:00+00:00",
            "side": "sell",
            "quantity": 0.3,
            "price": 61_000,
            "order_id": "a",
            "close": False,
        }
    ]
    monkeypatch.setattr(CcxtConnector, "_create_exchange", lambda self: stub)

    engine = _build_runtime(
        strategy_name="ema_crossover",
        config_override={},
        exchange="okx",
        symbol=_SYMBOL,
        timeframe="1h",
        demo=False,
        position_size=500,
        leverage=2,
        warmup_bars=100,
        risk_limits={"max_order_notional": 1000},
        trades=trades,
    )

    pos = next(iter(engine.execution.ledger.positions.values()))
    assert pos.quantity == -0.3


def test_build_runtime_net_position_handles_open_and_close(stub, monkeypatch):
    """A partial close nets down the owned position (buy 0.4, close 0.1 → 0.3)."""
    trades = [
        {
            "time": "2026-01-01T00:00:00+00:00",
            "side": "buy",
            "quantity": 0.4,
            "price": 60_000,
            "order_id": "a",
            "close": False,
        },
        {
            "time": "2026-01-01T02:00:00+00:00",
            "side": "sell",
            "quantity": 0.1,
            "price": 62_000,
            "order_id": "b",
            "close": True,
        },
    ]
    monkeypatch.setattr(CcxtConnector, "_create_exchange", lambda self: stub)

    engine = _build_runtime(
        strategy_name="ema_crossover",
        config_override={},
        exchange="okx",
        symbol=_SYMBOL,
        timeframe="1h",
        demo=False,
        position_size=500,
        leverage=2,
        warmup_bars=100,
        risk_limits={"max_order_notional": 1000},
        trades=trades,
    )

    pos = next(iter(engine.execution.ledger.positions.values()))
    assert pos.quantity == pytest.approx(0.3)
