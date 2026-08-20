# MU Trend-Pullback Strategy Design

## Purpose

Validate a long-only daily strategy for Micron (MU) that uses trend state as a
filter, buys confirmed trend starts or pullbacks, and scales exposure by
realized volatility. It is intended to support daily advice, not automatic
order submission.

## Frozen rules

- Use split-adjusted daily OHLCV bars.
- Compute MA20, MA60, ATR20 and 20-day realized volatility.
- Normalize trend distance as `(MA20 - MA60) / ATR20`.
- Uptrend requires distance above 0.5, a positive 20-day MA60 slope, and close
  above MA60 for three consecutive sessions.
- Downtrend uses the symmetric conditions below -0.5. Other observations are
  range-bound.
- On a newly confirmed uptrend, enter half of the volatility-scaled target.
- Add to the full target after price pulls back within 0.5 ATR of MA20, remains
  above MA60, and closes above the prior close.
- Target exposure is `min(100%, 15% / annualized_volatility_20d)`.
- Exit at the next open after two closes below MA60, a 3 ATR trailing stop, a
  2 ATR initial stop, or a 12% strategy drawdown.
- Remain long-only; a downtrend means cash rather than short exposure.

## Backtest controls

- Signals use the completed close and trade at the next open.
- Charge 1 bp commission and 2 bp slippage on traded notional.
- Cash earns zero and dividends are excluded.
- Compare against MU price buy-and-hold over the identical evaluation range.
- Report full available history, a later-period holdout, trades, win rate,
  exposure, turnover, CAGR, volatility, Sharpe and maximum drawdown.
- Run a predeclared parameter neighborhood around trend distance, volatility
  target and stop distances. Do not optimize parameters after seeing results.
- Label the result `default_unvalidated`; the historical earnings filter is
  excluded until a point-in-time event database is available.
