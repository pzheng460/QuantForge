"""
Mock paper-trading simulation with 5x leverage.

Simulates a complete VWAP-like trading session:
  1. Open long  @ 10000  (5x leverage)
  2. Price rises to 11000 → check unrealized PnL
  3. Close long  @ 11000  → check realized PnL & balance
  4. Open short  @ 11000
  5. Price drops to 9000  → check unrealized PnL
  6. Close short @ 9000   → check realized PnL & balance
  7. Attempt to exceed 5x leverage → order rejected
"""

import pickle
import pytest
from decimal import Decimal

from quantforge.base import MockLinearConnector
from quantforge.core.entity import TaskManager
from quantforge.core.nautilius_core import MessageBus, LiveClock
from quantforge.core.cache import AsyncCache
from quantforge.schema import ExchangeType, BookL1, PositionSide
from quantforge.constants import OrderSide, OrderType, OrderStatus
from quantforge.exchange.binance.constants import BinanceAccountType
from nautilus_trader.model.identifiers import TraderId

SYMBOL = "BTCUSDT-PERP.BINANCE"
LEVERAGE = 5
INITIAL_BALANCE = 10000.0
FEE_RATE = 0.0005


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


class PriceFeeder:
    """Controllable price feeder for BookL1."""

    def __init__(self, initial_price: float = 10000.0):
        self.price = initial_price

    def set_price(self, price: float):
        self.price = price

    def get_bookl1(self, symbol: str) -> BookL1:
        return BookL1(
            exchange=ExchangeType.BINANCE,
            symbol="BTCUSDT-PERP",
            bid=self.price,
            ask=self.price,
            bid_size=1,
            ask_size=1,
            timestamp=10000,
        )


# ---------------------------------------------------------------------------
# Full simulation
# ---------------------------------------------------------------------------

