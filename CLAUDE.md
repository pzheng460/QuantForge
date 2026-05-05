# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

QuantForge is a professional-grade quantitative trading platform built with Python 3.11+ that focuses on high-performance, low-latency trading across multiple exchanges. It features a modular, event-driven architecture with Rust-powered core components for maximum performance.

**All trading strategies are expressed as Pine Script (.pine) files.** The `quantforge/` package is the single source of all code.

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
# Run all tests (138 Pine/DSL + core tests)
uv run pytest

# Pine Script tests (103 tests: interpreter, transpiler parity, live engine, optimizer)
uv run pytest quantforge/pine/tests/ -v

# DSL tests (35 tests)
uv run pytest quantforge/dsl/tests/ -v

# Core framework tests
uv run pytest test/core/

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
- **Engine**: Central orchestrator managing all trading systems (`quantforge/engine.py`)
- **Strategy**: Base class for trading logic with multiple execution modes (`quantforge/strategy.py`)
- **Pine Engine**: Pine Script interpreter + live trading engine (`quantforge/pine/`)
- **Connectors**: Exchange-specific public (market data) and private (trading) connectors
- **EMS** (Execution Management System): Order submission and execution
- **OMS** (Order Management System): Order state tracking and management
- **Cache**: High-performance data caching layer (`quantforge/core/cache.py`)
- **Registry**: Order and component tracking (`quantforge/core/registry.py`)

### Exchange Integration
Each exchange follows a consistent pattern in `quantforge/exchange/{exchange}/`:
- **PublicConnector**: Market data WebSocket streams
- **PrivateConnector**: Account data and order execution
- **EMS/OMS**: Exchange-specific order management
- **ExchangeManager**: Coordinate connectors and systems

Supported exchanges:
- **Primary**: Binance, Bybit, OKX (full implementation)
- **Additional**: Bitget, Hyperliquid

### Performance Optimizations
- **uvloop**: High-performance event loop (2-4x faster than asyncio)
- **picows**: Cython-based WebSocket library (C++ performance)
- **msgspec**: Ultra-fast serialization/deserialization
- **nautilus-trader**: Rust-powered MessageBus and Clock components

## Key File Locations

### Core Framework
- `quantforge/engine.py` - Main trading engine
- `quantforge/strategy.py` - Strategy base class
- `quantforge/config.py` - Configuration management
- `quantforge/schema.py` - Data structures and schemas
- `quantforge/indicator.py` - Technical indicators framework

### Streaming Indicators
- `quantforge/indicators/streaming.py` - StreamingEMA, StreamingSMA, StreamingATR, StreamingADX, StreamingROC, StreamingBB, StreamingRSI

### Base Classes
- `quantforge/base/connector.py` - Base connector implementations
- `quantforge/base/ems.py` - Base execution management
- `quantforge/base/oms.py` - Base order management

### Exchange Implementations
Each exchange directory contains:
- `connector.py` - Public/Private connectors
- `ems.py` - Exchange-specific execution management
- `oms.py` - Exchange-specific order management
- `schema.py` - Exchange data structures
- `websockets.py` - WebSocket implementations
- `rest_api.py` - REST API client

### Configuration and Data
- `quantforge/constants.py` - Enums and constants
- `quantforge/backends/` - Database backends (Redis, PostgreSQL, SQLite)

## Environment Configuration

Copy `env.example` to `.env` and configure:
```bash
# Redis Configuration
QUANTFORGE_REDIS_HOST=127.0.0.1
QUANTFORGE_REDIS_PORT=6379
QUANTFORGE_REDIS_DB=0
QUANTFORGE_REDIS_PASSWORD=your_redis_password

# PostgreSQL Configuration
QUANTFORGE_PG_HOST=localhost
QUANTFORGE_PG_PORT=5432
QUANTFORGE_PG_USER=postgres
QUANTFORGE_PG_PASSWORD=your_postgres_password
QUANTFORGE_PG_DATABASE=postgres
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

## Streaming Indicator Primitives (`quantforge/indicators/streaming.py`)

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

Used by: `quantforge/dsl/indicators.py`, Pine transpiler TA calculators.

## Pine Script Engine

The Pine Script engine (`quantforge/pine/`) provides a parser, interpreter, and transpiler for TradingView-compatible Pine Script v5. **This is the primary strategy layer.**

### Pine Strategies

All trading strategies live as `.pine` files in `quantforge/pine/strategies/`:

| Strategy | File | Description |
|----------|------|-------------|
| Momentum ADX | `momentum_adx.pine` | Trend following with ROC, EMA, ADX filter, ATR trailing stop |
| Bollinger Band | `bollinger_band.pine` | Mean reversion at BB bands with trend SMA filter |
| Dual Regime | `dual_regime.pine` | Adaptive: momentum in trending, mean reversion in ranging |
| SMA Trend | `sma_trend.pine` | Long-only daily SMA trend following |
| Hurst Kalman | `hurst_kalman.pine` | Statistical arb approximation (EMA proxy for Kalman) |
| EMA Crossover | `ema_crossover.pine` | Simple EMA crossover (fast/slow) |

Test fixtures in `quantforge/pine/tests/fixtures/`: `ema_cross.pine`, `rsi_strategy.pine`, `rsi_mean_revert.pine`, `macd_cross.pine`, `bb_strategy.pine`, `ema_cross_5_13.pine`

### Pine CLI

```bash
# Backtest a .pine file on exchange data
python -m quantforge.pine.cli backtest my_strategy.pine --symbol BTC/USDT:USDT --exchange bitget --timeframe 15m --start 2026-01-01 --end 2026-03-12 --warmup-days 60

