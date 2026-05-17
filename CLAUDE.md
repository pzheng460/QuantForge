# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Project Overview

QuantForge is a Python 3.11+ quantitative trading platform organised around
**three loosely-coupled pieces**:

1. **Pine engine** (`quantforge/pine/`) — TradingView-compatible Pine Script
   v5 parser, interpreter, transpiler, optimizer, and live trading engine.
   **All trading strategies are `.pine` files**; this is the primary layer.
2. **DSL engine** (`quantforge/dsl/`) — a thin declarative Python API
   (`class MyStrategy(Strategy): on_bar(...)`) for quick prototypes.
3. **Web dashboard** (`apps/dashboard/`) — FastAPI backend + React/Vite
   frontend that wraps every Pine/DSL feature in a UI: live trading,
   backtest, grid optimize, AI optimize.

Exchange connectivity is provided by **`ccxt` directly** — there is no
hand-written per-exchange OMS/WS code in the live path. The Pine engine
talks to `ccxt.binance(...)`, `ccxt.okx(...)`, etc. through
`quantforge/pine/live/connector.py::CcxtConnector`.

## Development Commands

### Setup
```bash
uv sync                       # install runtime deps
uv sync --group dev           # install dev/test deps
uv add --dev pre-commit && pre-commit install   # required for contributions
```

### Testing
```bash
uv run pytest                                       # full suite

uv run pytest quantforge/pine/tests/ -v             # 89 Pine engine tests
uv run pytest quantforge/dsl/tests/ -v              # 35 DSL tests
uv run pytest test/cli/ -v                          # 18 CLI surface tests
uv run pytest test/dashboard/ -v                    # 10 backend router tests
uv run pytest test/optimizer_ab/ -v                 # 26 A/B harness tests
```
`pytest.ini` enables `asyncio_mode = auto`. Total ≈ 178 tests.

### Code Quality
```bash
uvx ruff check                # lint
uvx ruff format               # format
```

## Web Dashboard (`apps/dashboard/`)

```
apps/dashboard/
├── backend/
│   ├── main.py              # FastAPI app; mounts SPA from frontend/dist/ in prod
│   ├── jobs.py              # _fetch_ohlcv, _resolve_pine_source, _run_pine_optimize,
│   │                        #   _run_wfo, _run_three_stage, _run_heatmap
│   ├── live_engines.py      # in-memory engine manager (start/stop/list)
│   ├── models.py            # Pydantic request/response schemas
│   └── routers/
│       ├── strategies.py    # /api/strategies, /api/exchanges
│       ├── backtest.py      # /api/backtest/{run, {id}, cancel/{id}}
│       ├── optimize.py      # /api/optimize/{run, {id}, cancel/{id}, ws/{id}}
│       ├── live.py          # /api/live/{start, stop/{id}, engines, performance, ws}
│       └── agent.py         # /api/agent/{skills, run, {id}, {id}/stop, ws/{id}}
├── frontend/
│   ├── src/
│   │   ├── App.tsx          # Linear/Vercel-style masthead + nav + ErrorBoundary
│   │   ├── pages/{Dashboard,Backtest,Optimizer}.tsx
│   │   ├── components/
│   │   │   ├── ResizableSidebarShell.tsx  # SidebarProvider + draggable handle
│   │   │   ├── ResizeHandle.tsx           # capture-phase pointer events
│   │   │   ├── StrategyTester.tsx
│   │   │   ├── AgentTraceViewer.tsx
│   │   │   ├── MetricsSummary.tsx
│   │   │   ├── charts/{TradingChart,EquityChart,DrawdownChart,HeatmapChart}.tsx
│   │   │   └── ui/                        # shadcn primitives (Button, Input, etc.)
│   │   ├── api/client.ts                  # `/api` base, ApiError class
│   │   ├── hooks/use-queries.ts           # React Query hooks
│   │   ├── stores/{dashboard,backtest,optimizer,catalog}Store.ts (zustand)
│   │   └── types.ts
│   └── vite.config.ts                     # proxies /api → :8000 in dev
└── start.sh                                # dev (vite HMR + uvicorn --reload) | --prod (vite build + StaticFiles)
```

