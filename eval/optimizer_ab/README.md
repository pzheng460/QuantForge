# TiMi-loop A/B harness

Evaluate prospective changes to repo-local TiMi closed-loop optimizer methods
against the current baseline. The harness measures **out-of-sample** performance of the
optimized strategies — the optimizer never sees the holdout window during
its run.

## Air gap (the whole point)

```
runner.py    ── invokes claude subprocess ──>  agent sees train window only
                                              writes optimized .pine
                                                       │
                                                       ▼
holdout_eval.py (separate process) ── reads optimized .pine,
                                       runs train AND holdout backtests,
                                       writes both metrics back
```

The agent's prompt explicitly fixes the training window. The holdout
evaluator is a separate Python process whose results are written back
into the trial JSON only after the agent has terminated.

## Files

| File | Role |
|---|---|
| `test_set.yaml` | Frozen test set — strategies (dev/test/holdout tiers), regimes, holdout windows, trial budget. |
| `methods/<name>/SKILL.md` | One per method under test. `baseline/SKILL.md` is the current TiMi loop. |
| `runner.py` | Run one (method, strategy, regime, seed) trial via `claude --print --stream-json`. |
| `holdout_eval.py` | Air-gapped OOS evaluator. Reads the trial JSON, runs train+holdout backtests, writes metrics back. |
| `orchestrate.py` | Run the full matrix. Resume-safe (skips trial_ids already in CSV). |
| `analyze.py` | Pairwise comparison: per-method aggregates + paired Wilcoxon + bootstrap CI. |

## Smoke test (one cell, ~5 min, ~$1)

```
uv run python -m eval.optimizer_ab.orchestrate \
    --tier dev --methods baseline \
    --regimes trend_2024h1 --seeds 1
```

Expected output: `results/matrix.csv` with one row, `results/trials/*.json`
with the full trial record (returncode, cost, optimized_pine path, IS+OOS
metrics under `holdout`).

## Running an A/B (small)

1. Create a new method: `cp -r methods/baseline methods/reflexion`,
   then edit `methods/reflexion/SKILL.md` with the change you want to
   test.
2. Run baseline + treatment on the test tier:
   ```
   uv run python -m eval.optimizer_ab.orchestrate \
       --tier test --methods baseline,reflexion --seeds 1,2,3
   ```
3. Analyze:
   ```
   uv run python -m eval.optimizer_ab.analyze \
       --csv results/matrix.csv \
       --baseline baseline --treatment reflexion \
       --metric oos_sharpe
   ```
   Also run with `--metric overfit_index` to see if the new method is
   trading OOS performance for IS overfit.

## Cost estimation

Each trial calls Claude with `max_iterations=5`, `max_turns=80`. Average
cost ≈ $0.5–$2 for sonnet-4. Full A/B (2 methods × 5 strategies ×
3 regimes × 3 seeds = 90 trials) ≈ $90–$180.

Use `--no-holdout` for a runner-only smoke test (skips the OOS pass).

## Claude/Codex cross-validation

Run the same cells through both agent CLIs by passing multiple providers.
Use `--strategies` to keep smoke tests small:

```
uv run python -m eval.optimizer_ab.orchestrate \
    --tier dev --methods baseline \
    --strategies ema_crossover \
    --regimes trend_2024h1 --seeds 1 \
    --agent-providers claude,codex
```

Provider pairing is analyzed independently from method pairing:

```
uv run python -m eval.optimizer_ab.analyze \
    --csv eval/optimizer_ab/results/matrix.csv \
    --metric oos_sharpe \
    --compare-providers claude,codex
```

After both providers have produced candidates, run bidirectional review to
extract improvement factors:

```
uv run python -m eval.optimizer_ab.cross_review \
    --csv eval/optimizer_ab/results/matrix.csv \
    --providers claude,codex \
    --summary-csv eval/optimizer_ab/results/cross_reviews/factors.csv
```

This writes one structured review JSON per direction plus a flat CSV of
`improvement_factors` for ranking the next optimization knobs.

The first review-guided method is `cross_review_guided`. It keeps the baseline
TiMi loop but forces the optimizer to test review-derived factors such as EMA
slow-region, ADX 18-23, ATR stop, direction asymmetry, and validation sample
penalties:

```
uv run python -m eval.optimizer_ab.orchestrate \
    --tier dev --methods baseline,cross_review_guided \
    --strategies ema_crossover \
    --regimes trend_2024h1 --seeds 1 \
    --agent-providers claude,codex
```

For continuous adjustment, run the auto-tune evidence collector before launching
agents. It evaluates multiple windows and can merge exogenous news/events from a
JSONL file with `title`, `summary`, `symbols`, `source`, and `published_at`:

```
uv run python -m eval.auto_tune run \
    --pine quantforge/pine/strategies/ema_crossover.pine \
    --strategy ema_crossover \
    --windows current:2024-07-01:2024-12-31,stress:2024-08-01:2024-09-30 \
    --news-file events.jsonl \
    --out eval/optimizer_ab/results/auto_tune_report.json
```

The default mode is dry-run. Add `--execute` only when the evidence gate says
re-optimization is appropriate and the run budget is acceptable.

