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
- `strategy/strategies/_base/streaming.py` - Streaming indicator primitives (EMA, SMA, ATR, ADX, ROC, BB, RSI)
- `strategy/strategies/{name}/signal_core.py` - Signal cores (single source of truth for each strategy)
- `strategy/strategies/_base/` - BaseSignalGenerator, TradeFilterConfig, registration helpers, BaseQuantStrategy, PerformanceTracker, GenericStrategy, GenericIndicator, generic configs
- `strategy/strategies/{name}/` - Self-contained strategy: core.py, registration.py (minimal); indicator.py, live.py, configs.py (optional, for complex strategies)
- `strategy/runner.py` - Generic CLI runner for any strategy with LiveConfig

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

### Connector Leverage Setting
- `PrivateConnectorConfig.leverage` sets the leverage multiplier; `leverage_symbols` optionally restricts which symbols it applies to.
- Leverage is applied **after** `strategy.on_start()` via `engine._apply_leverage()`, so only symbols the strategy actually subscribes to are targeted (auto-detected via `strategy._subscribed_symbols`).
- Priority: explicit `leverage_symbols` config → auto-detected strategy symbols → skip (no all-futures fallback).
- `BitgetPrivateConnector.apply_leverage(strategy_symbols)` is the entry point; other exchanges can add the same method when they implement leverage setting.
- `Strategy._track_subscribed_symbols()` is called in every `subscribe_*()` method to build `_subscribed_symbols`.

## Unified Signal Core Architecture

All trading strategies share a **SignalCore** pattern that guarantees 100% code parity between backtest and live trading. The signal logic lives in a single shared class — both the backtest signal generator and the live indicator delegate to it.

### Directory Layout

