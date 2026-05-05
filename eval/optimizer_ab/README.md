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