For production scheduling, use QuantForge's internal scheduler wrapper:

```
uv run quantforge-cli auto-tune run-once \
    --pine quantforge/pine/strategies/ema_crossover.pine \
    --strategy ema_crossover \
    --windows current:2024-07-01:2024-12-31,stress:2024-08-01:2024-09-30 \
    --news-file events.jsonl

uv run quantforge-cli auto-tune run-once \
    --pine quantforge/pine/strategies/ema_crossover.pine \
    --strategy ema_crossover \
    --windows current:2024-07-01:2024-12-31,stress:2024-08-01:2024-09-30 \
    --news-file events.jsonl \
    --execute \
    --auto-deploy \
    --optimizer-results-csv eval/optimizer_ab/results/auto_tune.csv \
    --optimizer-trials-dir eval/optimizer_ab/results/auto_tune_trials \
    --promotion-report eval/optimizer_ab/results/promotion_pipeline.json \
    --shadow-report eval/optimizer_ab/results/shadow_compare.json

uv run quantforge-cli news rss \
    https://www.coindesk.com/arc/outboundfeeds/rss/ \
    --symbols BTC/USDT:USDT \
    --out eval/optimizer_ab/results/news_events.jsonl
uv run quantforge-cli news exchange-status \
    https://status.example.com/api/v2/incidents/unresolved.json \
    --exchange bitget \
    --symbols BTC/USDT:USDT \
    --out eval/optimizer_ab/results/exchange_events.jsonl
uv run quantforge-cli news microstructure market_microstructure.json \
    --source-name bitget \
    --out eval/optimizer_ab/results/microstructure_events.jsonl

uv run quantforge-cli auto-tune daemon \
    --job-file eval/optimizer_ab/results/auto_tune_jobs.json \
    --interval-sec 3600
```

The scheduler records run history and keeps the decision loop inside
QuantForge. News and event inputs should be collected by QuantForge-owned
collectors and written to `events.jsonl`; promotion and live strategy switching
remain under QuantForge's deployment gate.

When `--execute --auto-deploy` is enabled, the scheduler reads the optimizer
matrix CSV, ignores failed/lazy/no-op trials, selects the best deployable
candidate by `--deploy-metric` (default `oos_sharpe`), and sends it through the
same auto-promote pipeline.

External event risk is fused from keyword/news signals plus structured
components for exchange status, funding, open interest, and liquidations. The
auto-tune report keeps the aggregate `risk_score`/`risk_level` and the
component breakdown under `news_risk.components`.

The run-once/daemon wrapper also applies the generated auto-tune report to
QuantForge trading control state. Live Pine startup reads that control state:
`pause` blocks launch, while `reduce` halves the configured position size unless
`--ignore-control` is explicitly passed.

The scheduler keeps OpenClaw-style operational artifacts inside QuantForge:
`auto_tune_jobs_state.json` for aggregate job state, `auto_tune_runs/*.jsonl`
for per-run event journals, and `auto_tune_failed/*.json` for failed run
records. These are QuantForge-owned files, not OpenClaw runtime dependencies.

Optimized artifacts should enter QuantForge through the deployment registry,
not by overwriting a live strategy file:

```
uv run quantforge-cli deployment register \
    --strategy-id ema_crossover \
    --pine path/to/optimized.pine \
    --evidence eval/optimizer_ab/results/auto_tune_report.json \
    --source auto_tune
uv run quantforge-cli deployment transition <version-id> paper
uv run quantforge-cli deployment transition <version-id> shadow
uv run quantforge-cli deployment shadow-compare ema_crossover \
    --start 2024-07-01 --end 2024-12-31 \
    --out eval/optimizer_ab/results/shadow_compare.json
uv run quantforge-cli deployment promote <version-id>
uv run quantforge-cli deployment live-command ema_crossover --mode paper
```

`shadow-compare` evaluates the current promoted version and latest shadow
candidate on the same holdout window, then fails the command if trade count,
profit factor, or drawdown gates do not pass.

For cron-style automation, use the single pipeline command instead of manually
running each deployment step:

```
uv run quantforge-cli deployment auto-promote ema_crossover \
    --pine eval/optimizer_ab/results/auto_tune_trials/best/optimized.pine \
    --evidence eval/optimizer_ab/results/auto_tune_report.json \
    --start 2024-07-01 --end 2024-12-31 \
    --shadow-report eval/optimizer_ab/results/shadow_compare.json \
    --ledger ~/.quantforge/paper_ledger.sqlite \
    --min-runtime-fills 2 \
    --min-runtime-pnl-delta 0 \
    --max-runtime-drawdown-delta 0.02 \
    --out eval/optimizer_ab/results/promotion_pipeline.json
```

It registers the candidate, moves it through paper and shadow, promotes only
when shadow comparison passes, and rejects the candidate while keeping the
current promoted version active when shadow comparison fails. When `--ledger`
is supplied, runtime shadow evidence must also pass: sufficient shadow fills,
PnL not worse than promoted by the configured threshold, and drawdown not worse
than promoted by more than the configured delta.

