# Baseline TiMi-loop optimizer — n=43 trials snapshot

**File:** `baseline_n43.csv` (43 successful trials out of 45 attempted; 2 timed out).
**Date generated:** 2026-05-06.
**Cost:** $27.65 in Claude API spend (`claude-sonnet-4-6` via Claude Code CLI).
**Method:** baseline (snapshot of `.claude/skills/quantforge-optimizer/SKILL.md` at the time of the run, copied to `eval/optimizer_ab/methods/baseline/SKILL.md`).
**Air-gap invariants enforced:** training-window-only prompt; SKILL.md sanitized; OOS metrics computed on filtered equity_curve; per-trial knowledge log wiped.

## Test matrix

5 strategies × 3 regimes × 3 seeds = 45 attempted cells.

Strategies (test tier of `eval/optimizer_ab/test_set.yaml`):
- `momentum_adx`, `sma_trend`, `macd_trend`, `rsi_momentum`, `dual_regime`

Regimes (BTC/USDT 1h on Bitget):
- `trend_2024h1` — train 2024-01-01..2024-06-30, holdout 2024-07-01..2024-09-30
- `range_2024h2` — train 2024-08-01..2024-12-31, holdout 2025-01-01..2025-03-31
- `vol_2025h1` — train 2025-01-01..2025-06-30, holdout 2025-07-01..2025-09-30

## Headline findings

| Statistic | Value |
|---|---|
| OOS Sharpe median | **−0.88** |
| OOS Sharpe mean | −0.23 |
| OOS Sharpe std | 3.52 |
| OOS Sharpe range | [−5.89, +11.10] |
| % trials with negative OOS Sharpe | **56 %** (24/43) |
| % trials with positive OOS Sharpe | 42 % (18/43) |
| Median IS−OOS gap (overfit index) | **+4.09 Sharpe** |

## Per-cell breakdown

```
strategy       regime          n  IS med  OOS med        OOS range   cost
------------------------------------------------------------------------------
dual_regime    range_2024h2    3   +4.55    -4.28      [-4.9,-2.7]   2.80
dual_regime    trend_2024h1    3   +1.55    -0.38      [-2.4,+0.4]   3.25
dual_regime    vol_2025h1      3   +2.27    +2.80     [+0.0,+11.1]   5.72
macd_trend     range_2024h2    3   +5.11    +1.31             +1.3   0.44
macd_trend     trend_2024h1    3   +2.21    -2.83             -2.8   0.44
macd_trend     vol_2025h1      3   +3.48    +3.10      [-0.9,+5.3]   4.33
momentum_adx   range_2024h2    3   +3.58    +1.80             +1.8   0.45
momentum_adx   trend_2024h1    3   +2.26    -1.96             -2.0   0.48
momentum_adx   vol_2025h1      3   +2.63    +5.64             +5.6   0.45
rsi_momentum   range_2024h2    3   +5.14    -3.29      [-3.3,-3.2]   0.79
rsi_momentum   trend_2024h1    3   +3.97    +3.16      [+0.2,+3.3]   0.83
rsi_momentum   vol_2025h1      1   +0.09    -2.98             -3.0   1.38
sma_trend      range_2024h2    3   +4.69    -1.67      [-1.7,-1.6]   1.44
sma_trend      trend_2024h1    3   +4.44    -0.55      [-3.6,+0.4]   1.43
sma_trend      vol_2025h1      3   +2.10    -5.12      [-5.9,-3.0]   3.42
```

## Key paper-quality observations

### 1. Effort is inversely correlated with OOS quality
Splitting trials by Claude API spend:

| Cost bucket | n | OOS Sharpe median |
|---|---|---|
| < $0.50 (agent stops at first Gate-1 pass) | 26 | **+0.25** |
| > $1.00 (agent iterates aggressively) | 11 | **−0.38** |

The agent's effort signals overfitting risk. Conventional optimization wisdom would expect effort to improve OOS; the data shows the opposite. This is consistent with the Bailey/López de Prado analysis of selection bias, but quantified at the LLM-trial level.

### 2. The TiMi loop produces a coin-flip OOS distribution
56 % of trials have negative OOS Sharpe, 42 % positive. The median is below zero. Mean is statistically indistinguishable from zero (mean −0.23 ± 3.52). **There is no detectable OOS edge from running this optimizer at this budget.**