# Optimize input parameters (grid search over input.int/input.float ranges)
python -m quantforge.pine.cli optimize my_strategy.pine --symbol BTC/USDT:USDT --exchange bitget --timeframe 15m --start 2026-01-01 --end 2026-03-12 --metric sharpe --top 10 --json results.json

# Transpile Pine Script to standalone Python
python -m quantforge.pine.cli transpile my_strategy.pine --output strategy.py
python strategy.py  # runs standalone — no Pine interpreter dependency

# Live trading (demo/paper)
python -m quantforge.pine.cli live my_strategy.pine --exchange bitget --demo --symbol BTC/USDT:USDT --timeframe 15m

# Live trading (real money)
python -m quantforge.pine.cli live my_strategy.pine --exchange bitget --no-demo --confirm-live --symbol BTC/USDT:USDT --timeframe 15m
```

### Pine Transpiler

The transpiler (`quantforge/pine/transpiler/codegen.py`) generates **self-contained Python scripts** that:
- Embed TA calculator classes matching TradingView exactly (EMA with SMA seed, RSI with Wilder/RMA smoothing, etc.)
- Track positions and execute orders with next-bar-open semantics
- Fetch OHLCV data via ccxt or accept a `list[list]` of `[timestamp, open, high, low, close, volume]`
- Compute P&L independently — no Pine interpreter dependency

**TA mappings**: `ta.ema` → `_EMACalc`, `ta.sma` → `_SMACalc`, `ta.rsi` → `_RSICalc`, `ta.macd` → `_MACDCalc`, `ta.stdev` → `_StdevCalc`, `ta.atr` → `_ATRCalc`, `ta.adx` → `_ADXCalc`, `ta.bb` → `_BBCalc`, `ta.stoch` → `_StochCalc`, `ta.crossover`/`ta.crossunder` → `_crossover`/`_crossunder` with prev-bar tracking, `ta.highest`/`ta.lowest` → `_HighestCalc`/`_LowestCalc`, `ta.change` → `_ChangeCalc`

**Strategy mappings**: `strategy.entry` → `tracker.queue_entry()`, `strategy.close` → `tracker.queue_close()` (orders execute at next bar's open price)

**Parity guarantee**: Transpiled code produces identical trades and PnL to the Pine interpreter on the same data. Validated by 21 parity tests across 6 fixture strategies.

### Supported ta.* Functions

`ta.sma`, `ta.ema`, `ta.rma`, `ta.rsi`, `ta.atr`, `ta.adx`, `ta.macd`, `ta.bb`, `ta.stoch`, `ta.stdev`, `ta.crossover`, `ta.crossunder`, `ta.highest`, `ta.lowest`, `ta.change`, `ta.tr`

### Web UI Architecture

**Frontend stack**: React 18 + TypeScript (strict) + Vite + Tailwind CSS + **shadcn/ui** component library
- UI primitives (`Button`, `Input`, `Select`/`SelectTrigger`/`SelectContent`/`SelectItem`, `Label`, `Badge`, `Card`, `Checkbox`, `Tabs`, `Collapsible`, `Table`, `Separator`, `Popover`, `ScrollArea`) live in `web/frontend/src/components/ui/`
- Select uses Radix `@radix-ui/react-select` (not native `<select>`); all form inputs use shadcn components
- `ErrorBoundary` component wraps the app; pages are lazy-loaded with `React.lazy` + `Suspense`
- Vite chunk splitting: `vendor` (react/zustand), `charts` (recharts/lightweight-charts), `ui` (@radix-ui/lucide)
- Theming via CSS variables in `index.css` (dark TradingView-style theme); color tokens: `--background`, `--foreground`, `--primary`, `--card`, `--border`, `--muted`, `--destructive`
- All chart components consolidated in `src/components/charts/` (TradingChart, EquityChart, DrawdownChart, etc.)
- Utility: `cn()` from `@/lib/utils` (wraps `clsx` + `tailwind-merge`)
- Path alias `@/` → `./src/` (configured in `tsconfig.json` + `vite.config.ts`)
- Domain-specific colors (trading green/red) remain as `tv-green`, `tv-red` in Tailwind config

All backtest and optimization logic is unified in the main backtest module:
- `web/backend/jobs.py` — Shared helpers (`_fetch_ohlcv`, `_resolve_pine_source`, `_resolve_date_range`) and job runners for both backtest and optimization
- `web/backend/routers/backtest.py` — `/backtest/run` (POST, accepts `strategy` file name OR `pine_source` raw code), `/backtest/{id}` (GET, poll status)
- `web/backend/routers/optimize.py` — `/optimize/run` (POST, Pine grid search), `/optimize/{id}` (GET, poll status)
- `web/backend/routers/strategies.py` — `/strategies` (lists Pine files with parsed input params), `/exchanges`
- `web/backend/routers/live.py` — Live engine management: `/live/start` (POST), `/live/stop/{id}` (POST), `/live/engines` (GET), `/ws/live/performance` (WS)
- `web/backend/live_engines.py` — In-memory engine manager: `start_engine()`, `stop_engine()`, `list_engines()` — runs PineLiveEngine as asyncio tasks
- Frontend pages: `Dashboard.tsx` (live trading: strategy selector + start/stop + StrategyTester), `Backtest.tsx`, `Optimizer.tsx`
- `web/frontend/src/utils/liveAdapter.ts` — Converts `LivePerformance` → `BacktestResult` for StrategyTester rendering
- Route: `/` (live trading), `/backtest`, `/optimizer` in the web UI

### Pine Live Trading Engine

The Pine interpreter runs **directly** as a live trading engine — no transpilation needed. The same interpreter that produces exact trade parity with TradingView in backtest mode is used bar-by-bar on real-time klines.

**Architecture:**
```
Pine Script (.pine file)
     ↓
