from decimal import Decimal

from quantforge.constants import settings
from quantforge.config import (
    Config,
    PublicConnectorConfig,
    PrivateConnectorConfig,
    BasicConfig,
)
from quantforge.strategy import Strategy
from quantforge.constants import ExchangeType, OrderSide, OrderType, KlineInterval
from quantforge.exchange import BinanceAccountType
from quantforge.schema import BookL1, Order
from quantforge.engine import Engine


BINANCE_API_KEY = settings.BINANCE.FUTURE.TESTNET_1.API_KEY
BINANCE_SECRET = settings.BINANCE.FUTURE.TESTNET_1.SECRET


class Demo(Strategy):
    def __init__(self):
        super().__init__()
        self.signal = True

    def on_start(self):
        self.subscribe_bookl1(symbols=["BTCUSDT-PERP.BINANCE"])
        self.subscribe_kline(
            symbols="BTCUSDT-PERP.BINANCE", interval=KlineInterval.MINUTE_1
        )

    def on_failed_order(self, order: Order):
        self.log.info(str(order))

    def on_pending_order(self, order: Order):
        self.log.info(str(order))

    def on_accepted_order(self, order: Order):
        self.log.info(str(order))

    def on_filled_order(self, order: Order):
        self.log.info(str(order))

    def on_bookl1(self, bookl1: BookL1):
        if self.signal:
            symbol = "BTCUSDT-PERP.BINANCE"
            self.create_tp_sl_order(
                symbol=symbol,
                side=OrderSide.BUY,
                type=OrderType.MARKET,
                amount=Decimal("0.001"),
                tp_order_type=OrderType.MARKET,
                tp_trigger_price=self.price_to_precision(
                    symbol, price=bookl1.ask * 1.01
                ),
                sl_order_type=OrderType.MARKET,
                sl_trigger_price=self.price_to_precision(
                    symbol, price=bookl1.bid * 0.99
                ),
            )
            self.signal = False


config = Config(
    strategy_id="bnc_tp_sl_order",
    user_id="user_test",
    strategy=Demo(),
    basic_config={
        ExchangeType.BINANCE: BasicConfig(
            api_key=BINANCE_API_KEY,
            secret=BINANCE_SECRET,
            testnet=True,
        )
    },
    public_conn_config={
        ExchangeType.BINANCE: [
            PublicConnectorConfig(
                account_type=BinanceAccountType.USD_M_FUTURE_TESTNET,
                enable_rate_limit=True,
            )
        ]
    },
    private_conn_config={
        ExchangeType.BINANCE: [
            PrivateConnectorConfig(
                account_type=BinanceAccountType.USD_M_FUTURE_TESTNET,
                enable_rate_limit=True,
            )
        ]
    },
)

engine = Engine(config)

if __name__ == "__main__":
    try:
        engine.start()
    finally:
        engine.dispose()
