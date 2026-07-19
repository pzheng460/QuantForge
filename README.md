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

## CLI

QuantForge exposes the project workflows through one entry point:

```bash
uv run quantforge-cli --help
```

Common workflows:

```bash
# Pine strategies
uv run quantforge-cli strategies list
uv run quantforge-cli backtest ema_crossover --symbol BTC/USDT:USDT --exchange bitget
uv run quantforge-cli optimize ema_crossover --metric sharpe --top 10
uv run quantforge-cli live ema_crossover --demo --dry-run

# Web stack and API
uv run quantforge-cli web start
uv run quantforge-cli api get /health
uv run quantforge-cli api post /backtest/run --json-file request.json

# Live engines, examples, DSL, and evaluation harnesses
uv run quantforge-cli engines list
uv run quantforge-cli examples list bybit
uv run quantforge-cli dsl --list
uv run quantforge-cli eval optimizer-ab orchestrate --tier dev --methods baseline
```

Server-backed commands use `QF_API_URL`, defaulting to `http://127.0.0.1:8000/api`.
Agent workflows support both Claude Code and Codex CLI. By default, Codex
uses the model configured for the logged-in Codex account; pass `--model`
only when that account supports the requested model.

```bash
uv run quantforge-cli agent run --provider codex --skill quantforge-optimizer --strategy ema_crossover
uv run quantforge-cli eval optimizer-ab orchestrate --tier dev --methods baseline --agent-provider codex
uv run quantforge-cli eval optimizer-ab orchestrate --tier dev --methods baseline \
  --strategies ema_crossover --agent-providers claude,codex
uv run python -m eval.optimizer_ab.analyze \
  --csv eval/optimizer_ab/results/matrix.csv \
  --metric oos_sharpe --compare-providers claude,codex
uv run quantforge-cli eval optimizer-ab cross-review \
  --csv eval/optimizer_ab/results/matrix.csv \
  --providers claude,codex \
  --summary-csv eval/optimizer_ab/results/cross_reviews/factors.csv
uv run quantforge-cli eval optimizer-ab orchestrate \
  --tier dev --methods baseline,cross_review_guided \
  --strategies ema_crossover --agent-providers claude,codex
uv run quantforge-cli eval auto-tune run \
  --pine quantforge/pine/strategies/ema_crossover.pine \
  --strategy ema_crossover \
  --symbol BTC/USDT:USDT --timeframe 1h \
  --windows current:2024-07-01:2024-12-31,stress:2024-08-01:2024-09-30 \
  --news-file events.jsonl
uv run quantforge-cli auto-tune run-once \
  --pine quantforge/pine/strategies/ema_crossover.pine \
  --strategy ema_crossover \
  --windows current:2024-07-01:2024-12-31,stress:2024-08-01:2024-09-30 \
  --news-file events.jsonl
uv run quantforge-cli auto-tune run-once \
  --pine quantforge/pine/strategies/ema_crossover.pine \
  --strategy ema_crossover \
  --windows current:2024-07-01:2024-12-31,stress:2024-08-01:2024-09-30 \
  --news-file events.jsonl \
  --execute \
  --auto-deploy \
  --optimizer-results-csv eval/optimizer_ab/results/auto_tune.csv \
  --optimizer-trials-dir eval/optimizer_ab/results/auto_tune_trials \
  --promotion-report eval/optimizer_ab/results/promotion_pipeline.json \
  --shadow-report eval/optimizer_ab/results/shadow_compare.json
uv run quantforge-cli news collect raw_events.jsonl --out events.jsonl
uv run quantforge-cli news rss \
  https://www.coindesk.com/arc/outboundfeeds/rss/ \
  --symbols BTC/USDT:USDT \
  --out eval/optimizer_ab/results/news_events.jsonl
uv run quantforge-cli news exchange-status \
  https://status.example.com/api/v2/incidents/unresolved.json \
  --exchange bitget \
  --symbols BTC/USDT:USDT \
  --out eval/optimizer_ab/results/exchange_events.jsonl
uv run quantforge-cli news microstructure market_microstructure.json \
  --source-name bitget \
  --out eval/optimizer_ab/results/microstructure_events.jsonl
uv run quantforge-cli auto-tune daemon \
  --job-file eval/optimizer_ab/results/auto_tune_jobs.json \
  --interval-sec 3600
# auto-tune fuses news/status/funding/OI/liquidation events into news_risk.components
# Writes QuantForge-owned scheduler artifacts:
# eval/optimizer_ab/results/auto_tune_jobs_state.json
# eval/optimizer_ab/results/auto_tune_runs/*.jsonl
# eval/optimizer_ab/results/auto_tune_failed/*.json
uv run quantforge-cli control apply-report \
  --strategy-id ema_crossover \
  --report eval/optimizer_ab/results/auto_tune_report.json
uv run quantforge-cli deployment register \
  --strategy-id ema_crossover \
  --pine eval/optimizer_ab/results/auto_tune_trials/best/optimized.pine \
  --evidence eval/optimizer_ab/results/auto_tune_report.json \
  --source auto_tune
uv run quantforge-cli deployment transition <version-id> paper
uv run quantforge-cli deployment transition <version-id> shadow
uv run quantforge-cli deployment shadow-compare ema_crossover \
  --start 2024-07-01 --end 2024-12-31 \
  --out eval/optimizer_ab/results/shadow_compare.json
uv run quantforge-cli deployment promote <version-id>
uv run quantforge-cli deployment auto-promote ema_crossover \
  --pine eval/optimizer_ab/results/auto_tune_trials/best/optimized.pine \
  --evidence eval/optimizer_ab/results/auto_tune_report.json \
  --start 2024-07-01 --end 2024-12-31 \
  --shadow-report eval/optimizer_ab/results/shadow_compare.json \
  --ledger ~/.quantforge/paper_ledger.sqlite \
  --min-runtime-fills 2 \
  --min-runtime-pnl-delta 0 \
  --max-runtime-drawdown-delta 0.02 \
  --out eval/optimizer_ab/results/promotion_pipeline.json
uv run quantforge-cli deployment live-command ema_crossover --mode paper
uv run quantforge-cli deployment approval request live_command --strategy-id ema_crossover
uv run quantforge-cli deployment approval approve <approval-id> --approver <name>
uv run quantforge-cli deployment live-command ema_crossover \
  --mode live \
  --approval-id <approval-id> \
  --approvals ~/.quantforge/approvals.json \
  --policy live_policy.yaml \
  --request live_request.json

# Paper/shadow execution ledger
uv run quantforge-cli paper signal ema_crossover \
  --role shadow --side buy --price 100 --quantity 2 \
  --version-id <candidate-version-id> \
  --fee-rate 0.001 --slippage-bps 10
uv run quantforge-cli paper summary ema_crossover --role shadow
uv run quantforge-cli paper shadow-run ema_crossover \
  --events eval/optimizer_ab/results/runtime_signals.jsonl \
  --registry ~/.quantforge/deployments.json \
  --ledger ~/.quantforge/paper_ledger.sqlite \
  --out eval/optimizer_ab/results/shadow_observation.json
uv run quantforge-cli risk check ema_crossover \
  --role promoted \
  --max-drawdown 0.05 \
  --max-daily-loss 500 \
  --auto-rollback \
  --registry ~/.quantforge/deployments.json \
  --control-state eval/optimizer_ab/results/trading_control.json \
  --out eval/optimizer_ab/results/risk_report.json
uv run quantforge-cli risk execution live_orders.jsonl \
  --strategy-id ema_crossover \
  --control-state eval/optimizer_ab/results/trading_control.json \
  --max-slippage-bps 50 \
  --max-latency-ms 1000 \
  --max-spread-bps 20 \
  --out eval/optimizer_ab/results/execution_risk.json
uv run quantforge-cli risk live-policy live_policy.yaml live_request.json \
  --approvals ~/.quantforge/approvals.json \
  --out eval/optimizer_ab/results/live_policy_report.json
uv run quantforge-cli audit build ema_crossover \
  --auto-tune eval/optimizer_ab/results/auto_tune_report.json \
  --promotion eval/optimizer_ab/results/promotion_pipeline.json \
  --shadow eval/optimizer_ab/results/shadow_compare.json \
  --risk eval/optimizer_ab/results/risk_report.json \
  --json-out eval/optimizer_ab/results/audit_report.json \
  --markdown-out eval/optimizer_ab/results/audit_report.md
```