Pine Interpreter (SAME engine for backtest AND live)
     ↓  feeds real-time confirmed klines
QuantForge Exchange Connectors (via ccxt)
     ↓  strategy.entry/close signals → real orders
Exchange
```

**Key files:**
- `quantforge/pine/live/engine.py` — `PineLiveEngine`: warmup + live kline loop (smart polling with exact bar timing)
- `quantforge/pine/live/order_bridge.py` — `OrderBridge`: Pine signals → exchange orders (uses `CcxtConnector` for real order submission)
- `quantforge/pine/live/connector.py` — Warmup bar fetching + `CcxtConnector` (real order submission via ccxt with API keys from `settings`)
- `quantforge/pine/optimize.py` — `extract_pine_inputs()`, `generate_grid()`, `run_optimization()`: grid search over `input.int`/`input.float` parameters

**Incremental execution API** (added to `PineRuntime`):
- `init_incremental(script)` — Parse declarations, reset indicator state (call once)
- `process_bar(bar) → list[Order]` — Feed one bar, execute Pine script, return new orders
- `finalize() → BacktestResult` — Close remaining positions, return results

**Signal callbacks** (added to `StrategyContext`):
- `set_signal_callbacks(on_entry, on_close, on_exit)` — Register callbacks fired when Pine script places orders
- Used by `OrderBridge` to intercept `strategy.entry/close/exit` calls

**Parity guarantee:** 11 tests verify that incremental bar-by-bar execution produces identical trades and equity curves to batch execution across all fixture strategies.

**Live performance dashboard integration:**
- `DemoTracker.to_dict()` serializes P&L, trades, drawdown to `LivePerformanceOut`-compatible JSON
- `PineLiveEngine._flush_performance()` writes `~/.quantforge/live/{strategy_name}/live_performance.json` after each bar
- Web backend `_find_perf_files()` discovers these files via `rglob("live_performance.json")`
- WebSocket endpoint `/ws/live/performance` streams updates every 3 seconds

### Pine Tests

```bash
uv run pytest quantforge/pine/tests/ -v  # 89 tests (55 interpreter/parser + 11 live engine + 16 optimizer + 7 TV alignment)
```

## Declarative Strategy DSL (`quantforge/dsl/`)

A simplified, declarative Python API for defining trading strategies in ~15-30 lines. Uses streaming indicators from `quantforge/indicators/`.

### Quick Start

```python
from quantforge.dsl import Strategy, Param