```
strategy/strategies/_base/
├── streaming.py             # Streaming indicator primitives (EMA, SMA, ATR, ADX, ROC, BB, RSI)
├── test_data.py             # Synthetic OHLCV data generators (shared by registration.py + tests)
├── __init__.py
├── signal_generator.py      # BaseSignalGenerator, TradeFilterConfig, column constants
├── registration_helpers.py  # Factory functions: make_split_params_fn, make_mesa_dict_to_config, etc.
├── base_strategy.py         # BaseQuantStrategy: shared live strategy base class
├── generic_indicator.py     # GenericIndicator: wraps any SignalCore for live trading
├── generic_strategy.py      # GenericStrategy: generic live strategy using LiveConfig
├── generic_configs.py       # Generic config loader (replaces per-strategy configs.py)
├── performance.py           # PerformanceTracker for live/demo trading metrics
├── paper_validate.py        # Paper trading validation utilities

strategy/runner.py               # Generic CLI runner for any strategy with LiveConfig

strategy/strategies/{name}/
├── signal_core.py       # SignalCore class (single source of truth for signal logic)
├── core.py              # Strategy config dataclass
├── registration.py      # Strategy registration (auto-discovered) + LiveConfig + ParityTestConfig
├── indicator.py         # (OPTIONAL) Custom indicator for complex strategies
├── live.py              # (OPTIONAL) Custom live strategy for complex strategies
├── configs.py           # (OPTIONAL) Custom config loader (generic_configs.py replaces this)

strategy/backtest/registry.py          # StrategyRegistration, ParityTestConfig, LiveConfig, HeatmapConfig
test/strategy/parity_factory.py        # Test factory: make_parity_test_class()
test/strategy/test_all_parity.py       # Auto-discovers all strategies via registry (no manual edits needed)
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

### Streaming Indicator Primitives (`streaming.py`)

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
| `MAConvergenceSignalCore` | `MAConvergenceConfig` | SMA×3, EMA×3, ATR | MA convergence breakout |
| `SMATrendSignalCore` | `SMATrendConfig` | SMA (daily resampled) | Long-only trend following |
| `FundingArbSignalCore` | `FundingArbConfig` | Funding rate deque | Delta-neutral funding arb |
| `FearReversalSignalCore` | `FearReversalConfig` | RSI, ATR, SMA, EMA, ADX | Long-only fear bounce reversal |
| `SMAFundingSignalCore` | `SMAFundingConfig` | SMA, ATR, funding rate deque | Dual-leg: SMA trend (80%) + funding arb (20%) |
| `DynamicGridSignalCore` | `DynamicGridConfig` | SMA, ATR, ADX, SMA(ATR) | Grid trading with volatility-adaptive leverage |

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

#### Minimal (generic runner — only 2 files needed):

1. **Signal core** — `strategy/strategies/{name}/signal_core.py`: Implement `{Name}SignalCore` with `update()`, `update_indicators_only()`, `get_raw_signal()`
2. **Config** — `strategy/strategies/{name}/core.py`: Define `{Name}Config` dataclass
3. **Registration** — `strategy/strategies/{name}/registration.py`: Register with `BaseSignalGenerator` + `LiveConfig` + `ParityTestConfig`
4. **Package init** — `strategy/strategies/{name}/__init__.py`: Docstring only (auto-discovered)

No need to touch `test/strategy/test_all_parity.py` — it auto-discovers all strategies via `ParityTestConfig` in the registry.
No need to create indicator.py, live.py, configs.py, or signal.py. The generic runner handles everything.

Run with: `uv run python -m strategy.runner -S {name} --mesa 0`

#### Custom (for complex strategies needing on_kline override):

Add steps 6-7 only if the strategy needs tick-level stop checks, trailing stops, funding rate subscriptions, or completely custom logic:

6. **Live indicator** — `strategy/strategies/{name}/indicator.py`: Dual-mode wrapper
7. **Live strategy** — `strategy/strategies/{name}/live.py`: Custom `on_kline()` override

Currently custom: momentum (trailing stops), funding_rate (funding subscription), grid_trading (tick-based grid logic).

### Design Patterns

- **No circular imports**: signal cores live inside `strategy/strategies/{name}/signal_core.py` and import directly from `.core` within the same package — no lazy import hacks needed
- **Config override**: Signal generators use `dataclasses.replace()` to apply parameter overrides from backtest optimization
- **Auto-discovery**: `strategy/strategies/__init__.py` uses `pkgutil.iter_modules()` to automatically import `registration.py` from all strategy subdirectories
- **Bar confirmation**: Live indicators use timestamp change detection to confirm the previous bar is complete before processing
- **Signal mapping**: Live indicators use `_SIGNAL_MAP` dict to convert int signals to exchange-specific enum values
- **Dual-mode indicators**: Live indicators start in warmup mode (`update_indicators_only()`) and switch to live mode (`core.update()`) via `enable_live_mode()`, ensuring no false position state during historical kline replay
- **Position state sync (`sync_position`)**: All 12 signal cores expose `sync_position(pos_int, entry_price=0.0)` to atomically set `core.position` + `core.entry_price` from an external source. Used for two safety mechanisms:
  1. **Order failure rollback** (`on_failed_order` in `base_strategy.py`): `_open_position`/`_close_position` snapshot pre-order state into `_pre_order_snapshots[symbol]`; on failure, both `_positions[symbol]` (strategy side) and `core.position` (core side) are rolled back
  2. **Restart ghost position sync** (`_sync_startup_positions` in `generic_strategy.py`): on `on_start()`, queries `self.cache.get_position(symbol)` for each symbol and restores both strategy and core state if the exchange already has an open position
- **BaseQuantStrategy**: Shared base class (`strategy/strategies/_base/base_strategy.py`) for all live trading strategies. Provides position tracking, order management, circuit breaker, performance tracking, signal filtering (confirmation, cooldown, min holding), stale data guard, and a template `on_kline()`. Requires `account_type` (keyword-only) parameter for exchange-agnostic balance lookups. Subclasses implement `on_start()` and `_format_log_line()`, and optionally override hooks:
  - `_get_signal(symbol, indicator)` — customize signal arguments (e.g., funding_rate passes timing args)
  - `_check_stop_loss(symbol, indicator, price)` — customize stop loss check signature
  - `_pre_signal_hook(symbol, signal, price, indicator, current_bar)` — return True to skip `_process_signal` (e.g., regime filter)
  - `_process_signal(symbol, signal, price, current_bar)` — override signal handling (e.g., short-only, regime gating)
  - `_on_live_activated(symbol, indicator, current_bar)` — called once when live trading activates (e.g., `enable_live_mode()`)
  - Class-level opt-in: `_ENABLE_STALE_GUARD = True`, `_MAX_KLINE_AGE_S = 120.0` for stale data burst detection
  - Complex strategies (momentum, grid) override `on_kline` entirely for bar detection or tick-based logic
- **GenericStrategy + GenericIndicator**: Generic runner system (`strategy/strategies/_base/generic_strategy.py`, `generic_indicator.py`) that eliminates per-strategy `live.py`/`indicator.py`/`configs.py` for simple-to-moderate strategies. Configured via `LiveConfig` on `StrategyRegistration`. GenericIndicator uses `inspect.signature()` to auto-detect signal core method signatures and map kline fields to parameters. LiveConfig hooks (`process_signal_fn`, `pre_signal_hook_fn`, `on_live_activated_fn`, `pre_update_hook`) inject strategy-specific behavior without custom subclasses. CLI: `uv run python -m strategy.runner -S {name} --mesa 0 --exchange bitget`

### Running Parity Tests

```bash
# Run all parity tests (115 tests: 96 strategy parity + 19 streaming primitive)
uv run pytest test/strategy/ -v