## Architecture

```
┌──────────────┐     ┌────────────────────┐     ┌──────────────────┐
│  .pine file   │────▶│  Pine interpreter   │────▶│  OrderBridge     │
│  (strategy)   │     │  (backtest & live)  │     │  strategy.entry/ │
└──────────────┘     └────────────────────┘     │  close/exit      │
                                                  └────────┬─────────┘
┌──────────────┐     ┌────────────────────┐              │
│  Exchange     │◀───│  CcxtConnector      │◀─────────────┘
│  (via ccxt)   │     │  (orders, klines,  │   create_order()
└──────────────┘     │   positions)       │
                      └────────────────────┘
```

The same Pine interpreter runs backtests and live trading — no
transpilation between modes. Exchange connectivity goes through ccxt's
unified API; there is no hand-written per-exchange connector code.

## Project Structure

```
quantforge/
├── pine/            # Pine Script v5 parser, interpreter, transpiler,
│                    #   optimizer, live engine (primary layer)
├── dsl/             # Declarative Python strategy DSL (prototyping)
├── indicators/      # Streaming indicators (EMA, RSI, ATR, ADX, BB, ...)
├── backtest/        # Data cache, validation, Monte Carlo simulation
├── cli/             # quantforge-cli command group
└── *.py             # Evolving-mode subsystem (auto-tune, deployment,
                     #   risk control, paper/shadow ledger, alerts)

apps/dashboard/      # FastAPI backend + React/Vite frontend
eval/optimizer_ab/   # TiMi optimizer A/B evaluation harness
```

## Attribution

QuantForge was originally forked from [NexusTrader](https://github.com/RiverTrading/NexusTrader) by RiverTrading / Quantweb3. We gratefully acknowledge the foundational work of the NexusTrader project and its contributors.

## License

MIT — see [LICENSE](./LICENSE) for details.
