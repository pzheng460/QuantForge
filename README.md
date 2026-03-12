# ⚒️ QuantForge

> High-performance crypto quantitative trading framework for strategy development, backtesting, and live execution.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.11%20|%203.12%20|%203.13-blue)

```
 ██████  ██    ██  █████  ███    ██ ████████ ███████  ██████  ██████   ██████  ███████
██    ██ ██    ██ ██   ██ ████   ██    ██    ██      ██    ██ ██   ██ ██       ██
██    ██ ██    ██ ███████ ██ ██  ██    ██    █████   ██    ██ ██████  ██   ███ █████
██    ██ ██    ██ ██   ██ ██  ██ ██    ██    ██      ██    ██ ██   ██ ██    ██ ██
 ██████   ██████  ██   ██ ██   ████    ██    ██       ██████  ██   ██  ██████  ███████
```

---

## What is QuantForge?

QuantForge is a Python framework for building, backtesting, and deploying crypto trading strategies. It connects to major exchanges via high-performance WebSocket feeds, provides a clean strategy API, and includes a TradingView-style backtest visualization UI.

**Key capabilities:**
- 🏗️ **Strategy Framework** — Event-driven, timer-based, or custom signal strategies with minimal boilerplate
- 📊 **Backtesting Engine** — Historical data replay with TradingView-style chart visualization (lightweight-charts v5)
- 🔌 **Multi-Exchange** — Binance, OKX, Bybit, Bitget, Hyperliquid — unified API
- ⚡ **High Performance** — uvloop + picows (Cython WebSocket) + msgspec serialization + Rust core components
- 📈 **Indicators** — Custom indicator framework with automatic warmup from historical data
- 🛠️ **Order Management** — Professional OMS/EMS with position tracking, PnL monitoring, and algorithmic execution (TWAP)

## Performance

QuantForge is built for speed:

