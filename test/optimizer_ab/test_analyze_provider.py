from eval.optimizer_ab.analyze import provider_paired


def test_provider_paired_matches_same_cell_across_providers():
    rows = [
        {
            "method": "baseline",
            "strategy_name": "ema_crossover",
            "regime": "trend_2024h1",
            "seed": 1,
            "agent_provider": "claude",
            "candidate_backtests": 9.0,
        },
        {
            "method": "baseline",
            "strategy_name": "ema_crossover",
            "regime": "trend_2024h1",
            "seed": 1,
            "agent_provider": "codex",
            "candidate_backtests": 12.0,
        },
    ]

    assert provider_paired(rows, "claude", "codex", "candidate_backtests") == [
        (9.0, 12.0, ("baseline", "ema_crossover", "trend_2024h1", 1))
    ]
