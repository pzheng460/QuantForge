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

QuantForge is a Python platform for building, backtesting, optimizing,
and live-trading crypto strategies written in **TradingView Pine Script
v5**. The same interpreter runs backtest and live — what you validated
is exactly what trades. Exchange connectivity goes through ccxt's
unified API.

**Key capabilities:**
- 📜 **Pine Script engine** — parser + interpreter compatible with
  TradingView semantics; strategies are plain `.pine` files
- 📊 **Backtest & optimize** — grid search, walk-forward, three-stage
  validation, with progress streaming to the web UI
- 🖥️ **Web dashboard** — FastAPI + React (lightweight-charts) for live
  trading, backtesting, and AI-assisted strategy optimization
- 🤖 **Evolving Mode** — optional closed-loop auto-tune → paper/shadow
  compare → risk-gated deploy pipeline
- 🔌 **Multi-exchange via ccxt** — Bitget, Binance, OKX, Bybit,
  Hyperliquid; no hand-written per-exchange code

## Supported Exchanges

| Binance | OKX | Bybit | Bitget | Hyperliquid |
|:---:|:---:|:---:|:---:|:---:|
| ✅ Spot/Futures | ✅ Spot/Futures | ✅ Linear | ✅ UTA/Futures | ✅ Perps |

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)

### Installation

```bash
git clone https://github.com/pzheng460/QuantForge.git
cd QuantForge
uv sync              # runtime deps
uv sync --group dev  # + test/lint deps
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

### First Backtest

Strategies are `.pine` files in `quantforge/pine/strategies/`. Run one
against real exchange data:

```bash
uv run quantforge-cli backtest ema_crossover \
  --symbol BTC/USDT:USDT --exchange bitget --timeframe 15m --period 6m
```

Or start the web dashboard and use the UI:

```bash
./apps/dashboard/start.sh        # vite :5173 + FastAPI :8000
```

## Prototyping with the Python DSL

For quick experiments there is a thin declarative API
(`quantforge/dsl/`) — production strategies should still land as
`.pine` files:

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

Supported indicators: `ema`, `sma`, `rsi`, `atr`, `adx`, `bb`, `roc`.

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

# Live engines, DSL, and evaluation harnesses
uv run quantforge-cli engines list
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
unified API for crypto venues. US equities use the native Charles Schwab
broker connector described below.

### Charles Schwab

Create an Individual Trader API application in the Schwab Developer Portal and
configure its callback URL to match the Dashboard backend callback. Export the
application credentials before starting QuantForge:

```bash
export SCHWAB_APP_KEY="..."
export SCHWAB_APP_SECRET="..."
export SCHWAB_CALLBACK_URL="https://127.0.0.1:8000/api/brokers/schwab/auth/callback"
```

In the Dashboard, select **Charles Schwab**, click **Connect Charles Schwab**,
complete OAuth, and choose an account. Tokens are stored with user-only
permissions in `~/.quantforge/schwab/tokens.json`; never commit this file.

Schwab supports US stocks and ETFs on `1m`, `5m`, `15m`, `30m`, `1h`, `1d`,
and `1w` bars. Market, limit, and stop orders plus long, short, close, cancel,
and order-status operations are available through the connector. Fractional
shares, options, extended-hours, bracket, and multi-leg orders are not enabled.

Schwab has no exchange-style sandbox. Dashboard/CLI demo mode is local paper
trading. Real orders require a selected account, `demo=false`, the existing
typed strategy-name confirmation in the Dashboard, or both `--no-demo` and
`--confirm-live` in the CLI. Equity leverage must remain `1`.

## Project Structure

```
quantforge/
├── brokers/         # Broker protocol and Charles Schwab integration
├── pine/            # Pine Script v5 parser, interpreter, optimizer,
│                    #   live engine (primary layer)
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