class EMACross(Strategy):
    name = "decl_ema_crossover"
    timeframe = "15m"
    fast_period = Param(12, min=5, max=30, step=2)
    slow_period = Param(26, min=15, max=60, step=5)

    def setup(self):
        self.ema_fast = self.add_indicator("ema", self.fast_period)
        self.ema_slow = self.add_indicator("ema", self.slow_period)

    def on_bar(self, bar):
        if self.ema_fast.crossover(self.ema_slow):
            return self.BUY
        if self.ema_fast.crossunder(self.ema_slow):
            return self.SELL
        return self.HOLD
```

### Package Structure

```
quantforge/dsl/
├── __init__.py          # Public API: Strategy, Param, Bar, Indicator
├── api.py               # Strategy base class, Param descriptor, Bar dataclass
├── indicators.py        # Indicator wrapper (crossover/crossunder/history/lookback)
├── registry.py          # Auto-registration via metaclass
├── backtest.py          # Simple backtester with 1-bar signal delay
├── runner.py            # CLI runner for declarative strategies
├── examples/            # 5 example strategies
│   ├── ema_cross.py     # EMA Crossover (trend following)
│   ├── rsi_reversion.py # RSI Mean Reversion
│   ├── macd_cross.py    # MACD Crossover
│   ├── bb_reversion.py  # Bollinger Bands Mean Reversion
│   └── momentum_adx.py  # Momentum + ADX (regime-filtered)
└── tests/
    └── test_new_api.py  # 35 tests (parity, indicators, backtest, registration)
```

### Key Components

- **Strategy**: Base class with `setup()` + `on_bar()` API, signal constants (HOLD=0, BUY=1, SELL=-1, CLOSE=2), auto-registration via metaclass
- **Param**: Descriptor with optimization grid support (`Param(12, min=5, max=30, step=2)`)
- **Indicator**: Wrapper around streaming indicators with `.value`, `.ready`, `.crossover()`, `.crossunder()`, `[n]` lookback
- **Bar**: OHLCV dataclass passed to `on_bar()`

### Supported Indicators

`"ema"`, `"sma"`, `"rsi"`, `"atr"`, `"adx"`, `"bb"`, `"roc"` — all reuse `StreamingXXX` classes from `quantforge/indicators/streaming.py`

### Backtest

```python
from quantforge.dsl.backtest import backtest
result = backtest(EMACross, bars, fast_period=8, slow_period=21)
print(result.total_return_pct, result.trade_count, result.win_rate)
```

### DSL Tests

```bash
uv run pytest quantforge/dsl/tests/ -v  # 35 tests
```

## Backtest Data Infrastructure

### Local Data Cache & Multi-Source Validation

`quantforge/backtest/data/database.py` provides a SQLite cache for historical kline data:
- **KlineDatabase**: Stores OHLCV bars in `~/.quantforge/data/klines.db` (configurable)
- API: `save()`, `load()`, `has_data()`, `get_gaps()`, `stats()`
- Unique constraint: `(exchange, symbol, interval, timestamp)`
- Uses `calendar.timegm()` for timezone-safe UTC epoch conversion

`quantforge/backtest/data/cached_provider.py` provides smart caching + validation:
- **CachedDataProvider**: `fetch()` checks cache first, only pulls gaps from exchange
- **ValidatedData**: `fetch_and_validate()` compares data across multiple exchanges
- Returns: `primary_data`, `validation_report`, `anomalies`, `is_valid`

### Monte Carlo Simulation & Stress Testing

The `quantforge/backtest/simulation/` submodule provides statistical simulation capabilities:

| Module | Class | Purpose |
|--------|-------|---------|
| `bootstrap.py` | `BlockBootstrap` | Block bootstrap resampling on log-returns |
| `monte_carlo.py` | `GBMGenerator` | Geometric Brownian Motion path generation |
| `monte_carlo.py` | `JumpDiffusionGenerator` | Merton Jump Diffusion (GBM + Poisson jumps) |
| `stress_test.py` | `StressTestGenerator` | Importance sampling: crash, spike, volatility scenarios |
| `report.py` | `SimulationReport` | Distribution statistics, confidence intervals, plots |

### Supported Exchanges

| Exchange | CCXT ID | Maker Fee | Taker Fee |
|----------|---------|-----------|-----------|
| Bitget | `bitget` | 0.02% | 0.05% |
| Binance | `binance` | 0.02% | 0.04% |
| OKX | `okx` | 0.02% | 0.05% |
| Bybit | `bybit` | 0.02% | 0.05% |
| Hyperliquid | `hyperliquid` | 0.02% | 0.05% |

## Unified CLI (`quantforge-cli`)

`quantforge-cli` is a Click-based command group that mirrors every web route as a CLI subcommand. Stateless ops read the filesystem directly (no server needed); stateful ops hit the web API at `$QF_API_URL` (default `http://127.0.0.1:8000`).

