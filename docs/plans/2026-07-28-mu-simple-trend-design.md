# MU Simple Trend Overlay Design

## Candidate family

Test three deliberately simple long-only rules without post-result tuning:

1. Price/MA regime: 100% exposure above the long moving average and cash below.
2. Core/satellite regime: 70% core exposure, increasing to 100% above the
   long moving average and returning to 70% below it.
3. MA crossover: 100% exposure when MA50 exceeds MA200 and cash otherwise.

The price/MA rules use a two-percent hysteresis band: enter above 102% of the
moving average, exit below 98%, and retain the prior state between the two
levels. Signals use the completed close and execute at the next open.

## Validation gate

- Use Schwab split-adjusted MU daily bars and 3 bps traded-notional costs.
- Compare full history and the 2019+ later-period sample with MU price
  buy-and-hold.
- A credible candidate must have positive later-period return, materially
  lower drawdown, higher Sharpe, and retain at least 60% of buy-and-hold CAGR.
- Repeat price/MA rules with MA150, MA200 and MA250.
- Repeat core/satellite rules with 60%, 70% and 80% core exposure.
- Do not select an isolated best parameter. Prefer a stable neighborhood and
  the simplest rule.
- Cash earns zero; dividends, taxes and earnings-event timing are excluded.

All results remain research-only until a genuinely unseen forward period or
paper-trading record is available.
