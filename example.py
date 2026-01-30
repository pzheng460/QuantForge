import sys
from decimal import Decimal

from nexustrader.constants import settings
from nexustrader.config import Config, PublicConnectorConfig, PrivateConnectorConfig, BasicConfig
from nexustrader.strategy import Strategy
from nexustrader.constants import ExchangeType, OrderSide, OrderType
from nexustrader.exchange import BitgetAccountType
from nexustrader.schema import BookL1, Order
from nexustrader.engine import Engine
from nexustrader.config import LogConfig


class LogTee:
    """Writes output to both stdout and a log file"""
    def __init__(self, filename):
        self.terminal = sys.stdout
        self.log = open(filename, "w", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
        self.log.flush()

    def flush(self):
        self.terminal.flush()
        self.log.flush()

    def close(self):
        self.log.close()


# Redirect stdout to both terminal and log file
sys.stdout = LogTee("output.log")

# Retrieve API credentials from settings
API_KEY = settings.BITGET.DEMO.API_KEY
SECRET = settings.BITGET.DEMO.SECRET
PASSPHRASE = settings.BITGET.DEMO.PASSPHRASE


class Demo(Strategy):
    def __init__(self):
        super().__init__()
        self.buy_signal = True
        self.sell_signal = False
        self.position_printed = False

    def on_start(self):
        # Use USDT perpetual futures instead of spot for more active market data
        self.subscribe_bookl1(symbols=["BTCUSDT-PERP.BITGET"])

    def on_failed_order(self, order: Order):
        print(f"FAILED: {order}")
        self._print_position()

    def on_pending_order(self, order: Order):
        print(f"PENDING: {order}")

    def on_accepted_order(self, order: Order):
        print(f"ACCEPTED: {order}")

    def on_partially_filled_order(self, order: Order):
        print(f"PARTIALLY_FILLED: {order}")
        self._print_position()

    def on_filled_order(self, order: Order):
        print(f"FILLED: {order}")
        self._print_position()
        # After buy order is filled, trigger sell
        if order.side.is_buy:
            self.sell_signal = True

    def _print_position(self):
        """Print current position information"""
        print("\n" + "=" * 50)
        print("POSITION STATUS:")
        print("=" * 50)

        # Get position for the symbol using value_or to safely extract
        position = self.cache.get_position("BTCUSDT-PERP.BITGET").value_or(None)
        if position is not None:
            print(f"  Symbol: {position.symbol}")
            print(f"  Side: {position.side}")
            print(f"  Amount: {position.amount}")
            print(f"  Entry Price: {position.entry_price}")
            print(f"  Unrealized PnL: {position.unrealized_pnl}")
            print(f"  Realized PnL: {position.realized_pnl}")
        else:
            print("  No position found for BTCUSDT-PERP.BITGET")

        # Get all positions
        all_positions = self.cache.get_all_positions(ExchangeType.BITGET)
        if all_positions:
            print("\nAll Bitget Positions:")
            for symbol, pos in all_positions.items():
                print(f"  {symbol}: {pos.side} {pos.amount} @ {pos.entry_price}")
        else:
            print("\nNo positions found on Bitget")

        print("=" * 50 + "\n")

    def on_bookl1(self, bookl1: BookL1):
        print(f"BookL1: {bookl1.bid} / {bookl1.ask}")

        # Print position once at the beginning
        if not self.position_printed:
            self._print_position()
            self.position_printed = True

        # First buy BTC
        if self.buy_signal:
            self.create_order(
                symbol="BTCUSDT-PERP.BITGET",
                side=OrderSide.BUY,
                type=OrderType.MARKET,
                amount=Decimal("0.001"),
            )
            self.buy_signal = False

        # After buy is filled, sell BTC
        if self.sell_signal:
            self.create_order(
                symbol="BTCUSDT-PERP.BITGET",
                side=OrderSide.SELL,
                type=OrderType.MARKET,
                amount=Decimal("0.001"),
            )
            self.sell_signal = False


config = Config(
    strategy_id="bitget_futures_buy_and_sell",
    user_id="user_test",
    strategy=Demo(),
    log_config=LogConfig(level_stdout="DEBUG"),
    basic_config={
        ExchangeType.BITGET: BasicConfig(
            api_key=API_KEY,
            secret=SECRET,
            passphrase=PASSPHRASE,
            testnet=True,
        )
    },
    public_conn_config={
        ExchangeType.BITGET: [
            PublicConnectorConfig(
                account_type=BitgetAccountType.UTA_DEMO,
            )
        ]
    },
    private_conn_config={
        ExchangeType.BITGET: [
            PrivateConnectorConfig(
                account_type=BitgetAccountType.UTA_DEMO,
                max_slippage=0.01,  # 1% slippage
            )
        ]
    }
)

# Initialize the trading engine with the configuration
engine = Engine(config)

if __name__ == "__main__":
    try:
        engine.start()
    finally:
        engine.dispose()
        # Close the log file
        if hasattr(sys.stdout, 'close'):
            sys.stdout.close()
