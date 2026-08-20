# QuantForge Development Guide

## Architecture

QuantForge is Python-first. Trading strategies are reviewed Python classes
registered through `quantforge.strategy`. There is no runtime source editor or
embedded scripting engine.

The canonical path is:

```text
Strategy → OrderIntent / MultiLegOrderIntent → RiskEngine
         → ExecutionService → Broker Adapter
```

First-class assets are US equities, US equity options, crypto spot, and crypto
perpetual/futures. Crypto options are not part of the first phase.

## Important modules

- `quantforge/strategy/`: strategy API, registry, bars, indicators
- `quantforge/strategies/`: reviewed built-in strategies
- `quantforge/backtest/`: shared next-bar-open backtester
- `quantforge/domain/`: instruments, intents, events
- `quantforge/portfolio/`: positions and cash ledger
- `quantforge/risk/`: mandatory global risk checks
- `quantforge/execution/`: the only order submission boundary
- `quantforge/adapters/`: CCXT, Schwab, paper, and market-data adapters
- `quantforge/options/`: pricing, lifecycle, covered-call manager
- `quantforge/pine/`: Pine Script v5/v6 parser / interpreter / transpiler
- `apps/dashboard/`: FastAPI backend and parameter-only React UI

## Commands

```bash
uv run pytest -q
uv run ruff check quantforge apps/dashboard/backend test
uv run quantforge-cli strategies list

cd apps/dashboard/frontend
npm run build
```

## Strategy rules

- Subclass `Strategy` or `BarStrategy`.
- Define a strict Pydantic `StrategyConfig`.
- Register with `@register_strategy`.
- Publish parameters through the generated schema.
- Do not accept arbitrary strategy source from HTTP requests.
- Backtests and live engines must use the same strategy implementation.

## Risk and execution rules

- Strategies emit intent; they never call broker APIs directly.
- Every order passes through `ExecutionService` and `RiskEngine`.
- Keep live enable/halt, notional, leverage, spread, quote-age, option coverage,
  and daily-entry limits non-bypassable.
- Multi-leg Schwab option strategies must be submitted atomically.
- Do not describe option Delta as exact assignment probability.
- Historical modeled option quotes must carry `approximate_unvalidated`.

## Secrets

Never print or commit credentials. Schwab OAuth tokens are stored under
`~/.quantforge/schwab/` with restricted permissions. Application credentials
are loaded from `.keys/.secrets.toml`.

## Pine Script Engine

QuantForge includes a Pine Script engine that can parse, interpret, and
transpile TradingView Pine Script v5/v6 strategies.

### Architecture

```
pine_strategy.pine → [Parser] → [AST] → [Interpreter] → BacktestResult (trades, equity, metrics)
                                      → [Transpiler]  → Python QuantForge Strategy code
```

### Directory Structure

```
quantforge/pine/
├── __init__.py                          # Public API: PineParser, PineRuntime, PineTranspiler
├── parser/
│   ├── grammar.py                       # Lark PEG grammar (Pine v5/v6)
│   ├── ast_nodes.py                     # Typed dataclass AST nodes
│   └── parser.py                        # Pine source → AST (Lark + Transformer)
├── interpreter/
│   ├── series.py                        # PineSeries (bar-indexed historical values)
│   ├── context.py                       # ExecutionContext (variables, OHLCV, scopes, dispatch)
│   ├── runtime.py                       # PineRuntime (bar-by-bar execution engine)
│   └── builtins/
│       ├── ta.py                        # ta.* functions (SMA, EMA, RSI, ATR, ADX, MACD, etc.)
│       ├── math_fn.py                   # math.* functions (abs, max, min, log, sqrt, etc.)
│       ├── strategy.py                  # StrategyEngine (positions, orders, trades, equity)
│       └── input_fn.py                  # InputManager (input.int/float/bool/string/source)
├── transpiler/
│   └── codegen.py                       # AST → Python QuantForge Strategy code
└── tests/
    ├── test_parser.py                   # 28 parser tests
    ├── test_builtins.py                 # 41 builtin tests (series, math, ta, strategy, input)
    ├── test_interpreter.py              # 22 end-to-end tests (parse → run → verify trades)
    └── fixtures/                        # Sample .pine scripts (ema_cross, rsi_strategy)
```

### Quick Start

```python
from quantforge.pine import PineParser, PineRuntime
import pandas as pd

parser = PineParser()
ast = parser.parse(open("strategy.pine").read())
runtime = PineRuntime(ast, initial_capital=10000, commission=0.001)
result = runtime.run(ohlcv_df)  # DataFrame with open/high/low/close/volume
print(result.summary())
```

### Execution Model

- **Bar-by-bar**: Script body evaluated once per bar, matching TradingView
- **Order fill**: Orders placed on bar N fill on bar N+1's open (default)
- **process_orders_on_close**: Orders fill on current bar's close when enabled
- **Series propagation**: Every variable maintains history via PineSeries
- **Stateful ta.***: Each ta.* call-site gets a unique instance (keyed by line+counter)
- **var persistence**: `var x = 0` initializes once, persists across bars

### Supported Built-ins

| Namespace | Functions |
|-----------|-----------|
| `ta.*` | sma, ema, rma, rsi, atr, adx, macd, bbands, stoch, crossover, crossunder, highest, lowest, change, tr |
| `math.*` | abs, max, min, round, log, sqrt, pow, ceil, floor, sign |
| `strategy.*` | entry, exit, close, close_all, position_size, position_avg_price, long, short |
| `input.*` | int, float, bool, string, source |
| globals | na(), nz(), fixnan(), alert() |

### Running Pine Script Tests

```bash
uv run pytest quantforge/pine/tests/ -v  # All 91 tests
uv run pytest quantforge/pine/tests/test_parser.py -v      # 28 parser tests
uv run pytest quantforge/pine/tests/test_builtins.py -v     # 41 builtin tests
uv run pytest quantforge/pine/tests/test_interpreter.py -v  # 22 end-to-end tests
```
