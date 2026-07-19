# Charles Schwab Broker Integration Design

## Goal

Add Charles Schwab as a first-class live broker without coupling its OAuth,
account, equity-order, and market-data semantics to the existing CCXT crypto
connector. The first release supports US stocks and ETFs, including account and
position reads, live and historical market data, market/limit/stop orders,
short-sell/buy-to-cover, cancellation, and order-status reconciliation.

Options, fractional shares, extended-hours orders, bracket orders, and
multi-leg orders are out of scope.

## Architecture

Introduce a broker protocol used by the live engine and order bridge:

```text
PineLiveEngine
    -> BrokerConnector
        -> CcxtConnector
        -> SchwabConnector
            -> OAuthClient
            -> AccountsClient
            -> MarketDataClient
            -> OrdersClient
```

The protocol exposes quote/bar access, account and position reads, order
placement/cancellation/status, symbol normalization, and capability validation.
The existing CCXT behavior remains available through its adapter.

The Schwab implementation uses the official HTTP APIs directly rather than an
unofficial SDK, keeping authentication, error classification, and order
idempotency under QuantForge's control.

## Authentication and Token Storage

The Dashboard starts OAuth through
`/api/brokers/schwab/auth/start` and receives the callback through
`/api/brokers/schwab/auth/callback`. The flow validates a random state value and
uses the application credentials configured outside the repository.

Tokens are stored under `~/.quantforge/schwab/tokens.json` with user-only file
permissions. Tokens must never appear in logs, API responses, or exception
messages. Requests refresh expiring access tokens automatically. A refresh
failure transitions the connection to `AUTH_REQUIRED` and blocks all new
orders.

Only Schwab account hashes cross the backend boundary; raw account numbers are
not returned to the frontend or written to logs.

## Trading Flow and Safety

```text
Pine signal
  -> normalized order intent
  -> QuantForge risk checks and live confirmation
  -> account/capability/instrument checks
  -> Schwab order submission
  -> persist broker order id
  -> reconcile broker order status
  -> update fills and positions
```

Schwab position size is expressed as a USD budget at the CLI boundary and is
converted to a whole-share quantity by flooring against the latest validated
price. Demo mode remains local paper trading because the production Trader API
does not provide an exchange-style sandbox. Real submission requires valid
OAuth, an explicitly selected account, successful risk checks, `--no-demo`, and
`--confirm-live`.

The connector maps broker statuses into `pending`, `accepted`,
`partially_filled`, `filled`, `canceled`, `rejected`, or `unknown`. Rate limits,
network failures, and server errors may receive bounded backoff. Authentication
errors, order rejection, market-hours restrictions, and account permission
errors are not retried as transient failures.

An order submission that times out is reconciled before any retry. If its final
state cannot be determined, it becomes `unknown`, the strategy stops opening
new positions, and the operator must verify the account before resuming.

## Dashboard and Configuration

The Dashboard adds Schwab connection status, connect/reconnect controls, and an
account selector. Application key, secret, callback URL, selected account hash,
and token path are configuration values; secrets and tokens are excluded from
source control. User-facing documentation covers developer application setup,
callback configuration, token lifecycle, paper mode, and the explicit real-money
enablement sequence.

## Testing and Acceptance

One critical contract test is written first and must fail before implementation.
It observes the full path from a Pine order intent through a mocked official
Schwab HTTP boundary, including submission and status mapping.

Focused tests cover:

- OAuth state validation, token persistence permissions, refresh, and redaction;
- account-hash selection and capability checks;
- quote/bar normalization and USD-budget-to-whole-share conversion;
- market, limit, stop, short-sell, and buy-to-cover payloads;
- cancellation and all normalized order states;
- retry classification, ambiguous submission handling, and order blocking;
- continued behavior of the existing CCXT connector;
- Dashboard connection and account-selection API behavior.

Automated tests never place a real order. Manual acceptance uses a real Schwab
developer application to complete OAuth and read accounts, positions, quotes,
and history. A real-money smoke order is separate, deliberately opt-in, and must
use the smallest operator-approved size.

## Stable Baseline

The pre-integration baseline is commit
`795ba2f5c0b54eec0ef8fa7249daf2ec91166655`, recorded by the annotated tag
`v0.2.36-stable`.