### 3. LLM trajectory variance is large
Several (strategy, regime) cells show the agent picking very different optimization paths across seeds:

| Cell | OOS Sharpe range | Spread |
|---|---|---|
| dual_regime / vol_2025h1 | [+0.00, +11.10] | **11.10** |
| macd_trend / vol_2025h1 | [−0.88, +5.34] | 6.22 |
| sma_trend / trend_2024h1 | [−3.59, +0.35] | 3.94 |
| dual_regime / trend_2024h1 | [−2.40, +0.37] | 2.77 |

Same prompt, same model (`claude-sonnet-4-6`), same training window — yet OOS Sharpe spreads up to 11 across three reps. Reproducibility is not achievable at single-trial granularity; only median + bootstrap CI across seeds is meaningful.

Other cells show 0 LLM variance, but these are also the cells where the agent immediately accepts the baseline strategy after one backtest (Gate 1 passes, agent exits). For those cells "TiMi optimization" is a no-op and the OOS Sharpe is purely a property of the original strategy on the holdout window.

### 4. Regime explains more variance than strategy
The same strategy can produce wildly different OOS Sharpe across regimes:

| Strategy | OOS Sharpe across 3 regimes (median per regime) |
|---|---|
| momentum_adx | +1.80, −1.96, +5.64 → **range 7.60** |
| macd_trend | +1.31, −2.83, +3.10 → range 5.93 |
| sma_trend | −1.67, −0.55, −5.12 → range 4.57 |
| rsi_momentum | −3.29, +3.16, −2.98 → range 6.45 |
| dual_regime | −4.28, −0.38, +2.80 → range 7.08 |

Regime variance dominates. Any "improved optimizer" must be benchmarked across multiple regimes; single-period claims would be cherry-picked.

### 5. Lazy-trial detection — paper observation
After implementing `count_real_backtests` we found that on `momentum_adx` cells the agent ran exactly 1 backtest and accepted the baseline, so all three seeds produced identical metrics. This is the SKILL.md's actual stop rule (Gate 1 = PF > 1.2 ∧ MaxDD < 15 % ∧ trades ≥ 30) firing on already-passable strategies. The result: **for 5/15 cells, the optimizer added no value beyond running a single backtest.** Lazy trials are tagged in the CSV (`lazy_warning=True`) and filtered by `analyze.py` by default.

### 6. Anti-fabrication contract is required
The smoke-test history shows that without an explicit anti-fabrication contract in the prompt, sonnet-4-6 hallucinated metrics (claimed "Gate 1 passed: PF=1.33, MaxDD=11.15 %, 83 trades" without ever calling Bash to run a backtest — true PF was 1.83 with 63 trades). The harness's tool-call audit (`n_backtests` field) detects this; rejecting trials with `n_backtests==0` is the minimum defense.

## Failed trials

Two trials timed out (30 min limit per trial), both `rsi_momentum / vol_2025h1` (seed=1, seed=3):

```
[runner:FAIL] baseline__rsi_momentum__vol_2025h1__s1__5d3f83 rc=124 cost=$0.00
[runner:FAIL] baseline__rsi_momentum__vol_2025h1__s3__055ea5 rc=124 cost=$0.00
```

The successful seed=2 in the same cell ran 1.38 USD of compute, suggesting the failed seeds entered an unbounded reasoning loop. Worth investigating but not a framework bug.

## Reproducing

```bash
# Stage the baseline method
cp .claude/skills/quantforge-optimizer/SKILL.md \
   eval/optimizer_ab/methods/baseline/SKILL.md

# Re-run (resume-safe; ~$28 + ~3 hours wallclock)
uv run python -m eval.optimizer_ab.orchestrate \
    --tier test --methods baseline --seeds 1,2,3

# Re-analyze
uv run python -m eval.optimizer_ab.rebuild_csv
uv run python -m eval.optimizer_ab.analyze \
    --csv eval/optimizer_ab/results/matrix.csv \
    --baseline baseline --treatment baseline \
    --metric oos_sharpe
```

The exact baseline SKILL.md used is checked in at `eval/optimizer_ab/methods/baseline/SKILL.md`. Re-runs against the *current* canonical skill at `.claude/skills/quantforge-optimizer/SKILL.md` may differ if that file has been modified since this snapshot.
