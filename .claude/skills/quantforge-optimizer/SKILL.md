---
name: quantforge-optimizer
platform: [openclaw, claude-code]
description: >
  QuantForge Pine strategy optimization with TiMi-style closed-loop mathematical reflection.
  LLM analyzes backtest failures → formulates mathematical constraints → solves for optimal
  parameters → modifies Pine code → re-backtests → iterates until convergence.
  Hierarchical: parameter → function → strategy (escalate only when lower level fails).
  Triggers: "optimize strategy", "improve trading performance", "analyze backtest",
  "refine parameters", "generate Pine strategy", "strategy reflection",
  "find best strategy", "backtest all strategies".
tools: Read, Write, Edit, Bash, Glob, Grep
---

# QuantForge Optimizer

**TiMi-style closed-loop** strategy optimization for QuantForge Pine scripts.

Core loop: `Backtest → Analyze Failures → Mathematical Reflection → Constraint Solving → Code Modification → Re-Backtest → Iterate`

## Project

- Path: `/home/pzheng46/QuantForge`
- Pine strategies: `quantforge/pine/strategies/*.pine`
- API keys: `.keys/.secrets.toml`
- CLI: `uv run python -m quantforge.pine.cli`
- Resolve `<skill>` to this SKILL.md's parent directory

## Quick Reference

```bash
cd /home/pzheng46/QuantForge

# Backtest (use --timeframe 1h for BTC, 15m is too noisy)
uv run python -m quantforge.pine.cli backtest quantforge/pine/strategies/my.pine \
  --symbol BTC/USDT:USDT --timeframe 1h --exchange bitget

# Backtest with date range
uv run python -m quantforge.pine.cli backtest my.pine \
  --symbol BTC/USDT:USDT --timeframe 1h --start 2025-06-01 --end 2026-01-01

# Grid search (Level 1 fallback, not primary method)
uv run python -m quantforge.pine.cli optimize my.pine \
  --symbol BTC/USDT:USDT --timeframe 1h --metric sharpe --top 5
```

### CLI Defaults & Notes
- `--timeframe` defaults to 15m but **BTC must use 1h** (15m → all negative Sharpe)
- `--exchange` defaults to bitget
- `--warmup-days` defaults to 60

## ═══ THE CLOSED LOOP ═══

This is the primary optimization workflow. Follow it step by step.

### Step 0: Validate

```bash
python <skill>/scripts/validate_pine.py <strategy.pine>
```

Fix all errors before proceeding. Warnings are advisory.

### Step 1: Baseline Backtest

```bash
uv run python -m quantforge.pine.cli backtest <strategy.pine> \
  --symbol BTC/USDT:USDT --timeframe 1h --exchange bitget \
  2>&1 | tee /tmp/baseline.txt
```

Save the full output. Record baseline metrics.

### Step 2: Analyze Failures

```bash
cat /tmp/baseline.txt | python <skill>/scripts/analyze_backtest.py --pine-file <strategy.pine>
```

The `--pine-file` flag is optional but recommended — it enables strategy type classification
(mean_reversion vs trend_following) and type-specific failure recommendations.

This produces JSON with:
- `metrics`: Return, WinRate, PF, MaxDD, R-multiple, streaks
- `trade_analysis`: Per-direction R-multiples, MAE/MFE estimates, truncation info
- `failures`: Typed failure modes with severity and constraint hints
- `recommendation`: Suggested optimization level (1/2/3)
- `strategy_classification`: Strategy type detection (when `--pine-file` provided)
- `mae_mfe_estimates`: Lower-bound adverse/favorable excursion estimates

### Step 3: Mathematical Reflection (LLM Does This)

**This is the core TiMi alignment.** The LLM (you) performs mathematical reasoning:

#### 3a. Extract Risk Scenarios from Failures

For each failure mode, identify the **root cause** and the **parameter(s)** responsible.

Example failure modes → risk scenarios:

| Failure | Risk Scenario | Relevant Parameters |
|---------|--------------|-------------------|
| WHIPSAW (9 consecutive losses) | EMA crossover fires too often in range | `fast_period`, `slow_period`, ADX threshold |
| HIGH_DD (19%) | Position too large relative to volatility | Position size, stop-loss distance |
| LOW_WIN_RATE (29%) | Entry signal too noisy, no confirmation | Entry threshold, filter parameters |
| BAD_RR (avg_loss > 2× avg_win) | Stop too wide or take-profit too tight | ATR multiplier, profit target |
| LOW_PF (PF < 1.0) | Strategy net-losing — negative expectancy | All parameters (needs fundamental fix) |
| BAD_R_MULTIPLE (R < 1.0 + WR > 45%) | Winning often but winning small | Exit threshold, profit target |
| WEAK_LONG_R / WEAK_SHORT_R | One direction significantly underperforms | Asymmetric parameters, direction filter |

#### 3b. Formulate Mathematical Constraints

Transform each risk scenario into a **quantitative constraint** on the parameters.

**Template** (from TiMi paper, Eq. 3):
```
Θ* = argmax  Σ ωi·Ji(Θ, F)
     Θ∈C(Θ)

C(Θ) = {Θ ∈ Rⁿ | A(R)·Θ ⪯ b(R)}
```

**Practical examples:**

**Whipsaw constraint** — need ADX filter to avoid ranging markets:
```
From data: ADX < 20 → win_rate = 18%, ADX > 25 → win_rate = 52%
Break-even win_rate = 1/(1 + avg_win/avg_loss)
Constraint: adx_threshold ≥ 25
```

**Position size constraint** — limit compound drawdown:
```
Let r = per-trade risk, n = max consecutive losses (observed from trades)
Compound loss: 1 - (1-r)^n ≤ MaxDD_target
r ≤ 1 - MaxDD_target^(1/n)
Example: n=9, MaxDD_target=10% → r ≤ 1.16% per trade
```

**Stop-loss constraint** — balance premature stops vs large losses:
```
From trade log adverse excursion analysis:
p(stop_at_1.0×ATR) = 0.73 (too many false stops)
p(stop_at_1.5×ATR) = 0.31 (acceptable)
Constraint: atr_multiplier ≥ 1.5
```