### Frontend design system

- **Stack**: React 18 + Vite 5 + TypeScript (strict) + Tailwind 3 + shadcn/ui
  (Radix) + lightweight-charts + recharts + zustand + react-router 6.
- **Theme**: cool-white Linear/Vercel aesthetic. CSS variables in
  `index.css`:
  - `--background` pure white, `--surface` zinc-50 (very subtle cool tint
    for page bg behind cards), `--card` pure white.
  - `--primary` zinc-900 (#18181B), `--brand` blue-500 (#3B82F6).
  - P&L semantic: `--positive` green-600, `--negative` red-600.
  - Precision shadows (`--shadow-{xs,sm,md,lg,glow}`) instead of soft
    floaty defaults.
- **Typography**: **Geist** sans + **Geist Mono** (no Fraunces/Inter).
  Body letter-spacing `-0.011em`. All numbers use mono + `tabular-nums`.
- **Layout invariants** (do not break):
  - App root is `h-screen overflow-hidden flex-col` — viewport-bounded.
  - SidebarProvider gets `!min-h-0 h-full overflow-hidden` (overrides
    shadcn's default `min-h-svh` which would let children push past the
    viewport).
  - Chart panes use `flex-1 min-h-0 overflow-hidden` so lightweight-charts
    canvases can actually shrink under flex pressure.

### dev vs prod modes

```bash
./apps/dashboard/start.sh           # dev: vite :5173 + uvicorn :8000 --reload
./apps/dashboard/start.sh --prod    # prod: vite build → dist/; FastAPI serves SPA on :8000
./apps/dashboard/start.sh stop      # stop both
```

Dev `--reload` is **scope-bounded** to `apps/dashboard/backend` +
`quantforge/` so StatReload doesn't waste a CPU core scanning `eval/`,
`node_modules/`, agent-job JSON writes, etc. Prod mode runs without
`--reload` and mounts `apps/dashboard/frontend/dist/` via FastAPI
`StaticFiles` + SPA fallback to `index.html`.

### Resizable sidebars (`ResizableSidebarShell`)

Each page (`Dashboard`, `Backtest`, `Optimizer`) wraps its `SidebarProvider`
in `ResizableSidebarShell` so the user can drag the sidebar's right edge.
Width is persisted per page (`localStorage["sidebar-width:<storageKey>"]`),
double-click resets to default.

Vertical splits inside pages (chart ↔ bottom panel) use the same
`ResizeHandle` component. **Drag implementation pitfall fixed once,
don't reintroduce**: lightweight-charts attaches `pointermove` listeners
that capture events anywhere over its container. Naive `window.addEventListener`
handlers get starved. The hook uses **document-level capture-phase
listeners** (`document.addEventListener('pointermove', ..., true)`) so it
beats the chart's bubble-phase handlers. See `useResizablePanel()` in
`apps/dashboard/frontend/src/pages/{Dashboard,Backtest}.tsx`.

### AI Optimizer job persistence

`POST /api/agent/run` spawns a `claude --print --stream-json` subprocess,
streams its events back over `/ws/agent/{job_id}`, and persists every
state change to `~/.quantforge/dashboard/agent_jobs/{job_id}.json`.

- **Single-job retention**: creating a new job wipes prior files.
- **Reload recovery**: on backend startup, any job whose persisted status
  was `running`/`pending` is marked `failed` with a "Backend restarted"
  note, **and the captured child PID is SIGTERM'd** so it doesn't keep
  burning CPU.
- **Frontend 404 self-heal**: `useAgentStatus` stops polling on 404 and
  the Optimizer page auto-calls `resetAgent()` so the UI doesn't get stuck
  on a phantom job.

### Grid Search progress

`quantforge/pine/optimize.py::run_optimization()` accepts `progress_cb`.
Backend wires it through `_run_pine_optimize(req, job_id)` to write
`progress = { completed, total, avg_secs_per_combo, elapsed_secs }` into
the in-memory job, then the WS pushes the full status every second. The
frontend renders a progress bar + "≈ 2m 15s remaining · 1.23s / combo".

## Pine Script Engine (`quantforge/pine/`)

Parser + interpreter + transpiler + live engine + optimizer for
TradingView-compatible Pine Script v5.

### Pine Strategies (`quantforge/pine/strategies/`)

Current `.pine` files (visible in `/api/strategies`):
`bb_squeeze`, `bb_squeeze_v2`, `bollinger_band`, `bollinger_band_v4`,
`dual_regime`, `ema_crossover`, `ema_crossover_v2`, `ema_crossover_v3`,
`hurst_kalman`, `macd_trend`, `momentum_adx`, `rsi_momentum`,
`sma_trend`.

AI-optimized outputs land in `quantforge/pine/strategies/optimized/`
(not currently exposed by `/api/strategies` — by design, kept as
checkpoints, not live candidates).

Test fixtures in `quantforge/pine/tests/fixtures/`:
`ema_cross.pine`, `rsi_strategy.pine`, `rsi_mean_revert.pine`,
`macd_cross.pine`, `bb_strategy.pine`, `ema_cross_5_13.pine`.

### Pine CLI

```bash
# Backtest
python -m quantforge.pine.cli backtest my.pine --symbol BTC/USDT:USDT --exchange bitget --timeframe 15m --start 2026-01-01 --end 2026-03-12 --warmup-days 60

# Grid optimize (over input.int / input.float ranges)
python -m quantforge.pine.cli optimize my.pine --symbol BTC/USDT:USDT --exchange bitget --timeframe 15m --start 2026-01-01 --end 2026-03-12 --metric sharpe --top 10 --json results.json

# Transpile to standalone Python (no Pine dependency at runtime)
python -m quantforge.pine.cli transpile my.pine --output strategy.py

# Live (demo/sandbox)
python -m quantforge.pine.cli live my.pine --exchange okx --demo --symbol BTC/USDT:USDT --timeframe 1h

# Live (real money — requires --confirm-live)
python -m quantforge.pine.cli live my.pine --exchange okx --no-demo --confirm-live --symbol BTC/USDT:USDT --timeframe 1h --leverage 5
```

### Pine Transpiler

`quantforge/pine/transpiler/codegen.py` emits self-contained Python that
replicates the Pine interpreter bit-for-bit. **TA mappings**: `ta.ema → _EMACalc`,
`ta.sma → _SMACalc`, `ta.rsi → _RSICalc` (Wilder/RMA smoothing),
`ta.macd → _MACDCalc`, `ta.atr → _ATRCalc`, `ta.adx → _ADXCalc`,
`ta.bb → _BBCalc`, `ta.stoch → _StochCalc`, `ta.stdev → _StdevCalc`,
`ta.crossover/crossunder → _crossover/_crossunder` (with prev-bar
tracking), `ta.highest/lowest → _HighestCalc/_LowestCalc`,
`ta.change → _ChangeCalc`. **Strategy mappings**:
`strategy.entry → tracker.queue_entry()`, `strategy.close → tracker.queue_close()`
(orders fill at next bar's open). **21 parity tests** lock this behavior.

### Pine Live Engine

Same interpreter runs backtest and live — no transpilation between modes.
Key files:
- `quantforge/pine/live/engine.py` — `PineLiveEngine`: warmup + live kline
  loop with exact-bar timing
- `quantforge/pine/live/order_bridge.py` — `OrderBridge`: Pine
  `strategy.entry/close/exit` callbacks → ccxt orders
- `quantforge/pine/live/connector.py` — `CcxtConnector` (handles demo
  flag, loads API keys from `settings`, calls `set_leverage`,
  `create_{market,limit}_order`, `fetch_positions`, `fetch_balance`)

Live performance is JSON-dumped to
`~/.quantforge/live/{strategy_name}/live_performance.json` after each
bar; backend `_find_perf_files()` discovers and streams via
`/ws/live/performance` every 3 s.

### Supported `ta.*` Functions
`ta.sma`, `ta.ema`, `ta.rma`, `ta.rsi`, `ta.atr`, `ta.adx`, `ta.macd`,
`ta.bb`, `ta.stoch`, `ta.stdev`, `ta.crossover`, `ta.crossunder`,
`ta.highest`, `ta.lowest`, `ta.change`, `ta.tr`.

## Streaming Indicators (`quantforge/indicators/streaming.py`)

| Class | Description |
|-------|-------------|
| `StreamingEMA(period)` | Exponential moving average |
| `StreamingSMA(period)` | Simple moving average (rolling window) |
| `StreamingATR(period)` | Average true range (Wilder smoothing) |
| `StreamingROC(period)` | Rate of change |
| `StreamingADX(period)` | Average directional index |
| `StreamingBB(period, multiplier)` | Bollinger bands |
| `StreamingRSI(period)` | RSI with Wilder/RMA smoothing |

All share: `.value` (Optional[float]), `.update(...)`, `.reset()`. Used
by DSL (`quantforge/dsl/indicators.py`) and Pine transpiler TA calculators.

## Declarative Strategy DSL (`quantforge/dsl/`)

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
        if self.ema_fast.crossover(self.ema_slow): return self.BUY
        if self.ema_fast.crossunder(self.ema_slow): return self.SELL
        return self.HOLD
```

Supported indicator names (passed to `add_indicator`): `ema`, `sma`,
`rsi`, `atr`, `adx`, `bb`, `roc`. 35 tests.

## Backtest Data Infrastructure (`quantforge/backtest/`)

### Local cache
`quantforge/backtest/data/database.py::KlineDatabase` — SQLite cache at
`~/.quantforge/data/klines.db`. API: `save / load / has_data / get_gaps
/ stats`. Unique constraint on `(exchange, symbol, interval, timestamp)`.
TZ-safe via `calendar.timegm()`.

`quantforge/backtest/data/cached_provider.py::CachedDataProvider.fetch()`
returns cached bars + only fetches gaps from exchange.
`ValidatedData.fetch_and_validate()` cross-checks across exchanges and
returns `{primary_data, validation_report, anomalies, is_valid}`.

### Monte Carlo & stress testing — `quantforge/backtest/simulation/`

| Module | Class | Purpose |
|---|---|---|
| `bootstrap.py` | `BlockBootstrap` | Block bootstrap on log-returns |
| `monte_carlo.py` | `GBMGenerator` | Geometric Brownian Motion paths |
| `monte_carlo.py` | `JumpDiffusionGenerator` | Merton jump diffusion |
| `stress_test.py` | `StressTestGenerator` | Crash / spike / vol scenarios |
| `report.py` | `SimulationReport` | Distribution stats + plots |

## Supported Exchanges

| Exchange | ccxt id | Maker | Taker |
|---|---|---|---|
| Bitget | `bitget` | 0.02% | 0.05% |
| Binance | `binance` | 0.02% | 0.04% |
| OKX | `okx` | 0.02% | 0.05% |
| Bybit | `bybit` | 0.02% | 0.05% |
| Hyperliquid | `hyperliquid` | 0.02% | 0.05% |

## Secrets Configuration

API keys live in **`.keys/.secrets.toml`** (gitignored), loaded via
dynaconf in `quantforge/constants.py`. On import, `_check_secrets_file_perms()`
verifies the file is mode `0600` and auto-chmod's it down + warns if
not. Layout:

```toml
[BITGET.LIVE]
API_KEY = "..."
SECRET = "..."
PASSPHRASE = "..."

[OKX.LIVE.ACCOUNT1]
API_KEY = "..."
SECRET = "..."
PASSPHRASE = "..."

[BINANCE.TESTNET]
API_KEY = "..."
SECRET = "..."
```

Connector keys per exchange are in
`quantforge/pine/live/connector.py::_create_exchange()` — read once,
applied to `ccxt.<id>({apiKey, secret, password})`.

## Symbol Format

`{base}{quote}-{instrument_type}.{exchange}`, e.g.
`BTCUSDT-PERP.BINANCE`. ccxt's own per-exchange symbol format
(`BTC/USDT:USDT`) is also used directly in CLI / API calls.

## Unified CLI (`quantforge-cli`)

Click command group mirroring every web route. Stateless ops read disk;
stateful ops hit `$QF_API_URL` (default `http://127.0.0.1:8000/api`).

| Command | Web equivalent | Mode |
|---|---|---|
| `strategies list / show <n> / source <n> / rename <old> <new>` | `/strategies*` | filesystem |
| `exchanges list` | `/exchanges` | static |
| `engines list [--via-server]` | `/live/engines` | persist file or HTTP |
| `engines start <pine> [--via-server]` | `/live/start` | foreground or HTTP |
| `engines stop <id>` | `/live/stop/{id}` | HTTP |
| `engines performance [strategy]` | `/live/performance` | persist file |
| `agent skills` | `/agent/skills` | filesystem |
| `agent run --skill X --strategy Y [--via-server]` | `/agent/run` | foreground subprocess or HTTP |
| `agent status <id>` / `stop <id>` | `/agent/{id}*` | HTTP |
| `backtest <pine>` / `optimize <pine>` / `live <pine>` | `/backtest/run`, `/optimize/run`, `/live/start` | wraps `quantforge.pine.cli` |

All list commands accept `--json`. Pine names auto-resolve from
`quantforge/pine/strategies/`. Sources in
`quantforge/cli/commands/{strategies,exchanges,engines,agent,pine}_cmd.py`
plus `_http.py` for HTTP.

## AI Optimizer Skill (`.claude/skills/quantforge-optimizer/`)

The project-scoped Claude skill that the Web UI's "AI Optimize" mode
invokes. Implements TiMi-style closed-loop mathematical reflection on
backtest failures. Lives in-repo so improvements can be committed and
A/B-evaluated.

## TiMi Optimizer A/B Harness (`eval/optimizer_ab/`)

Air-gapped framework for comparing variants of the optimizer skill.
Each trial = `(method, strategy, regime, seed)`; `runner.py` runs Claude
Code in an isolated staged skill dir on the *train* window only, then
`holdout_eval.py` runs the optimized `.pine` on the regime's *holdout*
window so the agent never sees OOS data.

| File | Role |
|---|---|
| `test_set.yaml` | Frozen 3-tier strategy split × regimes × seeds |
| `methods/<name>/SKILL.md` | One per method under test |
| `runner.py` | Single trial: stage skill, invoke Claude, capture `FINAL_OUTPUT:` |
| `holdout_eval.py` | Filters equity/trades by bar timestamp; warmup prefix never counted |
| `orchestrate.py` | Matrix loop; resume key `cell_id + "__"` (so seed=1 ≠ prefix of seed=10) |
| `analyze.py` | Per-method aggregates + paired Wilcoxon + bootstrap 95% CI |
| `rebuild_csv.py` | Regenerate CSV from existing trial JSONs without re-running |
| `cross_review.py` | Cross-model review variant |

**Air-gap invariants**: agent's prompt pins `--start --end` to train
window; `stage_skill` rewrites every hardcoded date snippet in SKILL.md
/ scripts / references; OOS metrics computed only on `time >= start_unix`;
per-trial `optimization_log.jsonl` wiped to avoid cross-run learning.

**Known limitation**: Claude CLI exposes no `--seed`, so `seeds: [1, 2, 3]`
are *replicate indices*, not reproducible random seeds — report median
± bootstrap CI across seeds, not single-point estimates.

## Conventions

- **Imports**: always absolute (`from quantforge.pine ...`), never
  relative.
- **Strategies live in `.pine` files**, never in `.py`. The DSL is for
  prototyping only.
- **No per-exchange hand-written connector code** — use ccxt unified API.
  If a feature is missing, monkey-patch on the ccxt instance, don't write
  a parallel adapter.

## Workflow Rules

- After every code change: update `CLAUDE.md` and `CLAUDE_CN.md`, then
  commit and push to the `dev` branch.

## CLI Usage Warnings

- Do not run `quantforge-cli monitor` from inside Claude Code (it's
  blocking + TUI-flavored).

## Ruff Usage

- Lint: `uvx ruff check`
- Format: `uvx ruff format`