| Component | Technology | Advantage |
|---|---|---|
| Event Loop | [uvloop](https://github.com/MagicStack/uvloop) | 2-4x faster than default asyncio |
| WebSocket | [picows](https://github.com/tarasko/picows) | Cython-based, C++ Boost.Beast-level speed |
| Serialization | [msgspec](https://jcristharif.com/msgspec/) | Faster than orjson/ujson |
| Core Bus & Clock | Rust ([nautilus](https://github.com/nautechsystems/nautilus_trader)) | Memory-safe, zero-cost abstractions |

## Supported Exchanges

| Binance | OKX | Bybit | Bitget | Hyperliquid |
|:---:|:---:|:---:|:---:|:---:|
| ✅ Spot/Futures | ✅ Spot/Futures | ✅ Linear | ✅ UTA/Futures | ✅ Perps |

## Quick Start

### Prerequisites

- Python 3.11+
- Redis
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

### Installation

```bash
# From source
git clone https://github.com/pzheng460/QuantForge.git
cd QuantForge
uv venv && uv pip install -e .

# Or with pip
pip install -e .
```

### Configuration

Create `.keys/.secrets.toml` with your exchange API credentials:

```toml
[BITGET]
[BITGET.UTA_DEMO]
api_key = "your_api_key"
secret = "your_secret"
passphrase = "your_passphrase"
```

### Hello World Strategy

```python
from decimal import Decimal
from quantforge.strategy import Strategy
from quantforge.constants import OrderSide, OrderType
from quantforge.schema import BookL1

class SimpleStrategy(Strategy):
    def __init__(self):
        super().__init__()
        self.subscribe_bookl1(symbols=["BTCUSDT-PERP.BINANCE"])
        self.triggered = False

    def on_bookl1(self, bookl1: BookL1):
        if not self.triggered:
            self.create_order(
                symbol="BTCUSDT-PERP.BINANCE",
                side=OrderSide.BUY,
                type=OrderType.MARKET,
                amount=Decimal("0.001"),
            )
            self.triggered = True
```

## Strategy Modes

### Event-Driven
React to real-time market data (order book, trades, klines):

```python
def on_bookl1(self, bookl1: BookL1):
    # Your logic on every book update
    pass

def on_kline(self, kline: Kline):
    # Your logic on every kline
    pass
```

### Timer-Based
Execute logic at fixed intervals:

```python
def __init__(self):
    super().__init__()
    self.schedule(self.algo, trigger="interval", seconds=60)

def algo(self):
    # Runs every 60 seconds
    pass
```

### Custom Signal
Integrate external signals:

```python
def on_custom_signal(self, signal: object):
    # React to any custom signal object
    pass
```

## Custom Indicators

Build indicators with automatic warmup from historical data:

```python
from quantforge.indicator import Indicator
from quantforge.constants import KlineInterval

class EMA(Indicator):
    def __init__(self, period: int):
        super().__init__(
            params={"period": period},
            name=f"EMA_{period}",
            warmup_period=period * 2,
            warmup_interval=KlineInterval.HOUR_1,
        )
        self.period = period
        self.value = None
        self._k = 2 / (period + 1)

    def handle_kline(self, kline):
        if not kline.confirm:
            return
        if self.value is None:
            self.value = kline.close
        else:
            self.value = kline.close * self._k + self.value * (1 - self._k)
```

Register in your strategy:

```python
self.ema_fast = EMA(period=5)
self.register_indicator(
    symbols="BTCUSDT-PERP.BITGET",
    indicator=self.ema_fast,
    data_type=DataType.KLINE,
    account_type=BitgetAccountType.UTA_DEMO,
)
```

## Backtesting

Run backtests with historical data and visualize results in a TradingView-style UI:

```bash
uv run python -m strategy.backtest.runner
```

The web UI serves on `http://localhost:5173` with interactive charts, trade markers, equity curves, and performance metrics.

## Web Callbacks

Expose FastAPI endpoints alongside a running strategy:

```python
from quantforge.web import create_strategy_app
from quantforge.config import WebConfig

class MyStrategy(Strategy):
    web_app = create_strategy_app(title="My Strategy API")

    @web_app.post("/toggle")
    async def toggle(self, payload: dict = Body(...)):
        self.signal = payload.get("signal", True)
        return {"signal": self.signal}

# In config:
web_config = WebConfig(enabled=True, host="127.0.0.1", port=9000)
```

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────┐
│  Exchange    │────▶│  PublicConnector  │────▶│  Strategy   │
│  WebSocket   │     │  (Market Data)   │     │  (Your Code)│
└─────────────┘     └──────────────────┘     └──────┬──────┘
                                                     │
┌─────────────┐     ┌──────────────────┐            │
│  Exchange    │◀───│  PrivateConnector │◀───────────┘
│  REST API    │     │  (OMS / EMS)     │   create_order()
└─────────────┘     └──────────────────┘
```

- **PublicConnector**: Streams market data (BookL1, Kline, Trade)
- **PrivateConnector**: Manages orders, positions, account state
- **OMS (Order Management System)**: Tracks order lifecycle
- **EMS (Execution System)**: Submits orders, handles fills

## Project Structure

```
quantforge/
├── core/           # MessageBus, Clock, config
├── exchange/       # Exchange connectors (Binance, OKX, Bybit, Bitget, Hyperliquid)
├── indicator/      # Indicator framework with warmup
├── backtest/       # Backtesting engine & analysis
├── web/            # FastAPI web server
└── schema/         # Data models (msgspec Structs)

strategy/           # Strategy implementations
web/frontend/       # TradingView-style backtest UI
```

## Attribution

QuantForge was originally forked from [NexusTrader](https://github.com/RiverTrading/NexusTrader) by RiverTrading / Quantweb3. We gratefully acknowledge the foundational work of the NexusTrader project and its contributors.

## License

MIT — see [LICENSE](./LICENSE) for details.
