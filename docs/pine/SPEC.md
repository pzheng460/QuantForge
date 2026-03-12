# Pine Script Engine Specification

## Overview

The QuantForge Pine Script engine parses, interprets, and transpiles TradingView Pine Script v5/v6.
It enables users to run Pine strategies on historical data and optionally convert them to native
QuantForge Python strategies.

## Architecture

```
quantforge/pine/
├── parser/          # Source → AST
│   ├── grammar.py   # Lark PEG grammar
│   ├── ast_nodes.py # Typed AST dataclasses
│   └── parser.py    # Preprocessor + Lark + Transformer
├── interpreter/     # AST → execution
│   ├── series.py    # PineSeries time-series type
│   ├── context.py   # Execution context (OHLCV, variables)
│   ├── runtime.py   # Bar-by-bar engine, BacktestResult
│   └── builtins/
│       ├── ta.py       # ta.sma/ema/rsi/atr/adx/macd/bbands/stoch/crossover/crossunder/highest/lowest/change/tr
│       ├── math_fn.py  # math.abs/max/min/round/log/sqrt/pow
│       ├── strategy.py # strategy.entry/exit/close/close_all + position tracking
│       └── input_fn.py # input.int/float/bool/string/source
└── transpiler/
    └── codegen.py   # AST → QuantForge Python strategy code
```

## Critical Formula Requirements

### EMA (Exponential Moving Average)
- Alpha = `2 / (length + 1)`
- Seed with SMA of first `length` bars
- Formula: `ema = alpha * value + (1 - alpha) * prev_ema`

### RMA (Wilder Smoothing / Running Moving Average)
- Formula: `rma = (prev * (length - 1) + value) / length`
- Seed with SMA of first `length` bars
- Used internally by RSI, ATR, and ADX

### RSI (Relative Strength Index)
- Uses **RMA** (Wilder smoothing), NOT SMA, for avg gain/loss
- Seed avg_gain and avg_loss with SMA of first `length` changes
- `RS = avg_gain / avg_loss`
- `RSI = 100 - 100 / (1 + RS)`

### ATR (Average True Range)
- True Range: `max(high - low, |high - prev_close|, |low - prev_close|)`
- Smoothed with **RMA** (Wilder smoothing)

### ADX (Average Directional Index)
- +DM / -DM smoothed with RMA
- +DI / -DI = 100 * smoothed_DM / ATR
- DX = 100 * |+DI - -DI| / (+DI + -DI)
- ADX = RMA(DX, length)

## Order Execution

- Orders execute on **NEXT bar open** (default)
- `strategy.entry("id", strategy.long)` queues an entry order
- `strategy.close("id")` queues a close order
- Pending orders are executed at the open of the following bar

## Supported Features

### Pine Script Syntax
- Version directives (`//@version=5`)
- `strategy()` / `indicator()` declarations
- Variable assignment (`=`, `:=`, `+=`, `-=`, `*=`, `/=`)
- `var` declarations (persist across bars)
- Tuple assignment (`[a, b] = func()`)
- if/else if/else blocks
- for/for-in/while loops with break/continue
- User-defined functions (`name(params) => body`)
- Ternary expressions (`cond ? a : b`)
- History reference operator (`series[offset]`)
- Member access (`ta.sma`, `strategy.long`)

### Built-in Functions
- **ta.***: sma, ema, rma, rsi, atr, adx, macd, bb, stoch, crossover, crossunder, highest, lowest, change, tr
- **math.***: abs, max, min, round, log, sqrt, pow, ceil, floor, sign
- **strategy.***: entry, exit, close, close_all
- **input.***: int, float, bool, string, source
- **Utilities**: na(), nz(), plot() (no-op), alert() (no-op)

## Usage

```python
from quantforge.pine import parse, PineRuntime, ExecutionContext, BarData

# Parse Pine Script
script = parse(pine_source_code)

# Create execution context with OHLCV data
ctx = ExecutionContext.from_arrays(open, high, low, close, volume)

# Run
runtime = PineRuntime(ctx)
result = runtime.run(script)

# Access results
print(result.net_profit, result.total_trades, result.win_rate)
```
