# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

NexusTrader is a professional-grade quantitative trading platform built with Python 3.11+ that focuses on high-performance, low-latency trading across multiple exchanges. It features a modular, event-driven architecture with Rust-powered core components for maximum performance.

## Development Commands

### Dependencies and Setup
```bash
# Install with uv (package manager used in this project)
uv sync

# Install development dependencies
uv sync --group dev

# Install pre-commit hooks (required for contributions)
uv add --dev pre-commit
pre-commit install
```

### Testing
```bash
# Run all tests with async support
uv run pytest

# Run specific test modules
uv run pytest test/core/
uv run pytest test/core/test_entity.py

# Test configuration: pytest.ini enables asyncio_mode = auto
```

### Code Quality
```bash
# Linting and formatting (via ruff)
uv run ruff check
uv run ruff format
```

### Development Infrastructure
```bash
# Start Redis, PostgreSQL, Loki logging stack
docker-compose up -d

# Clear log files
./clear.sh

# Process management (production)
pm2 start ecosystem.config.js
```

## Architecture Overview

### Core Components
- **Engine**: Central orchestrator managing all trading systems (`nexustrader/engine.py`)
- **Strategy**: Base class for trading logic with multiple execution modes (`nexustrader/strategy.py`)
- **Connectors**: Exchange-specific public (market data) and private (trading) connectors
- **EMS** (Execution Management System): Order submission and execution
- **OMS** (Order Management System): Order state tracking and management
- **Cache**: High-performance data caching layer (`nexustrader/core/cache.py`)
- **Registry**: Order and component tracking (`nexustrader/core/registry.py`)

### Exchange Integration
Each exchange follows a consistent pattern in `nexustrader/exchange/{exchange}/`:
- **PublicConnector**: Market data WebSocket streams
- **PrivateConnector**: Account data and order execution
- **EMS/OMS**: Exchange-specific order management
- **ExchangeManager**: Coordinate connectors and systems

Supported exchanges:
- **Primary**: Binance, Bybit, OKX (full implementation)
- **Additional**: Bitget, Hyperliquid

### Strategy Execution Modes
1. **Event-Driven**: React to market events (`on_bookl1`, `on_trade`, `on_kline`)
2. **Timer-Based**: Scheduled execution using `schedule()` method
3. **Signal-Based**: Custom signal processing (`on_custom_signal`)

### Performance Optimizations
- **uvloop**: High-performance event loop (2-4x faster than asyncio)
- **picows**: Cython-based WebSocket library (C++ performance)
- **msgspec**: Ultra-fast serialization/deserialization
- **nautilus-trader**: Rust-powered MessageBus and Clock components

## Key File Locations

### Core Framework
- `nexustrader/engine.py` - Main trading engine
- `nexustrader/strategy.py` - Strategy base class
- `nexustrader/config.py` - Configuration management
- `nexustrader/schema.py` - Data structures and schemas
- `nexustrader/indicator.py` - Technical indicators framework

### Base Classes
- `nexustrader/base/connector.py` - Base connector implementations
- `nexustrader/base/ems.py` - Base execution management
- `nexustrader/base/oms.py` - Base order management

### Exchange Implementations
Each exchange directory contains:
- `connector.py` - Public/Private connectors
- `ems.py` - Exchange-specific execution management
- `oms.py` - Exchange-specific order management
- `schema.py` - Exchange data structures
- `websockets.py` - WebSocket implementations
- `rest_api.py` - REST API client

### Strategy Signal Layer
- `strategy/indicators/base.py` - Streaming indicator primitives (EMA, SMA, ATR, ADX, ROC, BB, RSI)
- `strategy/indicators/{name}.py` - Signal cores (single source of truth for each strategy)
- `strategy/strategies/{name}/` - Backtest configs, signal generators, registrations
- `strategy/bitget/{name}/` - Live trading indicators and exchange-specific wrappers

### Configuration and Data
- `nexustrader/constants.py` - Enums and constants
- `nexustrader/backends/` - Database backends (Redis, PostgreSQL, SQLite)

## Environment Configuration

Copy `env.example` to `.env` and configure:
```bash
# Redis Configuration
NEXUS_REDIS_HOST=127.0.0.1
NEXUS_REDIS_PORT=6379
NEXUS_REDIS_DB=0
NEXUS_REDIS_PASSWORD=your_redis_password

# PostgreSQL Configuration  
NEXUS_PG_HOST=localhost
NEXUS_PG_PORT=5432
NEXUS_PG_USER=postgres
NEXUS_PG_PASSWORD=your_postgres_password
NEXUS_PG_DATABASE=postgres
```

