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
- `strategy/strategies/_base/` - BaseSignalGenerator, TradeFilterConfig, registration helpers
- `strategy/strategies/{name}/` - Backtest configs and registrations (auto-discovered)
- `strategy/live/common/base_strategy.py` - BaseQuantStrategy: shared live strategy base class
- `strategy/live/{name}/` - Live trading indicators and exchange-specific wrappers

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

strategy/strategies/_base/
├── __init__.py
├── signal_generator.py  # BaseSignalGenerator, TradeFilterConfig, column constants
├── registration_helpers.py  # Factory functions: make_split_params_fn, make_mesa_dict_to_config, etc.

strategy/strategies/{name}/
├── core.py              # Strategy config dataclass
├── registration.py      # Strategy registration (auto-discovered via __init__.py)

strategy/live/common/base_strategy.py   # BaseQuantStrategy: shared live strategy base class
strategy/live/{name}/indicator.py       # Live: dual-mode (warmup: update_indicators_only, live: core.update())

test/indicators/parity_factory.py      # Test factory: make_parity_test_class()
test/indicators/test_all_parity.py     # Unified parity tests for all 9 strategies
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
| `update(close, high, low, ...)` | Backtest + Live (live mode) | Updates indicators + returns signal with full position management |
| `update_indicators_only(close, high, low, ...)` | Live (warmup mode) | Updates indicators only, no signal/position logic |
| `get_raw_signal()` | Live (warmup mode) | Stateless signal computation from current indicator values |

Live indicators operate in **dual mode**: during warmup they use `update_indicators_only()` + `get_raw_signal()` to avoid false position state; after warmup settles they switch to `core.update()` via `enable_live_mode()` for unified position management.

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

### BaseSignalGenerator (`_base/signal_generator.py`)

Generic signal generator that replaces all per-strategy `signal.py` files. Variance between strategies is encoded as constructor parameters:

```python
class BaseSignalGenerator:
    def __init__(self, config, filter_config, *, core_cls, update_columns,
                 core_extra_filter_fields=("signal_confirmation",),
                 pre_loop_hook=None, bar_hook=None):
```

**Column constants** (which DataFrame columns to pass to `core.update()`):
- `COLUMNS_CLOSE = ("close",)` — ema_crossover, bollinger_band, hurst_kalman
- `COLUMNS_CLOSE_HIGH_LOW = ("close", "high", "low")` — regime_ema, grid_trading
- `COLUMNS_CLOSE_HIGH_LOW_VOLUME = ("close", "high", "low", "volume")` — momentum, dual_regime, vwap

**TradeFilterConfig** (base filter config for all strategies):
```python
@dataclass
class TradeFilterConfig:
    min_holding_bars: int = 4
    cooldown_bars: int = 2
    signal_confirmation: int = 1
```

Strategies needing extra filter fields use subclasses (e.g., `HurstKalmanFilterConfig` adds `only_mean_reversion`).

**Hooks** for outlier strategies:
- `pre_loop_hook(core, data, generator)` — funding_rate: inject funding rate time series
- `bar_hook(core, data, i, arrays)` — vwap: inject `day` param; funding_rate: inject timing params

### Registration Helpers (`_base/registration_helpers.py`)

Four factory functions auto-generate boilerplate from dataclass introspection:

| Function | Purpose |
|----------|---------|
| `make_split_params_fn(config_cls)` | Split mixed params dict into (config_kwargs, filter_kwargs) |
| `make_filter_config_factory(filter_config_cls, min_hold_formula=None)` | Generate filter config for heatmap scanning |
| `make_mesa_dict_to_config(config_cls, filter_config_cls, x_param, y_param, ...)` | Convert mesa heatmap results to StrategyConfig |
| `make_export_config(strategy_name, config_cls, filter_config_cls, ...)` | Generate Python config code from optimized params |

### Adding a New Strategy

Minimal steps to add a new strategy (only real logic needed, ~350-550 lines):

1. **Signal core** — `strategy/indicators/{name}.py`: Implement `{Name}SignalCore` with `update()`, `update_indicators_only()`, `get_raw_signal()`
2. **Config** — `strategy/strategies/{name}/core.py`: Define `{Name}Config` dataclass
3. **Registration** — `strategy/strategies/{name}/registration.py`: ~50 lines of declarative registration using `BaseSignalGenerator` + helper factories
4. **Package init** — `strategy/strategies/{name}/__init__.py`: Docstring only (auto-discovered, no manual import needed)
5. **Parity test** — Add entry in `test/indicators/test_all_parity.py`: ~10 lines using `make_parity_test_class()`
6. **Live indicator** — `strategy/live/{name}/indicator.py`: Dual-mode wrapper with `enable_live_mode()`, passes filter params to core
7. **Live strategy** — `strategy/live/{name}/strategy.py`: Inherit `BaseQuantStrategy`, implement `on_start()` and `_format_log_line()`

No need to create signal.py, no manual import in `__init__.py`, no per-strategy test file.

### Design Patterns

- **Lazy imports**: `regime_ema.py` and `hurst_kalman.py` use lazy import functions (e.g., `_lazy_regime_imports()`) to break circular dependencies between `strategy/indicators/` and `strategy/strategies/`
- **Config override**: Signal generators use `dataclasses.replace()` to apply parameter overrides from backtest optimization
- **Auto-discovery**: `strategy/strategies/__init__.py` uses `pkgutil.iter_modules()` to automatically import `registration.py` from all strategy subdirectories
- **Bar confirmation**: Live indicators use timestamp change detection to confirm the previous bar is complete before processing
- **Signal mapping**: Live indicators use `_SIGNAL_MAP` dict to convert int signals to exchange-specific enum values
- **Dual-mode indicators**: Live indicators start in warmup mode (`update_indicators_only()`) and switch to live mode (`core.update()`) via `enable_live_mode()`, ensuring no false position state during historical kline replay
- **BaseQuantStrategy**: Shared base class (`strategy/live/common/base_strategy.py`) for all live trading strategies, containing position tracking, order management, circuit breaker, performance tracking, and a template `on_kline()`. Subclasses only implement `on_start()` and optionally `_format_log_line()`. Currently piloted by ema_crossover.

### Running Parity Tests

```bash
# Run all parity tests (87 tests: 68 strategy parity + 19 streaming primitive)
uv run pytest test/indicators/ -v

# Run only strategy parity tests
uv run pytest test/indicators/test_all_parity.py -v

# Run streaming primitive parity tests
uv run pytest test/indicators/test_streaming_parity.py -v
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
uv run python strategy/live/hurst_kalman/backtest.py --full
uv run python strategy/live/ema_crossover/backtest.py --heatmap
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
- `strategy/backtest/` — Unified framework (runner, CLI, registry, exchange profiles, heatmap, utils)
- `strategy/strategies/` — Exchange-agnostic strategy definitions (configs, signal generators delegate to cores)
- `strategy/live/` — Live trading indicators (delegate to signal cores for parity)
- `examples/` — Exchange API usage examples (binance, okx, bybit, hyperliquid, bitget)

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