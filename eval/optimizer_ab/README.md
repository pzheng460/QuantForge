# TiMi-loop A/B harness

Evaluate prospective changes to the TiMi closed-loop optimizer
(`~/.openclaw/skills/quantforge-optimizer/SKILL.md`) against the current
baseline. The harness measures **out-of-sample** performance of the
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