## Custom Indicator Development

Indicators support automatic warmup with historical data:

```python
class CustomIndicator(Indicator):
    def __init__(self, period: int = 20):
        super().__init__(
            params={"period": period},
            name=f"Custom_{period}",
            warmup_period=period * 2,  # Required historical periods
            warmup_interval=KlineInterval.MINUTE_1,  # Data interval
        )
    
    def handle_kline(self, kline: Kline):
        # Process kline data
        pass
```

Register indicators in strategy:
```python
self.register_indicator(
    symbols="BTCUSDT-PERP.BINANCE",
    indicator=self.custom_indicator,
    data_type=DataType.KLINE,
    account_type=BinanceAccountType.USD_M_FUTURE_TESTNET,
)
```

## Symbol Format

All symbols follow the pattern: `{base}{quote}-{instrument_type}.{exchange}`

Examples:
- `BTCUSDT-PERP.BINANCE` (Binance perpetual futures)
- `BTCUSDT-PERP.OKX` (OKX perpetual futures)
- `BTCUSDT-PERP.BYBIT` (Bybit perpetual futures)

## Configuration Management

Configuration uses `dynaconf` for environment-based settings:
- API credentials stored in `settings` system
- Environment variables via `.env` files
- Exchange account types specify testnet/mainnet and account categories

## Contributing Guidelines

From CONTRIBUTING.md:
1. Open GitHub issues before implementing changes
2. Fork from main branch and keep synced
3. Install pre-commit hooks (mandatory)
4. Small, focused pull requests with clear descriptions
5. Reference GitHub issues in PR descriptions
6. Target main branch for all PRs

## Infrastructure Services

Development stack includes:
- **Redis**: Data caching and pub/sub messaging
- **PostgreSQL**: Persistent data storage
- **Grafana Loki**: Centralized logging
- **Promtail**: Log shipping agent

Start with: `docker-compose up -d`

## Development Conventions

### Import Guidelines
- Always use absolute path imports

## Unified Signal Core Architecture

All trading strategies share a **SignalCore** pattern that guarantees 100% code parity between backtest and live trading. The signal logic lives in a single shared class — both the backtest signal generator and the live indicator delegate to it.

### Directory Layout

```
strategy/indicators/
├── base.py              # Streaming indicator primitives (EMA, SMA, ATR, ADX, ROC, BB, RSI)
├── momentum.py          # MomentumSignalCore
├── ema_crossover.py     # EMASignalCore
├── bollinger_band.py    # BBSignalCore
├── regime_ema.py        # RegimeEMASignalCore
├── hurst_kalman.py      # HurstKalmanSignalCore
├── vwap.py              # VWAPSignalCore
├── funding_rate.py      # FundingRateSignalCore
├── dual_regime.py       # DualRegimeSignalCore
├── grid_trading.py      # GridSignalCore

strategy/strategies/{name}/signal.py   # Backtest: thin wrapper calling core.update()
strategy/bitget/{name}/indicator.py    # Live: delegates to core.update_indicators_only() + get_raw_signal()
test/indicators/test_{name}_parity.py  # Parity tests verifying core vs generator match
```

### Signal Constants

All cores use the same integer signal values:
- `HOLD = 0` — No action
- `BUY = 1` — Open long / close short
- `SELL = -1` — Open short / close long
- `CLOSE = 2` — Close current position

### Three-Method API

Each `SignalCore` class exposes three methods:

| Method | Used By | Description |
|--------|---------|-------------|
| `update(close, high, low, ...)` | Backtest | Updates indicators + returns signal with full position management |
| `update_indicators_only(close, high, low, ...)` | Live | Updates indicators only, no signal/position logic |
| `get_raw_signal()` | Live | Stateless signal computation from current indicator values |

### Streaming Indicator Primitives (`base.py`)

| Class | Description |
|-------|-------------|
| `StreamingEMA(period)` | Exponential moving average |
| `StreamingSMA(period)` | Simple moving average (rolling window) |
| `StreamingATR(period)` | Average true range (Wilder smoothing) |
| `StreamingROC(period)` | Rate of change |
| `StreamingADX(period)` | Average directional index |
| `StreamingBB(period, multiplier)` | Bollinger bands (SMA ± multiplier × σ) |
| `StreamingRSI(period)` | Relative strength index (Wilder smoothing) |

All primitives share: `.value` property, `.update()` returning `Optional[float]`, `.reset()` method.

### Signal Core → Strategy Mapping

