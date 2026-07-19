import json

from eval.optimizer_ab.cross_review import (
    build_review_prompt,
    extract_review_output,
    pair_provider_trials,
    summarize_review_records,
)


def test_pair_provider_trials_matches_same_cell():
    rows = [
        {
            "trial_json": "claude.json",
            "method": "baseline",
            "strategy_name": "ema_crossover",
            "regime": "trend_2024h1",
            "seed": "1",
            "agent_provider": "claude",
        },
        {
            "trial_json": "codex.json",
            "method": "baseline",
            "strategy_name": "ema_crossover",
            "regime": "trend_2024h1",
            "seed": "1",
            "agent_provider": "codex",
        },
    ]

    pairs = pair_provider_trials(rows, "claude", "codex")

    assert pairs == [(rows[0], rows[1])]


def test_extract_review_output_accepts_json_sentinel():
    payload = {
        "decision": "needs_retest",
        "robustness_score": 72,
        "overfit_risk": 41,
        "improvement_factors": [
            {
                "factor": "ADX filter",
                "evidence": "validation drawdown",
                "expected_effect": "reduce whipsaw",
            }
        ],
        "blocking_issues": [],
    }
    stream = "thinking\nREVIEW_OUTPUT: " + json.dumps(payload)

    assert extract_review_output(stream) == payload


def test_extract_review_output_from_ndjson_agent_message():
    payload = {"decision": "accept", "improvement_factors": [], "blocking_issues": []}
    stream = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": "REVIEW_OUTPUT: " + json.dumps(payload),
            },
        }
    )

    assert extract_review_output(stream) == payload


def test_build_review_prompt_is_read_only_and_structured():
    trial = {
        "trial_id": "t1",
        "agent_provider": "claude",
        "optimized_pine": "/tmp/optimized.pine",
        "stream_log": "/tmp/claude_stream.log",
        "internal_split": {
            "fit_start": "2024-01-01",
            "fit_end": "2024-05-06",
            "validation_start": "2024-05-07",
            "validation_end": "2024-06-30",
        },
    }

    prompt = build_review_prompt(trial, reviewer_provider="codex")

    assert "read-only cross-review" in prompt
    assert "REVIEW_OUTPUT:" in prompt
    assert "/tmp/optimized.pine" in prompt
    assert "2024-05-07" in prompt


def test_summarize_review_records_flattens_improvement_factors():
    records = [
        {
            "trial_id": "t1",
            "reviewed_provider": "claude",
            "reviewer_provider": "codex",
            "review": {
                "decision": "needs_retest",
                "robustness_score": 72,
                "overfit_risk": 41,
                "improvement_factors": [
                    {
                        "factor": "ADX filter",
                        "evidence": "validation drawdown",
                        "expected_effect": "reduce whipsaw",
                    }
                ],
            },
        }
    ]

    assert summarize_review_records(records) == [
        {
            "trial_id": "t1",
            "reviewed_provider": "claude",
            "reviewer_provider": "codex",
            "decision": "needs_retest",
            "robustness_score": 72,
            "overfit_risk": 41,
            "factor": "ADX filter",
            "evidence": "validation drawdown",
            "expected_effect": "reduce whipsaw",
        }
    ]