Promotion is rejected unless the evidence action is `observe`, trigger reasons
are empty, and news risk is not high. Roll back with
`uv run quantforge-cli deployment rollback <strategy-id>`.

Real live launch commands require an explicit approval record:

```
uv run quantforge-cli deployment approval request live_command --strategy-id ema_crossover
uv run quantforge-cli deployment approval approve <approval-id> --approver <name>
uv run quantforge-cli deployment live-command ema_crossover \
    --mode live \
    --approval-id <approval-id> \
    --approvals ~/.quantforge/approvals.json \
    --policy live_policy.yaml \
    --request live_request.json
```

During paper or shadow observation, record virtual signals into QuantForge's
paper ledger:

```
uv run quantforge-cli paper signal ema_crossover \
    --role shadow --side buy --price 100 --quantity 2 \
    --version-id <candidate-version-id> \
    --fee-rate 0.001 --slippage-bps 10
uv run quantforge-cli paper summary ema_crossover --role shadow
uv run quantforge-cli paper shadow-run ema_crossover \
    --events eval/optimizer_ab/results/runtime_signals.jsonl \
    --registry ~/.quantforge/deployments.json \
    --ledger ~/.quantforge/paper_ledger.sqlite \
    --out eval/optimizer_ab/results/shadow_observation.json
uv run quantforge-cli risk check ema_crossover \
    --role promoted \
    --max-drawdown 0.05 \
    --max-daily-loss 500 \
    --auto-rollback \
    --registry ~/.quantforge/deployments.json \
    --control-state eval/optimizer_ab/results/trading_control.json \
    --out eval/optimizer_ab/results/risk_report.json
uv run quantforge-cli risk execution live_orders.jsonl \
    --strategy-id ema_crossover \
    --control-state eval/optimizer_ab/results/trading_control.json \
    --max-slippage-bps 50 \
    --max-latency-ms 1000 \
    --max-spread-bps 20 \
    --out eval/optimizer_ab/results/execution_risk.json
uv run quantforge-cli risk live-policy live_policy.yaml live_request.json \
    --approvals ~/.quantforge/approvals.json \
    --out eval/optimizer_ab/results/live_policy_report.json
uv run quantforge-cli audit build ema_crossover \
    --auto-tune eval/optimizer_ab/results/auto_tune_report.json \
    --promotion eval/optimizer_ab/results/promotion_pipeline.json \
    --shadow eval/optimizer_ab/results/shadow_compare.json \
    --risk eval/optimizer_ab/results/risk_report.json \
    --json-out eval/optimizer_ab/results/audit_report.json \
    --markdown-out eval/optimizer_ab/results/audit_report.md
```

The ledger records signals, virtual fills, position state, realized PnL, equity,
and max drawdown. Use `.json` for a simple file ledger or `.sqlite`/`.db` for
the SQLite backend. `paper shadow-run` consumes normalized runtime signal JSONL
with `promoted` and `shadow` signal objects per market event, binds them to the
current promoted and latest shadow deployment versions, and writes both sides to
the same ledger. `risk check` is the first kill-switch layer: it writes
`observe`, `reduce`, or `pause` into QuantForge trading control state based on
drawdown, daily loss, single-loss, and consecutive-loss limits. With
`--auto-rollback`, a hard `pause` decision restores the previous promoted
deployment version when one exists. `audit build` then merges auto-tune,
promotion, shadow, risk, and rollback evidence into one JSON/Markdown report.

## Air-gap invariants

The harness preserves these properties; do not break them when adding methods:

1. The agent's only data window is `regimes[<regime>].train_period`.
2. `regimes[<regime>].holdout_period` must not appear in any prompt sent
   to the agent.
3. `holdout_eval.py` runs after `runner.py` returns, in a separate process.
4. `optimization_log.jsonl` is wiped per trial (cross-run learning would
   contaminate baseline).
5. `runner.stage_skill` rewrites every `--start YYYY-MM-DD --end YYYY-MM-DD`
   pattern in SKILL.md, scripts, and references to the trial's pinned
   training window so the agent cannot copy a stale example.
6. `holdout_eval.evaluate` filters equity_curve and trades by bar timestamp
   before computing metrics; the warmup prefix that overlaps the train
   window is excluded.

## Known limitations

- **No deterministic LLM seed.** The Claude Code CLI does not expose
  `--seed`, so `seeds: [1, 2, 3]` in test_set.yaml are *replicate
  indices*, not reproducible random seeds — re-running the same (method,
  strategy, regime, seed) cell produces a fresh sample. Results should
  be reported as median ± bootstrap CI across seeds, not as point
  estimates from a single run. `analyze.py` does this by default.
- **Exchange data depth.** ccxt fetches in pages of 1000 bars; for
  Bitget on 1h BTC, full 6-month windows may not return all expected
  bars. The harness reports `n_bars` and `n_warmup_bars` so this is
  visible.
- **Agent may stop before max_iterations.** If the strategy passes its
  own Gate-1 early (regardless of whether the in-sample sample size is
  meaningful), the agent stops. This is the agent's behaviour, not a
  framework bug.
