from quantforge.config import (
    Config,
    PublicConnectorConfig,
    BasicConfig,
)
from quantforge.strategy import Strategy
from quantforge.constants import ExchangeType
from quantforge.constants import KlineInterval
from quantforge.exchange import OkxAccountType
from quantforge.schema import Kline
from quantforge.engine import Engine


class Demo(Strategy):
    def __init__(self):
        super().__init__()
        self.signal = True

    def on_start(self):
        self.subscribe_kline(
            "BTCUSDT-PERP.OKX", interval=KlineInterval.SECOND_1, use_aggregator=True
        )

    def on_kline(self, kline: Kline):
        self.log.info(str(kline))


config = Config(
    strategy_id="okx_subscribe_klines",
    user_id="user_test",
    strategy=Demo(),
    basic_config={
        ExchangeType.OKX: BasicConfig(
            testnet=True,
        )
    },
    public_conn_config={
        ExchangeType.OKX: [
            PublicConnectorConfig(
                account_type=OkxAccountType.DEMO,
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