# Run only strategy parity tests
uv run pytest test/strategy/test_all_parity.py -v

# Run streaming primitive parity tests
uv run pytest test/strategy/test_streaming_parity.py -v
```

## Unified Backtest Framework

The backtest system is exchange-agnostic and supports all strategies through a unified CLI.

### Quick Start

```bash
# Unified CLI (recommended):
uv run python -m strategy.backtest -S hurst_kalman -X bitget -p 1y --full
uv run python -m strategy.backtest -S ema_crossover -X binance --heatmap
uv run python -m strategy.backtest -S bollinger_band -X okx --optimize

# Live trading (generic runner — works for most strategies):
uv run python -m strategy.runner -S ema_crossover --mesa 0
uv run python -m strategy.runner -S hurst_kalman --mesa 0 --exchange binance
uv run python -m strategy.runner --list  # show available strategies

# Live trading (custom — for complex strategies):
uv run python -m strategy.strategies.momentum.live --mesa 3
uv run python -m strategy.strategies.funding_rate.live --mesa 0
```

### CLI Arguments

| Flag | Description |
|------|-------------|
| `-S, --strategy` | Strategy name: `hurst_kalman`, `ema_crossover`, `bollinger_band` |
| `-X, --exchange` | Exchange: `bitget`, `binance`, `okx`, `bybit`, `hyperliquid` |
| `--symbol` | Trading pair (default: exchange-specific BTC/USDT perpetual) |
| `-p, --period` | Data period: `1w`, `1m`, `3m`, `6m`, `1y`, `2y`, `3y`, `5y` (short periods warn when used with analysis flags) |
| `-m, --mesa` | Mesa config index (0 = best) |
| `--heatmap` | Run heatmap parameter scan |
| `--heatmap-resolution` | Heatmap grid resolution (default: 15) |
| `-o, --optimize` | Grid search optimization |
| `-w, --walk-forward` | Walk-forward validation |
| `-r, --regime` | Market regime analysis |
| `-f, --full` | Three-stage complete validation |
| `-s, --show-results` | Show saved results |
| `-e, --export-config` | Export config for paper trading |
| `-j, --jobs` | Parallel workers: `1`=sequential (default), `-1`=all CPU cores |
| `-L, --leverage` | Leverage multiplier (default: 1.0) |
| `-R, --rolling-optimize` | Rolling optimize (day-forward test): re-optimize on rolling training window, test next day |
| `--train-days` | Training window size in days for `--rolling-optimize` (default: 7) |
| `--no-cache` | Skip local SQLite cache, fetch directly from exchange |
| `--no-validate` | Skip automatic cross-validation of newly fetched data |
| `--db-stats` | Show local kline database statistics and exit |

### Architecture

- `strategy/strategies/` — All strategy code: signal cores, registrations, live/indicator/configs (self-contained per strategy)
- `strategy/strategies/_base/` — Shared base classes (BaseSignalGenerator, BaseQuantStrategy, streaming primitives, test data)
- `strategy/backtest/` — Unified framework (runner, CLI, registry, exchange profiles, heatmap, utils)
- `examples/` — Exchange API usage examples (binance, okx, bybit, hyperliquid, bitget)

### Supported Exchanges

| Exchange | CCXT ID | Maker Fee | Taker Fee |
|----------|---------|-----------|-----------|
| Bitget | `bitget` | 0.02% | 0.05% |
| Binance | `binance` | 0.02% | 0.04% |
| OKX | `okx` | 0.02% | 0.05% |
| Bybit | `bybit` | 0.02% | 0.05% |
| Hyperliquid | `hyperliquid` | 0.02% | 0.05% |

### Backtest Methodology

The backtest engine applies several corrections to ensure simulated results match live trading behaviour:

**Signal Delay (1-bar execution lag)**
Signals generated from bar i are executed at bar i+1 (`_apply_signal_delay()` in `runner.py` and `heatmap.py`). This prevents look-ahead bias where a bar-close signal is filled at the same bar's close price.

**Walk-Forward Window Scaling**
WFO window sizes scale with bar interval via `_bars_per_day(interval)`:
- 15m strategies: 8640 bars train / 2880 bars test (90d / 30d)
- 1h strategies: 2160 bars train / 720 bars test (90d / 30d)
- Standalone `--walk-forward` re-optimises in each window using `default_grid`; when called from `--full` it uses fixed params for stability checking.

**position_size_pct Passthrough**
Strategies with `position_size_pct < 1.0` (e.g. `funding_rate=0.30`, `grid_trading=0.20`) correctly size positions in all backtest paths: single run, grid search, walk-forward, heatmap.

**Intrabar Stop Loss**
`BaseSignalGenerator.generate()` checks bar `low`/`high` against `entry_price * stop_loss_pct` after each `core.update()` call. If triggered, the signal is overridden to CLOSE and core state is reset. Parity tests mirror this logic in `_run_core` so all 115 tests still pass.

**Funding Rate Data Quality**
When `funding_rates` is empty/None, `use_funding_rate=False` is passed to `CostConfig` and a warning is printed. The fallback in `_build_funding_rate_series` uses `0.0` (no cost modelled) instead of the previous misleading `0.000014` constant.

**Funding Rate Fallback (Gate.io)**
`fetch_funding_rates()` in `utils.py` tries the requested exchange first; if it returns fewer than 500 records for periods > 6 months, it falls back to Gate.io (`gate` in CCXT) which provides full funding rate history.  Bitget/OKX only return ~100–270 recent records, while Gate.io returns 5000+ covering years of data.

**SMA Trend Daily-Close Gating**
The `sma_trend` strategy resamples 1h bars to daily close, computes rolling SMA, then forward-fills back to 1h. Signal evaluation (close vs SMA) is gated to daily-close bars only — the last 1h bar of each calendar day (`is_daily_close=True`). All intraday bars return HOLD, preventing 1h price noise from generating false crossovers. The runner's 1-bar signal delay already prevents look-ahead (signal from bar i executes at bar i+1), so no `.shift(1)` on the SMA itself is needed. The `sma_funding` strategy inherits the same daily-close gating for its trend leg; `pre_update_hook` injects `is_daily_close = (kline.start_hour == 23)` in live mode.

**Sharpe Annualisation (Auto-Inferred)**
`PerformanceAnalyzer` and `VectorizedBacktest._calculate_metrics()` infer `periods_per_year` from the equity curve's DatetimeIndex via `infer_periods_per_year()` (`nexustrader/backtest/analysis/performance.py`). No manual configuration needed — 1h strategies automatically use ~8766 instead of the incorrect 15m constant 35040.

**UTC Datetime Convention**
`strategy/backtest/utils.py` and `cli.py` use `datetime.now(timezone.utc).replace(tzinfo=None)` instead of `datetime.now()`.  Since `calendar.timegm()` treats naive datetimes as UTC, using local time would produce timestamps offset by the system timezone (e.g. +8h in UTC+8), causing exchange APIs to reject requests with future timestamps.

**Parallel Scan / Optimise**
`HeatmapScanner` and `GridSearchOptimizer` accept `n_jobs` (also exposed as `--jobs/-j` in the CLI):
- HeatmapScanner: full parallel — each `_run_single()` creates a fresh generator (thread-safe)
- GridSearchOptimizer: signal generation stays sequential (shared closure), only `VectorizedBacktest.run()` is parallelised
- `n_jobs=-1` uses all available CPU cores

### Local Data Cache & Multi-Source Validation

`nexustrader/backtest/data/database.py` provides a SQLite cache for historical kline data:
- **KlineDatabase**: Stores OHLCV bars in `~/.nexustrader/data/klines.db` (configurable)
- API: `save()`, `load()`, `has_data()`, `get_gaps()`, `stats()`
- Unique constraint: `(exchange, symbol, interval, timestamp)`
- Uses `calendar.timegm()` for timezone-safe UTC epoch conversion

`nexustrader/backtest/data/cached_provider.py` provides smart caching + validation:
- **CachedDataProvider**: `fetch()` checks cache first, only pulls gaps from exchange
- **ValidatedData**: `fetch_and_validate()` compares data across multiple exchanges
- Returns: `primary_data`, `validation_report`, `anomalies`, `is_valid`

`strategy/backtest/utils.py` — `fetch_data()` uses cache by default (`no_cache=False`) with automatic cross-validation against OKX for newly fetched data (`validate=True`).  Already-cached data skips validation.  Usable validation sources (China-accessible): okx, gate, htx.

Tests: `uv run pytest test/backtest/test_database.py -v` (20 tests)

### Monte Carlo Simulation & Stress Testing

The `nexustrader/backtest/simulation/` submodule provides statistical simulation capabilities for strategy robustness assessment:

| Module | Class | Purpose |
|--------|-------|---------|
| `bootstrap.py` | `BlockBootstrap` | Block bootstrap resampling on log-returns (preserves fat tails, volatility clustering) |
| `monte_carlo.py` | `GBMGenerator` | Geometric Brownian Motion path generation |
| `monte_carlo.py` | `JumpDiffusionGenerator` | Merton Jump Diffusion (GBM + Poisson jumps for fatter tails) |
| `stress_test.py` | `StressTestGenerator` | Importance sampling: crash, spike, and volatility regime scenarios |
| `stress_test.py` | `StressTestResult` | Dataclass with paths, importance weights, tail probability |
| `report.py` | `SimulationReport` | Distribution statistics, confidence intervals, optional matplotlib plots |

All classes accept OHLCV DataFrames (DatetimeIndex, columns: open/high/low/close/volume) and produce `List[pd.DataFrame]` of synthetic paths in the same format. GBM/JD use `infer_periods_per_year()` for dt calculation.

```python
from nexustrader.backtest.simulation import (
    BlockBootstrap, GBMGenerator, JumpDiffusionGenerator,
    StressTestGenerator, SimulationReport,
)
```

Tests: `uv run pytest test/backtest/test_simulation.py -v` (23 tests)

## Claude Code Memories

### Workflow Rules
- After every code change: update CLAUDE.md and CLAUDE_CN.md, then commit and push to dev branch

### CLI Usage Warnings
- Do not run nexustrader-cli moniter in claude code

### Ruff Usage
- Lint all files in the current directory with `uvx ruff check`
- Format all files in the current directory with `uvx ruff format`