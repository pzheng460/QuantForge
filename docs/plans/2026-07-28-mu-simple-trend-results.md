# MU Simple Trend Overlay Results

## Selected candidate

`candidate_unvalidated`

Use a 60% strategic MU core and a 40% tactical sleeve controlled by price
relative to MA150:

- Increase total target exposure to 100% after a close above 102% of MA150.
- Reduce total target exposure to 60% after a close below 98% of MA150.
- Make no change inside the band.
- Execute at the following open.

This is an exposure overlay, not a prediction that attempts to identify exact
tops or bottoms.

## Why this candidate

The test compared pure price/MA timing, core/satellite timing and MA
crossovers. The MA150 core/satellite neighborhood was the most consistent
compromise between retaining MU upside and reducing severe historical losses.
It did not depend on a single exact core allocation or hysteresis setting.

## Main results

| Period | Rule | CAGR | Max DD | Sharpe |
|---|---|---:|---:|---:|
| 2007-03-02 to 2026-07-27 | 60% core + MA150 sleeve | 29.69% | -67.61% | 0.83 |
| Same period | MU buy/hold | 25.04% | -87.91% | 0.66 |
| 2019-01-02 to 2026-07-27 | 60% core + MA150 sleeve | 48.33% | -53.13% | 1.09 |
| Same period | MU buy/hold | 56.10% | -57.82% | 1.09 |

The later-period rule retained 86.2% of buy-and-hold CAGR. It made 32
allocations at the standard three-basis-point cost assumption.

## Non-overlapping regimes

| Period | Rule CAGR | MU CAGR | Rule max DD | MU max DD |
|---|---:|---:|---:|---:|
| 2007–2012 | -2.97% | -10.09% | -67.61% | -87.91% |
| 2013–2018 | 44.61% | 29.95% | -49.30% | -73.80% |
| 2019–2022 | 6.66% | 12.71% | -44.09% | -49.79% |
| 2023–2026 | 114.90% | 124.43% | -53.13% | -57.82% |

The overlay cannot make a volatile single stock low risk. Its benefit is
relative: it reduced damage in adverse regimes while retaining most upside in
strong regimes.

## Robustness

- MA150 core allocations of 60%, 70% and 80% all remained profitable in the
  later period and retained 86%–92% of buy-and-hold CAGR.
- Hysteresis bands from 0% through 4% produced later-period CAGRs of
  46.90%–50.32% for the 60% core rule, excluding the zero-band result of
  48.45%; wider bands reduced trades.
- Raising total trading friction from 3 bps to 30 bps did not invalidate the
  related core/satellite family.
- MA250 was materially weaker, so the evidence does not support treating any
  arbitrary long moving average as equivalent.

## Current MU state

On 2026-07-27:

- Close: $900.20.
- MA150: $599.22.
- Upper risk-on threshold: $611.21.
- Lower risk-off threshold: $587.24.

The selected long-horizon rule is risk-on. If the assigned MU exposure is
already full, its action is hold rather than add merely because of a recent
decline.

## Focus window: latest two years

For 2024-07-29 through 2026-07-27, the simplest MA50/MA250 crossover produced
the strongest risk-adjusted result among the predeclared candidates:

| Rule | CAGR | Total return | Max DD | Sharpe | Allocations |
|---|---:|---:|---:|---:|---:|
| MA50/MA250 crossover | 180.84% | 663.87% | -30.31% | 2.04 | 3 |
| 60% core + MA150 sleeve | 178.59% | 659.31% | -35.76% | 1.97 | 8 |
| MU buy/hold | 186.40% | 714.37% | -42.93% | 1.87 | 1 |

The crossover was risk-on at the initial 2024-07-29 allocation, switched to
cash on 2024-09-26 using the prior close, and switched back to risk-on on
2025-07-02. On 2026-07-27, MA50 was $957.32 and MA250 was $432.07, so the
current signal remained risk-on.

This two-year ranking is useful for the requested recent-market focus but is
not enough to replace the longer-history candidate automatically. The
MA50/MA250 rule had materially larger drawdowns over the full history.

## Limitations

This candidate was selected after comparing a small predeclared family, so the
2019+ period is evidence of robustness but no longer a pristine final holdout.
Cash earns zero, dividends and taxes are excluded, and the result is based on
one unusually volatile and successful stock. It requires paper trading or a
future unseen period before promotion to `backtest_validated`.
