# Technology Risk-Managed Portfolio Backtest

## Objective

Test whether a US-listed technology portfolio can target an 8%–12% long-run
return while keeping drawdown near 15%–20% by reducing exposure to cash during
unfavorable regimes.

## Universe and limits

- XLK: core technology ETF, base cap 60%.
- SMH: semiconductor satellite ETF, base cap 25%.
- TSLA: optional stock satellite, cap 5%.
- NVDA: optional stock satellite, cap 5%.
- Cash: residual allocation, minimum 5% in full risk-on conditions.

The portfolio does not use leverage, short selling or options.

## Signals and sizing

Signals use split-adjusted daily price bars. Cash earns 0% and fund
distributions are not added to returns in this first-pass test. An asset is
eligible only when:

- close is above MA200;
- MA50 is above MA200;
- its 20-day annualized volatility is below its rolling 80th percentile.

The strategy calculates signals at each monthly decision and changes target
weights on the first trading day of the month. Eligible base weights are scaled to a 12% portfolio
volatility target using the trailing 20-day covariance matrix. Gross exposure
is clipped to 30%–95%; ineligible allocations remain as cash.

## Drawdown controls

- At an 8% strategy drawdown, risk exposure is multiplied by 0.5.
- At a 12% strategy drawdown, risk assets are liquidated and a 20-trading-day
  cooldown starts.
- After cooldown, exposure resumes only at a monthly rebalance and only for
  eligible assets.

Signals use prior-close data and trades execute at the next available open.
Commission and slippage are charged on turnover.

## Evaluation

Report CAGR, total return, annualized volatility, maximum drawdown, Sharpe
ratio, turnover, time in cash and comparison with buy-and-hold XLK and a static
60/25/5/5 portfolio. Results must identify the exact available date range.

The first pass is in-sample research and is labelled `default_unvalidated`.
No parameter can be called validated until a separate rolling out-of-sample
test succeeds.
