"""Test leverage enforcement in MockLinearConnector."""

import pickle
import pytest
from decimal import Decimal

from quantforge.base import MockLinearConnector
from quantforge.core.entity import TaskManager
from quantforge.core.nautilius_core import MessageBus, LiveClock
from quantforge.core.cache import AsyncCache
from quantforge.schema import ExchangeType, BookL1
from quantforge.constants import OrderSide, OrderType, OrderStatus
from quantforge.exchange.binance.constants import BinanceAccountType
from nautilus_trader.model.identifiers import TraderId


@pytest.fixture(scope="session")
def event_loop_policy():
    import asyncio
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture
def market():
    return pickle.load(open("./test/test_data/market.pkl", "rb"))


@pytest.fixture
def market_id():
    return pickle.load(open("./test/test_data/market_id.pkl", "rb"))


@pytest.fixture
def task_manager(event_loop_policy):
    loop = event_loop_policy.new_event_loop()
    return TaskManager(loop, enable_signal_handlers=False)


@pytest.fixture
def message_bus():
    return MessageBus(trader_id=TraderId("TEST-001"), clock=LiveClock())


@pytest.fixture
def exchange(market, market_id):
    class MockExchangeManager:
        def __init__(self):
            self.market = market
            self.market_id = market_id
            self.exchange_id = ExchangeType.BINANCE

    return MockExchangeManager()


@pytest.fixture
def bookl1_10000():
    return BookL1(
        exchange=ExchangeType.BINANCE,
        symbol="BTCUSDT-PERP",
        bid=10000,
        ask=10000,
        bid_size=1,
        ask_size=1,
        timestamp=10000,
    )


async def _make_connector(
    leverage, exchange, message_bus, task_manager, bookl1_10000
):
    clock = LiveClock()
    cache = AsyncCache(
        strategy_id="test-leverage",
        user_id="test-user",
        msgbus=message_bus,
        clock=clock,
        task_manager=task_manager,
    )
    cache.bookl1 = lambda symbol: bookl1_10000
    await cache._init_storage()

    conn = MockLinearConnector(
        initial_balance={"USDT": 10000, "BTC": 0},
        account_type=BinanceAccountType.LINEAR_MOCK,
        exchange=exchange,
        msgbus=message_bus,
        clock=clock,
        cache=cache,
        task_manager=task_manager,
        overwrite_balance=True,
        overwrite_position=True,
        fee_rate=0.0005,
        quote_currency="USDT",
        update_interval=60,
        leverage=leverage,
    )
    await conn._init_balance()
    await conn._init_position()
    return conn


# ---- 1x leverage tests ----


async def test_1x_buy_1btc_succeeds(
    exchange, message_bus, task_manager, bookl1_10000
):
    """1x leverage: buy 1 BTC (notional=10000, ratio=1.0) should succeed."""
    conn = await _make_connector(1, exchange, message_bus, task_manager, bookl1_10000)
    order = await conn.create_order(
        symbol="BTCUSDT-PERP.BINANCE",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=Decimal("1"),
    )
    assert order.status == OrderStatus.PENDING


async def test_1x_buy_2btc_fails(
    exchange, message_bus, task_manager, bookl1_10000
):
    """1x leverage: buy 2 BTC (notional=20000, ratio=2.0) should fail."""
    conn = await _make_connector(1, exchange, message_bus, task_manager, bookl1_10000)
    order = await conn.create_order(
        symbol="BTCUSDT-PERP.BINANCE",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=Decimal("2"),
    )
    assert order.status == OrderStatus.FAILED


# ---- 5x leverage tests ----


async def test_5x_buy_2btc_succeeds(
    exchange, message_bus, task_manager, bookl1_10000
):
    """5x leverage: buy 2 BTC (notional=20000, ratio=2.0) should succeed."""
    conn = await _make_connector(5, exchange, message_bus, task_manager, bookl1_10000)
    order = await conn.create_order(
        symbol="BTCUSDT-PERP.BINANCE",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=Decimal("2"),
    )
    assert order.status == OrderStatus.PENDING


async def test_5x_buy_5btc_succeeds(
    exchange, message_bus, task_manager, bookl1_10000
):
    """5x leverage: buy 5 BTC (notional=50000, ratio=5.0) should succeed."""
    conn = await _make_connector(5, exchange, message_bus, task_manager, bookl1_10000)
    order = await conn.create_order(
        symbol="BTCUSDT-PERP.BINANCE",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=Decimal("5"),
    )
    assert order.status == OrderStatus.PENDING


async def test_5x_buy_6btc_fails(
    exchange, message_bus, task_manager, bookl1_10000
):
    """5x leverage: buy 6 BTC (notional=60000, ratio=6.0) should fail."""
    conn = await _make_connector(5, exchange, message_bus, task_manager, bookl1_10000)
    order = await conn.create_order(
        symbol="BTCUSDT-PERP.BINANCE",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=Decimal("6"),
    )
    assert order.status == OrderStatus.FAILED


# ---- 10x leverage test ----


async def test_10x_buy_10btc_succeeds(
    exchange, message_bus, task_manager, bookl1_10000
):
    """10x leverage: buy 10 BTC (notional=100000, ratio=10.0) should succeed."""
    conn = await _make_connector(10, exchange, message_bus, task_manager, bookl1_10000)
    order = await conn.create_order(
        symbol="BTCUSDT-PERP.BINANCE",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        amount=Decimal("10"),
    )
    assert order.status == OrderStatus.PENDING


# ---- Multi-order accumulation tests ----


async def test_5x_short_accumulate_within_limit(
    exchange, message_bus, task_manager, bookl1_10000
):
    """5x leverage: short 2 + short 2 BTC (total=40000, ratio<5.0) both succeed.

    Note: first order's fee reduces available balance, so we use a total
    that stays comfortably under 5x even after fees.
    """
    conn = await _make_connector(5, exchange, message_bus, task_manager, bookl1_10000)
    o1 = await conn.create_order(
        symbol="BTCUSDT-PERP.BINANCE",
        side=OrderSide.SELL,
        type=OrderType.MARKET,
        amount=Decimal("2"),
    )
    o2 = await conn.create_order(
        symbol="BTCUSDT-PERP.BINANCE",
        side=OrderSide.SELL,
        type=OrderType.MARKET,
        amount=Decimal("2"),
    )
    assert o1.status == OrderStatus.PENDING
    assert o2.status == OrderStatus.PENDING


async def test_5x_short_accumulate_exceeds_limit(
    exchange, message_bus, task_manager, bookl1_10000
):
    """5x leverage: short 3 + short 3 BTC (total=60000, ratio=6.0) 2nd fails."""
    conn = await _make_connector(5, exchange, message_bus, task_manager, bookl1_10000)
    o1 = await conn.create_order(
        symbol="BTCUSDT-PERP.BINANCE",
        side=OrderSide.SELL,
        type=OrderType.MARKET,
        amount=Decimal("3"),
    )
    o2 = await conn.create_order(
        symbol="BTCUSDT-PERP.BINANCE",
        side=OrderSide.SELL,
        type=OrderType.MARKET,
        amount=Decimal("3"),
    )
    assert o1.status == OrderStatus.PENDING
    assert o2.status == OrderStatus.FAILED