async def test_full_trading_simulation_5x_leverage(
    exchange, message_bus, task_manager
):
    """
    End-to-end paper-trading simulation with 5x leverage.

    balance = 10000 USDT, leverage = 5x, fee_rate = 0.05%
    """
    clock = LiveClock()
    cache = AsyncCache(
        strategy_id="sim-5x",
        user_id="test",
        msgbus=message_bus,
        clock=clock,
        task_manager=task_manager,
    )

    feeder = PriceFeeder(10000.0)
    cache.bookl1 = feeder.get_bookl1
    await cache._init_storage()

    conn = MockLinearConnector(
        initial_balance={"USDT": INITIAL_BALANCE, "BTC": 0},
        account_type=BinanceAccountType.LINEAR_MOCK,
        exchange=exchange,
        msgbus=message_bus,
        clock=clock,
        cache=cache,
        task_manager=task_manager,
        overwrite_balance=True,
        overwrite_position=True,
        fee_rate=FEE_RATE,
        quote_currency="USDT",
        update_interval=60,
        leverage=LEVERAGE,
    )
    await conn._init_balance()
    await conn._init_position()

    balance = conn.pnl  # pnl property returns total USDT balance
    assert balance == INITIAL_BALANCE, f"Initial balance should be {INITIAL_BALANCE}"
    print(f"\n{'='*70}")
    print(f"PAPER TRADING SIMULATION — 5x Leverage")
    print(f"{'='*70}")
    print(f"Initial balance: {balance:.2f} USDT")
    print(f"Leverage: {LEVERAGE}x")
    print()

    # ==================================================================
    # STEP 1: Open long 2 BTC @ 10000 (notional=20000, ratio=2.0 < 5x)
    # ==================================================================
    print("--- Step 1: Open LONG 2 BTC @ 10000 ---")
    order = await conn.create_order(
        symbol=SYMBOL, side=OrderSide.BUY,
        type=OrderType.MARKET, amount=Decimal("2"),
    )
    assert order.status == OrderStatus.PENDING, "Long order should succeed (2x < 5x)"

    position = cache.get_position(SYMBOL).unwrap()
    assert position.side == PositionSide.LONG
    assert position.amount == Decimal("2")
    assert position.entry_price == 10000.0

    fee1 = float(order.fee)  # 2 * 10000 * 0.0005 = 10
    expected_fee1 = 2 * 10000 * FEE_RATE
    assert abs(fee1 - expected_fee1) < 0.01, f"Fee should be {expected_fee1}, got {fee1}"

    balance_after_open = conn.pnl
    expected_balance = INITIAL_BALANCE - fee1
    assert abs(balance_after_open - expected_balance) < 0.01
    print(f"  Position: LONG 2 BTC @ 10000")
    print(f"  Fee: {fee1:.2f} USDT")
    print(f"  Balance: {balance_after_open:.2f} USDT")
    print(f"  Notional: {conn.total_notional:.0f}")
    print()

    # ==================================================================
    # STEP 2: Price rises to 11000 → check unrealized PnL
    # ==================================================================
    print("--- Step 2: Price rises to 11000 ---")
    feeder.set_price(11000.0)
    conn._update_unrealized_pnl()

    position = cache.get_position(SYMBOL).unwrap()
    # Long 2 BTC: unrealized = 2 * (11000 - 10000) = +2000
    expected_upnl = 2 * (11000 - 10000)
    assert abs(position.unrealized_pnl - expected_upnl) < 0.01
    print(f"  Unrealized PnL: {position.unrealized_pnl:+.2f} USDT (expected {expected_upnl:+.2f})")
    print(f"  Total notional @ 11000: {conn.total_notional:.0f}")
    print()

    # ==================================================================
    # STEP 3: Close long @ 11000 → realized PnL
    # ==================================================================
    print("--- Step 3: Close LONG 2 BTC @ 11000 ---")
    order = await conn.create_order(
        symbol=SYMBOL, side=OrderSide.SELL,
        type=OrderType.MARKET, amount=Decimal("2"),
    )
    assert order.status == OrderStatus.PENDING, "Close long should succeed"

    position = cache.get_position(SYMBOL).value_or(None)
    assert position is None or position.amount == 0, "Position should be closed"

    fee2 = float(order.fee)  # 2 * 11000 * 0.0005 = 11
    expected_fee2 = 2 * 11000 * FEE_RATE
    assert abs(fee2 - expected_fee2) < 0.01

    # Realized PnL = 2 * (11000 - 10000) = 2000
    realized_pnl_long = 2 * (11000 - 10000)
    balance_after_close_long = conn.pnl
    expected_balance_long = INITIAL_BALANCE + realized_pnl_long - fee1 - fee2
    assert abs(balance_after_close_long - expected_balance_long) < 0.01, \
        f"Balance: expected {expected_balance_long:.2f}, got {balance_after_close_long:.2f}"

    print(f"  Realized PnL: +{realized_pnl_long:.2f} USDT")
    print(f"  Fee: {fee2:.2f} USDT")
    print(f"  Balance: {balance_after_close_long:.2f} USDT")
    print(f"  Total fees so far: {fee1 + fee2:.2f}")
    print()

    # ==================================================================
    # STEP 4: Open short 3 BTC @ 11000 (notional=33000)
    # ==================================================================
    print("--- Step 4: Open SHORT 3 BTC @ 11000 ---")
    order = await conn.create_order(
        symbol=SYMBOL, side=OrderSide.SELL,
        type=OrderType.MARKET, amount=Decimal("3"),
    )
    assert order.status == OrderStatus.PENDING, "Short order should succeed"

    position = cache.get_position(SYMBOL).unwrap()
    assert position.side == PositionSide.SHORT
    assert position.amount == Decimal("3")
    assert position.entry_price == 11000.0

    fee3 = float(order.fee)  # 3 * 11000 * 0.0005 = 16.5
    balance_after_short = conn.pnl
    print(f"  Position: SHORT 3 BTC @ 11000")
    print(f"  Fee: {fee3:.2f} USDT")
    print(f"  Balance: {balance_after_short:.2f} USDT")
    print(f"  Leverage ratio: {conn.total_notional / balance_after_short:.2f}x")
    print()

    # ==================================================================
    # STEP 5: Price drops to 9000 → check unrealized PnL
    # ==================================================================
    print("--- Step 5: Price drops to 9000 ---")
    feeder.set_price(9000.0)
    conn._update_unrealized_pnl()

    position = cache.get_position(SYMBOL).unwrap()
    # Short 3 BTC: unrealized = 3 * (11000 - 9000) = +6000
    expected_upnl_short = 3 * (11000 - 9000)
    assert abs(position.unrealized_pnl - expected_upnl_short) < 0.01
    print(f"  Unrealized PnL: {position.unrealized_pnl:+.2f} USDT (expected {expected_upnl_short:+.2f})")
    print()

    # ==================================================================
    # STEP 6: Close short @ 9000 → realized PnL
    # ==================================================================
    print("--- Step 6: Close SHORT 3 BTC @ 9000 ---")
    order = await conn.create_order(
        symbol=SYMBOL, side=OrderSide.BUY,
        type=OrderType.MARKET, amount=Decimal("3"),
    )
    assert order.status == OrderStatus.PENDING, "Close short should succeed"

    position = cache.get_position(SYMBOL).value_or(None)
    assert position is None or position.amount == 0, "Position should be closed"

    fee4 = float(order.fee)  # 3 * 9000 * 0.0005 = 13.5
    realized_pnl_short = 3 * (11000 - 9000)

    total_fees = fee1 + fee2 + fee3 + fee4
    total_realized = realized_pnl_long + realized_pnl_short
    balance_final = conn.pnl
    expected_final = INITIAL_BALANCE + total_realized - total_fees

    assert abs(balance_final - expected_final) < 0.01, \
        f"Final balance: expected {expected_final:.2f}, got {balance_final:.2f}"

    print(f"  Realized PnL: +{realized_pnl_short:.2f} USDT")
    print(f"  Fee: {fee4:.2f} USDT")
    print(f"  Balance: {balance_final:.2f} USDT")
    print()

    # ==================================================================
    # STEP 7: Attempt to exceed 5x leverage → should be rejected
    # ==================================================================
    print("--- Step 7: Exceed 5x leverage limit ---")
    # balance ~ 17949, try to buy 10 BTC @ 9000 = notional 90000
    # ratio = 90000 / 17949 ≈ 5.01 > 5x → FAIL
    order = await conn.create_order(
        symbol=SYMBOL, side=OrderSide.BUY,
        type=OrderType.MARKET, amount=Decimal("10"),
    )
    assert order.status == OrderStatus.FAILED, "Should reject order exceeding 5x"
    print(f"  Buy 10 BTC @ 9000 (notional=90000): REJECTED (exceeds 5x)")
    print()

    # Buy within limit should still work
    order_ok = await conn.create_order(
        symbol=SYMBOL, side=OrderSide.BUY,
        type=OrderType.MARKET, amount=Decimal("9"),
    )
    assert order_ok.status == OrderStatus.PENDING, "Should allow order within 5x"
    ratio = (9 * 9000) / balance_final
    print(f"  Buy 9 BTC @ 9000 (notional=81000, ratio={ratio:.2f}x): ACCEPTED")
    print()

    # ==================================================================
    # SUMMARY
    # ==================================================================
    print(f"{'='*70}")
    print(f"SIMULATION SUMMARY")
    print(f"{'='*70}")
    print(f"  Initial Balance:      {INITIAL_BALANCE:>12.2f} USDT")
    print(f"  Long  PnL (2 BTC):    {realized_pnl_long:>+12.2f} USDT")
    print(f"  Short PnL (3 BTC):    {realized_pnl_short:>+12.2f} USDT")
    print(f"  Total Realized PnL:   {total_realized:>+12.2f} USDT")
    print(f"  Total Fees:           {total_fees:>12.2f} USDT")
    print(f"  Final Balance:        {balance_final:>12.2f} USDT")
    print(f"  Expected Balance:     {expected_final:>12.2f} USDT")
    print(f"  Match: {'YES' if abs(balance_final - expected_final) < 0.01 else 'NO'}")
    print(f"  Leverage Limit:       Enforced (5x)")
    print(f"{'='*70}")
