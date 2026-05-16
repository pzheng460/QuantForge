from eval.optimizer_ab.orchestrate import build_cell_id, filter_strategies, parse_csv_list


def test_cell_id_includes_provider_for_cross_validation():
    cell_id = build_cell_id(
        method="baseline",
        strat="quantforge/pine/strategies/ema_crossover.pine",
        regime="trend_2024h1",
        seed=1,
        provider="codex",
    )

    assert cell_id == "baseline__codex__ema_crossover__trend_2024h1__s1"


def test_parse_csv_list_preserves_default_when_empty():
    assert parse_csv_list("", ["claude"]) == ["claude"]
    assert parse_csv_list("claude,codex", ["claude"]) == ["claude", "codex"]


def test_filter_strategies_accepts_path_or_stem():
    strategies = [
        "quantforge/pine/strategies/ema_crossover.pine",
        "quantforge/pine/strategies/bollinger_band.pine",
    ]

    assert filter_strategies(strategies, ["ema_crossover"]) == [strategies[0]]
    assert filter_strategies(strategies, [strategies[1]]) == [strategies[1]]
