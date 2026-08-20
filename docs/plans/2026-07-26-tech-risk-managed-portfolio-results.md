# Technology Risk-Managed Portfolio: First-Pass Results

## Status

`default_unvalidated`

This is an approximate historical simulation, not evidence of a stable or
repeatable return. The rules were selected before this run, but the selected
universe contains current winners and therefore has survivorship and selection
bias.

## Data and execution assumptions

- Source: Schwab daily OHLCV price history.
- Common evaluation range: 2011-07-01 through 2026-07-24.
- TSLA and NVDA prices were continuous across their known split dates.
- Orders use the following trading day's open.
- Round-trip model is applied by turnover: 1 bp commission plus 2 bp slippage
  per traded notional.
- Cash earns 0%; dividends and taxes are excluded.
- Initial capital: $500,000.

## Approved 60/25/5/5 portfolio

| Volatility target | CAGR | Realized vol | Max drawdown | Sharpe | Average cash |
|---|---:|---:|---:|---:|---:|
| 10% | 9.81% | 9.74% | -17.17% | 1.01 | 56.6% |
| 12% | 11.04% | 10.81% | -17.72% | 1.03 | 52.4% |

The 12% version incurred approximately $14,654 of modeled costs and two
20-trading-day hard-stop cooldowns. Its modeled annual turnover was about
6.49 times initial capital.

## Regime sensitivity for the 12% version

| Evaluation period | CAGR | Realized vol | Max drawdown | Sharpe |
|---|---:|---:|---:|---:|
| 2011-07-01 to 2015-12-31 | 3.66% | 8.90% | -13.64% | 0.45 |
| 2016-01-04 to 2020-12-31 | 14.58% | 11.20% | -17.72% | 1.27 |
| 2021-01-04 to 2026-07-24 | 14.62% | 11.80% | -8.15% | 1.22 |

The weak first regime is important: the full-period average conceals long
periods below the desired return.

## Concentration sensitivity

Over the full period:

| Portfolio | Vol target | CAGR | Max drawdown |
|---|---:|---:|---:|
| Approved XLK/SMH/TSLA/NVDA | 10% | 9.81% | -17.17% |
| No TSLA, retain 5% NVDA | 10% | 9.45% | -17.49% |
| ETF only | 10% | 8.55% | -17.49% |
| ETF only | 12% | 8.48% | -22.14% |

This sensitivity shows that raising the volatility target does not reliably
raise return, and that the 10% target is the more defensible first candidate
under a 15%–20% drawdown constraint.

## Interpretation

The first pass is promising enough for further testing, but not ready for
production. It does not establish "stable income": returns are capital gains,
not contractual income, and vary materially by regime. Before promotion, test
total-return data, cash yield, alternative start dates, rolling walk-forward
windows, parameter neighborhoods, and a broader point-in-time universe.
