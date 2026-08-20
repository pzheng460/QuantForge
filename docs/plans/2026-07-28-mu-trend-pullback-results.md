# MU Trend-Pullback Backtest Results

## Verdict

`rejected_unvalidated`

The frozen trend-pullback rules controlled drawdown but failed to retain enough
MU upside. They should not be used as a primary buy/sell system.

## Data and assumptions

- Schwab split-adjusted daily OHLCV, 2006-07-27 through 2026-07-27.
- Signals at the close and execution at the following open.
- 1 bp commission plus 2 bp slippage per traded notional.
- Cash earns zero; distributions and taxes are excluded.
- Initial capital: $100,000.

## Results

| Period | Strategy CAGR | Strategy max DD | MU buy/hold CAGR | MU buy/hold max DD |
|---|---:|---:|---:|---:|
| 2006-11-22 to 2026-07-27 | 2.56% | -14.14% | 23.24% | -88.74% |
| 2019-01-02 to 2026-07-27 | 3.59% | -13.30% | 56.10% | -57.82% |

The full-period strategy had 53 trades, a 35.85% win rate, 15.1 average
holding days, and only 16.2% time in market. The later-period test had 19
trades, a 31.58% win rate, and 18.7% time in market.

## Parameter neighborhood

Twenty-seven predeclared combinations varied trend threshold, volatility
target and both ATR stops by plus or minus 20% in the 2019+ period:

- CAGR range: -1.89% to 5.59%.
- Median CAGR: 1.40%.
- Worst maximum drawdown: -15.83%.
- 16 of 27 variants had positive CAGR.
- 0 of 27 variants beat MU buy-and-hold CAGR.

## Diagnosis

The rules succeeded at limiting risk by keeping average exposure low, but the
combination of delayed confirmation, half-sized initial entries and ATR exits
cut MU trends into short holding periods. This is a structural failure rather
than evidence that one threshold needs optimization.

The result supports using trend state as a risk overlay on a strategic core
position, not as an all-in/all-out timing system for MU. Any revised model must
be specified before a new holdout period is examined.
