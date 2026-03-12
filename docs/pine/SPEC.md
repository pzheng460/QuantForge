# Pine Script Engine - Technical Specification

## Overview
QuantForge Pine Script Engine: Parse, interpret, and transpile TradingView Pine Script strategies.

## Architecture (Hybrid)

```
pine_strategy.pine
       │
       ▼
  [Pine Parser]  (Lark PEG grammar, Pine v5/v6)
       │
       ▼
     [AST]
       │
       ├──→ [Interpreter] → Exact bar-by-bar backtest (match TradingView)
       │
       └──→ [Transpiler]  → QuantForge Python Strategy (for live deployment)
```

## Phase 1: Pine Parser + Core Interpreter

### 1.1 Parser
- Support Pine Script v5 and v6
- Use Lark (PEG parser for Python) - pure Python, no native deps
- Output: typed AST nodes

### 1.2 Core Language Features
- Types: int, float, bool, string, color, series (implicit)
- Variables: `var`, regular, `varip`
- Control: if/else, for, while, switch
- Functions: user-defined, built-in
- Series indexing: `close[1]`, `high[3]`
- Operators: arithmetic, comparison, logical, ternary

### 1.3 Built-in Functions (Priority Order)

#### Tier 1 (Must Have)
- `ta.sma`, `ta.ema`, `ta.rsi`, `ta.atr`, `ta.adx`
- `ta.macd`, `ta.bbands`, `ta.stoch`
- `ta.crossover`, `ta.crossunder`
- `ta.highest`, `ta.lowest`, `ta.change`
- `ta.tr` (true range)
- `math.abs`, `math.max`, `math.min`, `math.round`, `math.log`
- `strategy.entry`, `strategy.exit`, `strategy.close`, `strategy.close_all`
- `strategy.position_size`, `strategy.position_avg_price`
- `input.int`, `input.float`, `input.bool`, `input.string`, `input.source`

#### Tier 2 (Important)
- `ta.wma`, `ta.vwma`, `ta.hma`, `ta.rma`
- `ta.cci`, `ta.mfi`, `ta.obv`, `ta.psar`
- `ta.pivothigh`, `ta.pivotlow`
- `ta.valuewhen`, `ta.barssince`
- `request.security` (multi-timeframe)
- `alert`, `alertcondition`
- `str.*` functions
- `array.*` basic operations

#### Tier 3 (Nice to Have)
- `ta.dmi`, `ta.supertrend`
- `matrix.*`, `map.*`
- `table.*` (display only)
- `line.*`, `label.*`, `box.*` (charting)
- Libraries import

### 1.4 Execution Model
- Bar-by-bar processing (matching TradingView)
- Series propagation: every variable maintains history
- Strategy execution:
  - Orders execute on NEXT bar's open (default)
  - `process_orders_on_close=true` → execute on current bar close
  - Commission and slippage modeling
  - Pyramiding rules
  - `calc_on_every_tick` support

### 1.5 Validation
- For each ta.* function, unit test against TradingView's output
- Full strategy backtest comparison: same Pine script → compare trade list with TV

## Phase 2: Transpiler (Pine → QuantForge Python)

### 2.1 Mapping Rules
- `strategy.entry("Long", strategy.long)` → `self.create_order(side=OrderSide.BUY, ...)`
- `strategy.close("Long")` → close position logic
- `strategy.exit(...)` with TP/SL → mapped to exit orders
- `ta.*` functions → pandas-ta or custom indicator implementations
- `input.*` → strategy constructor parameters
- `close/open/high/low/volume` → kline data columns
- `close[n]` → deque/array lookback
- `var x = 0` → `self.x = 0` (persistent across bars)
- `request.security()` → multi-timeframe subscription

### 2.2 Output Format
- Generates a complete QuantForge Strategy subclass
- Readable, editable Python code
- Includes comments mapping back to original Pine lines
- Generates both backtest config and live config

## Phase 3: Advanced Features
- `request.security()` full support
- Alert → webhook bridge
- Complete ta.* library
- Pine Script debugger
- Strategy optimization (grid search on Pine inputs)

## Directory Structure
```
quantforge/pine/
├── __init__.py
├── parser/
│   ├── __init__.py
│   ├── grammar.py      # Lark grammar definition
│   ├── ast_nodes.py    # AST node types
│   └── parser.py       # Pine → AST
├── interpreter/
│   ├── __init__.py
│   ├── runtime.py      # Bar-by-bar execution engine
│   ├── series.py       # Series type implementation
│   ├── builtins/
│   │   ├── __init__.py
│   │   ├── ta.py       # ta.* functions
│   │   ├── math_fn.py  # math.* functions
│   │   ├── strategy.py # strategy.* functions
│   │   └── input_fn.py # input.* functions
│   └── context.py      # Execution context
├── transpiler/
│   ├── __init__.py
│   ├── codegen.py      # AST → Python code generator
│   └── templates/      # Code templates
└── tests/
    ├── __init__.py
    ├── test_parser.py
    ├── test_interpreter.py
    ├── test_builtins.py
    └── fixtures/        # .pine test files
```