| Core Class | Config | Indicators Used | Strategy Type |
|------------|--------|-----------------|---------------|
| `MomentumSignalCore` | `MomentumConfig` | EMA×2, SMA, ATR, ROC | Trend following |
| `EMASignalCore` | `EMAConfig` | EMA×2 | Trend following |
| `BBSignalCore` | `BBConfig` | BB, SMA (trend bias) | Mean reversion |
| `RegimeEMASignalCore` | `RegimeEMAConfig` | EMA×2, ATR, ADX | Regime-gated trend |
| `HurstKalmanSignalCore` | `HurstKalmanConfig` | KalmanFilter1D, Hurst, ZScore | Statistical arb |
| `VWAPSignalCore` | `VWAPConfig` | RSI, cumulative VWAP, ZScore | Mean reversion |
| `FundingRateSignalCore` | `FundingRateConfig` | SMA, funding rate deque | Funding arb (short-only) |
| `DualRegimeSignalCore` | `DualRegimeConfig` | ADX, ROC, EMA×3, ATR, SMA, BB | Adaptive regime switch |
| `GridSignalCore` | `GridConfig` | SMA, ATR, dynamic grid levels | Grid trading |

### Position Management State

Each core tracks: `position` (0/1/-1), `entry_bar`, `entry_price`, `cooldown_until`, `signal_count`, `bar_index`. Filter params: `min_holding_bars`, `cooldown_bars`, `signal_confirmation`.

### Design Patterns

- **Lazy imports**: `regime_ema.py` and `hurst_kalman.py` use lazy import functions (e.g., `_lazy_regime_imports()`) to break circular dependencies between `strategy/indicators/` and `strategy/strategies/`
- **Config override**: Signal generators use `dataclasses.replace()` to apply parameter overrides from backtest optimization
- **Bar confirmation**: Live indicators use timestamp change detection to confirm the previous bar is complete before processing
- **Signal mapping**: Live indicators use `_SIGNAL_MAP` dict to convert int signals to exchange-specific enum values

### Running Parity Tests

```bash
# Run all parity tests (86 tests)
uv run pytest test/indicators/ -v

# Run a specific strategy's parity test
uv run pytest test/indicators/test_momentum_parity.py -v
```

## Unified Backtest Framework

The backtest system is exchange-agnostic and supports all strategies through a unified CLI.

### Quick Start

```bash
# Unified CLI (recommended):
uv run python -m strategy.backtest -S hurst_kalman -X bitget -p 1y --full
uv run python -m strategy.backtest -S ema_crossover -X binance --heatmap
uv run python -m strategy.backtest -S bollinger_band -X okx --optimize

# Backward-compatible (old entry points still work):
uv run python strategy/bitget/hurst_kalman/backtest.py --full
uv run python strategy/bitget/ema_crossover/backtest.py --heatmap
```

### CLI Arguments

| Flag | Description |
|------|-------------|
| `-S, --strategy` | Strategy name: `hurst_kalman`, `ema_crossover`, `bollinger_band` |
| `-X, --exchange` | Exchange: `bitget`, `binance`, `okx`, `bybit`, `hyperliquid` |
| `--symbol` | Trading pair (default: exchange-specific BTC/USDT perpetual) |
| `-p, --period` | Data period: `3m`, `6m`, `1y`, `2y` |
| `-m, --mesa` | Mesa config index (0 = best) |
| `--heatmap` | Run heatmap parameter scan |
| `-o, --optimize` | Grid search optimization |
| `-w, --walk-forward` | Walk-forward validation |
| `-r, --regime` | Market regime analysis |
| `-f, --full` | Three-stage complete validation |
| `-s, --show-results` | Show saved results |
| `-e, --export-config` | Export config for paper trading |

### Architecture

- `strategy/indicators/` — Shared signal cores and streaming primitives (single source of truth)
- `strategy/backtest/` — Unified framework (runner, CLI, registry, exchange profiles, heatmap)
- `strategy/strategies/` — Exchange-agnostic strategy definitions (configs, signal generators delegate to cores)
- `strategy/bitget/` — Live trading indicators (delegate to signal cores for parity)

### Supported Exchanges

| Exchange | CCXT ID | Maker Fee | Taker Fee |
|----------|---------|-----------|-----------|
| Bitget | `bitget` | 0.02% | 0.05% |
| Binance | `binance` | 0.02% | 0.04% |
| OKX | `okx` | 0.02% | 0.05% |
| Bybit | `bybit` | 0.02% | 0.05% |
| Hyperliquid | `hyperliquid` | 0.02% | 0.05% |

## Claude Code Memories

### CLI Usage Warnings
- Do not run nexustrader-cli moniter in claude code

### Ruff Usage
- Lint all files in the current directory with `uvx ruff check`
- Format all files in the current directory with `uvx ruff format`