| Command | Web equivalent | Mode |
|---|---|---|
| `strategies list` / `show <n>` / `source <n>` / `rename <old> <new>` | `/strategies*` | filesystem |
| `exchanges list` | `/exchanges` | static |
| `engines list [--via-server]` | `/live/engines` | persist file or HTTP |
| `engines start <pine> [--via-server]` | `/live/start` | foreground or HTTP |
| `engines stop <id>` | `/live/stop/{id}` | HTTP only |
| `engines performance [strategy]` | `/live/performance` | persist file |
| `agent skills` | `/agent/skills` | filesystem |
| `agent run --skill X --strategy Y [--via-server]` | `/agent/run` | foreground subprocess or HTTP |
| `agent status <id>` / `stop <id>` | `/agent/{id}*` | HTTP only |
| `backtest <pine>` / `optimize <pine>` / `live <pine>` | `/backtest/run`, `/optimize/run`, `/live/start` | wraps `quantforge.pine.cli` |

All list-style commands accept `--json` for scripting. Pine names auto-resolve from `quantforge/pine/strategies/`. Sources live in `quantforge/cli/commands/{strategies,exchanges,engines,agent,pine}_cmd.py` plus `_http.py` for the HTTP client.

## TiMi Optimizer A/B Harness (`eval/optimizer_ab/`)

Air-gapped evaluation framework for comparing variants of the LLM optimizer (`~/.openclaw/skills/quantforge-optimizer`). Each trial = (method, strategy, regime, seed); `runner.py` invokes Claude Code in an isolated staged skill dir on the train window only, then `holdout_eval.py` runs the optimized .pine on the regime's holdout window in a separate process so the agent never sees OOS data.

| File | Role |
|---|---|
| `test_set.yaml` | Frozen 3-tier strategy split (dev/test/holdout) × 3 regimes × seeds. |
| `methods/<name>/SKILL.md` | One per method under test; `baseline/SKILL.md` snapshots the canonical skill. |
| `runner.py` | Single trial: stage skill, invoke `claude --print --stream-json`, capture `FINAL_OUTPUT:` sentinel. |
| `holdout_eval.py` | Runs train + holdout backtests; **filters equity_curve and trades by bar timestamp so the warmup prefix doesn't contaminate OOS metrics**. |
| `orchestrate.py` | Matrix loop over cells; resume key is `cell_id + "__"` so seed=1 is not a prefix of seed=10. Appends rows to `results/matrix.csv`. |
| `analyze.py` | Per-method aggregates + paired Wilcoxon + bootstrap 95 % CI on Δ. |
| `rebuild_csv.py` | Regenerate the CSV from existing trial JSONs without re-invoking the runner (use after metric-formula fixes). |

Air-gap invariants: agent's prompt pins `--start --end` to the train window; `stage_skill` rewrites every hardcoded `--start YYYY-MM-DD --end YYYY-MM-DD` snippet in SKILL.md / scripts / references to the trial's training window so the agent cannot copy stale examples; OOS metrics are computed only on bars with `time >= start_unix`; per-trial `optimization_log.jsonl` is wiped so cross-run learning doesn't pollute baselines.

Known limitations: Claude CLI exposes no `--seed`, so `seeds: [1, 2, 3]` are *replicate indices*, not reproducible random seeds — report median ± bootstrap CI across seeds, not single-point estimates.

## Claude Code Memories

### Workflow Rules
- After every code change: update CLAUDE.md and CLAUDE_CN.md, then commit and push to dev branch

### CLI Usage Warnings
- Do not run quantforge-cli moniter in claude code

### Ruff Usage
- Lint all files in the current directory with `uvx ruff check`
- Format all files in the current directory with `uvx ruff format`
