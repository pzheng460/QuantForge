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