**Profit-taking constraint** (TiMi Case #3):
```
Average profitable movement during trends: ΔP_trend
First profit target: h1 ≥ log(1 + ΔP_trend/P_entry) / log(1 + Φ)
```

See `references/optimization-cases.md` for 4 detailed worked examples with decision tree.

#### 3c. Solve for New Parameters

Given the constraints, determine new parameter values. Methods:
1. **Analytical**: Solve the constraint equations directly
2. **Constrained grid search**: Run optimizer but only within the feasible region
3. **LLM reasoning**: Use mathematical reasoning to find Pareto-optimal point

### Step 4: Modify Pine Code

Apply the solved parameters to the Pine file. Two approaches:

**Parameter-only change (Level 1):** Edit `input.*` default values
```pine
// Before
fast_period = input.int(5, title="Fast EMA", minval=3, maxval=20)
// After (from constraint solving)
fast_period = input.int(8, title="Fast EMA", minval=3, maxval=20)
```

**Function swap (Level 2):** Add/replace indicator or filter
```pine
// Added: ADX regime filter (from whipsaw constraint)
adx_val = ta.adx(14)
trending = adx_val > 25
```

**Strategy restructure (Level 3):** Redesign entry/exit logic.
Use templates from `references/pine-patterns.md`.

### Step 5: Re-Backtest & Compare

```bash
# Backtest modified strategy
uv run python -m quantforge.pine.cli backtest <strategy_v2.pine> \
  --symbol BTC/USDT:USDT --timeframe 1h --exchange bitget \
  2>&1 | tee /tmp/after.txt

# Generate comparison report
python <skill>/scripts/generate_report.py \
  --before /tmp/baseline.txt --after /tmp/after.txt \
  --changes "Description of changes" --level 1 --strategy "Strategy Name"
```

### Step 6: Convergence Check

**If improved and passes Gate 1 → proceed to Gate 2 (holdout)**
**If improved but not enough → loop back to Step 2 with new baseline**
**If not improved → escalate optimization level (1→2→3) and loop**

**Maximum iterations**: 3 per level, 3 levels = 9 total max.
**Stop if**: 2 consecutive iterations show no improvement at same level.

```bash
# Gate 2: Automated holdout validation
python <skill>/scripts/holdout_test.py <strategy.pine> \
  --symbol BTC/USDT:USDT --timeframe 1h
```

## ═══ HIERARCHICAL OPTIMIZATION (TiMi-aligned) ═══

Minimum intervention principle — escalate ONLY when lower level fails:

### Level 1: Parameter Tuning
- Adjust `input.*` values based on constraint solving
- **Position sizing**: If MaxDD is within 5% of target, reduce `default_qty_value` first
  - Formula: `new_qty = old_qty × (MaxDD_target / MaxDD_actual)`
  - Example: MaxDD=16.88%, target=15% → qty=100×(15/16.88)=89% → use 85%
- Fallback: grid search within constrained feasible region
- **Escalate when**: All reasonable parameter values still fail Gate 1

### Level 2: Function Swap
- Replace indicator (SMA→EMA), add filter (ADX regime), change stop type (fixed→ATR)
- Common Level 2 interventions:
  - Add `ta.adx()` regime filter → fixes WHIPSAW
  - Add `strategy.exit()` with ATR stop → fixes HIGH_DD
  - Add RSI/volume confirmation → fixes LOW_WIN_RATE
- **Escalate when**: Function swaps don't fix the structural problem

### Level 3: Strategy Restructure
- Redesign entry/exit logic entirely
- Dual-regime architecture (trend + mean-reversion)
- Multi-indicator confluence
- Use templates from `references/pine-patterns.md`

## ═══ PROGRESSIVE VALIDATION ═══

### Gate 1: Historical Backtest (In-Sample)

**Must meet ALL:**
- Profit Factor > 1.2
- Max Drawdown < 15%
- Win Rate > 30%
- Total Trades ≥ 30
- No ZERO_PNL bug trades

### Gate 2: Out-of-Sample (Time-based Holdout)

```bash
# Simple mode (default): 2/3 train, 1/3 holdout
python <skill>/scripts/holdout_test.py <strategy.pine> \
  --symbol BTC/USDT:USDT --timeframe 1h

# Walk-forward mode: rolling windows (more robust)
python <skill>/scripts/holdout_test.py <strategy.pine> \
  --symbol BTC/USDT:USDT --timeframe 1h \
  --mode walkforward --window-days 60 --step-days 21
```

**Simple mode must meet ALL:**
- Holdout Profit Factor > 1.0
- Holdout MaxDD < 2× in-sample MaxDD
- Performance degradation < 50%

**Walk-forward mode must meet ALL:**
- Average test PF > 1.0 across all windows
- No window has test MaxDD > 2× its train MaxDD
- Consistency score ≥ 0.5 (at least half the windows profitable)

**If Gate 2 fails → strategy is overfit. Return to Gate 1.**

### Gate 3: Live Simulation (Demo)

```bash
tmux new -s strategy_name
uv run python -m quantforge.pine.cli live <strategy.pine> \
  --symbol BTC/USDT:USDT --timeframe 1h --exchange bitget --demo
```

Run ≥1 week. Check: no crashes, fills match signals, max single-trade loss < 3%.

**⚠️ Use tmux (not OpenClaw exec) for long-running strategies.**
**⚠️ Bitget UTA Demo leverage: set via web UI, API may not work.**

## ═══ FULL AUTO MODE ═══

Launch the entire closed-loop as a single autonomous Claude Code session:

```bash
# One-liner: auto-optimize a strategy (runs until convergence or max iterations)
bash <skill>/scripts/auto_optimize.sh <strategy.pine> [symbol] [timeframe] [max_iters]

# Examples:
bash <skill>/scripts/auto_optimize.sh quantforge/pine/strategies/ema_crossover.pine
bash <skill>/scripts/auto_optimize.sh my_strategy.pine BTC/USDT:USDT 1h 9

# Background (OpenClaw) — use timeout≥1800 for 4+ iterations:
exec background:true timeout:1800 command:"bash <skill>/scripts/auto_optimize.sh ema_crossover.pine"
```

This launches Claude Code with a comprehensive prompt that:
1. Runs the full TiMi loop autonomously (validate → backtest → analyze → reflect → modify → re-backtest)
2. Iterates up to N times with automatic level escalation (param → function → strategy)
3. Runs Gate 2 holdout when Gate 1 passes
4. Writes detailed logs + SUMMARY.md to `/tmp/quantforge-optimize-<name>-<timestamp>/`
5. Keeps an audit trail of every mathematical reflection and constraint solved

**No human intervention needed** — just check the summary when it's done.

## ═══ UTILITY SCRIPTS ═══

All in `<skill>/scripts/`. Run from `/home/pzheng46/QuantForge`.

| Script | Purpose |
|--------|---------|
| `auto_optimize.sh <file>` | **Full auto**: closed-loop until convergence |
| `validate_pine.py <file>` | Pre-flight checks + strategy classification + unused input detection |
| `analyze_backtest.py` (stdin) | Parse backtest → failures + recommendation JSON (supports `--pine-file` for classification) |
| `batch_backtest.py [--timeframe 1h]` | Tournament: backtest all .pine, ranked table |
| `holdout_test.py <file>` | Gate 2: `--mode simple` (default) or `--mode walkforward` |
| `generate_report.py --before --after` | Comparison report with Gate 1 check (supports `--previous-best`) |

## ═══ CRITICAL PATTERNS ═══

### Regime-Aware Position Management
Flatten on regime change — don't let trending positions survive into ranging:
```pine
if trending
    if ta.crossover(fast, slow)
        strategy.entry("Long", strategy.long)
    if ta.crossunder(fast, slow)
        strategy.entry("Short", strategy.short)
if not trending
    strategy.close("Long")
    strategy.close("Short")
```

### Direction Flip via strategy.entry()
`strategy.entry()` auto-closes opposite direction — no need for separate close:
```pine
if ta.crossover(fast, slow) and trending
    strategy.entry("Long", strategy.long)    // auto-closes Short
if ta.crossunder(fast, slow) and trending
    strategy.entry("Short", strategy.short)  // auto-closes Long
```

### Red Flags
- **In-sample Sharpe > 5** → almost certainly overfit
- **Grid search top results have identical params** (fast==slow) → degenerate
- **BTC on 15min** → all negative Sharpe, use 1h

## ═══ CLOSED-LOOP EXAMPLE (Full Walkthrough) ═══

```
Strategy: EMA Crossover (ema_crossover.pine)
Symbol: BTC/USDT:USDT, 1h

── Iteration 1 ──────────────────────────────────
[Backtest] Return: +10.6%, WinRate: 29.7%, PF: 1.21, MaxDD: 19.06%
[Failures] WHIPSAW (9 consecutive losses), LOW_WIN_RATE (29.7%), HIGH_DD (19%)
[Level] Recommended: Level 2 (Function Swap)

[Mathematical Reflection]
  Risk scenario: EMA crossover fires in ranging market (ADX < 20)
  From trade log: ADX < 20 during all 9 consecutive losses
  Constraint: adx_threshold ≥ 25 (where win_rate > break-even)

[Action] Add ADX regime filter:
  + adx_val = ta.adx(14)
  + trending = adx_val > 25
  + Guard all entries with `and trending`
  + Add `if not trending → close all`

[Re-backtest] Return: +18.2%, WinRate: 42%, PF: 1.55, MaxDD: 12.1%
[Result] ✅ Gate 1 PASSED → proceed to Gate 2

── Gate 2 ───────────────────────────────────────
[Holdout] Train: +15.3%, Holdout: +8.7%, degradation: 43%
[Result] ✅ Gate 2 PASSED → proceed to Gate 3
```

## ═══ KNOWLEDGE BASE ═══

### Cross-Run Learning

Each optimization run appends results to `knowledge/optimization_log.jsonl`. Future runs
automatically read this history to learn from past successes and failures.

Fields: `strategy`, `symbol`, `timeframe`, `iterations`, `gate1_pass`, `gate2_pass`,
`best_pf`, `key_lessons`, `failed_interventions`, `successful_interventions`.

### Strategy Type Classification

`validate_pine.py` and `analyze_backtest.py --pine-file` automatically detect strategy type:
- **trend_following**: crossover/crossunder, multiple EMAs, momentum, ROC
- **mean_reversion**: z-score, stdev, Bollinger Bands, RSI with oversold/overbought
- **hybrid**: both patterns present

Analysis recommendations are adjusted per strategy type (e.g., WHIPSAW thresholds relaxed
for mean-reversion strategies).

## References

- `references/pine-patterns.md` — 4 strategy templates
- `references/pine-builtins.md` — Supported ta.*/strategy.* functions
- `references/optimization-cases.md` — 4 mathematical reasoning examples + decision tree
- `references/pine-interpreter-quirks.md` — **READ THIS** — QuantForge interpreter behavioral differences
- `references/anti-patterns.md` — Organized by strategy type (trend, mean-reversion, hybrid)
- `knowledge/optimization_log.jsonl` — Cross-run optimization history
