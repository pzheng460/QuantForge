from eval.optimizer_ab.runner import (
    CANONICAL_SKILL,
    count_real_backtests,
    split_train_window,
    stage_skill,
    summarize_trial_audit,
)


def test_canonical_optimizer_skill_is_claude_skill():
    assert CANONICAL_SKILL.parts[-3:] == (".claude", "skills", "quantforge-optimizer")
    assert (CANONICAL_SKILL / "SKILL.md").exists()
    assert (CANONICAL_SKILL / "scripts" / "validate_pine.py").exists()


def test_split_train_window_creates_internal_validation():
    split = split_train_window("2024-01-01", "2024-06-30")

    assert split == {
        "fit_start": "2024-01-01",
        "fit_end": "2024-05-06",
        "validation_start": "2024-05-07",
        "validation_end": "2024-06-30",
    }


def test_count_codex_backtests_and_optimizer_runs():
    stream = "\n".join(
        [
            '{"type":"item.completed","item":{"type":"command_execution","command":"uv run python -m quantforge.pine.cli backtest baseline.pine"}}',
            '{"type":"item.completed","item":{"type":"command_execution","command":"uv run python -m quantforge.pine.cli optimize candidate.pine"}}',
        ]
    )

    assert count_real_backtests(stream) == 2


def test_audit_marks_single_baseline_as_no_op():
    audit = summarize_trial_audit(
        stream='{"type":"item.completed","item":{"type":"command_execution","command":"uv run python -m quantforge.pine.cli backtest baseline.pine"}}',
        optimized="/tmp/optimized.pine",
        work_pine="/tmp/baseline.pine",
    )

    assert audit["n_backtests"] == 1
    assert audit["candidate_backtests"] == 0
    assert audit["validation_backtests"] == 0
    assert audit["optimization_attempted"] is False
    assert audit["no_op"] is True


def test_audit_accepts_multiple_candidate_backtests():
    stream = "\n".join(
        [
            '{"type":"item.completed","item":{"type":"command_execution","command":"uv run python -m quantforge.pine.cli backtest /tmp/work/ema.pine"}}',
            '{"type":"item.completed","item":{"type":"command_execution","command":"uv run python -m quantforge.pine.cli backtest /tmp/work/candidate_1.pine | tee candidate_1_fit.txt"}}',
            '{"type":"item.completed","item":{"type":"command_execution","command":"uv run python -m quantforge.pine.cli backtest /tmp/work/candidate_1.pine | tee candidate_1_validation.txt"}}',
            '{"type":"item.completed","item":{"type":"command_execution","command":"uv run python -m quantforge.pine.cli backtest /tmp/work/candidate_2.pine | tee candidate_2_fit.txt"}}',
            '{"type":"item.completed","item":{"type":"command_execution","command":"uv run python -m quantforge.pine.cli backtest /tmp/work/candidate_2.pine | tee candidate_2_validation.txt"}}',
            '{"type":"item.completed","item":{"type":"command_execution","command":"uv run python -m quantforge.pine.cli backtest /tmp/work/candidate_3.pine | tee candidate_3_fit.txt"}}',
            '{"type":"item.completed","item":{"type":"command_execution","command":"uv run python -m quantforge.pine.cli backtest /tmp/work/candidate_3.pine | tee candidate_3_validation.txt"}}',
        ]
    )

    audit = summarize_trial_audit(
        stream=stream,
        optimized="/tmp/work/candidate_2.pine",
        work_pine="/tmp/work/ema.pine",
    )

    assert audit["n_backtests"] == 7
    assert audit["candidate_backtests"] == 6
    assert audit["validation_backtests"] == 3
    assert audit["optimization_attempted"] is True
    assert audit["no_op"] is False


def test_stage_skill_uses_repo_local_base(tmp_path):
    method_dir = tmp_path / "method"
    method_dir.mkdir()
    (method_dir / "SKILL.md").write_text("# Method\n")

    staged = stage_skill(method_dir, tmp_path / "work", "2024-01-01", "2024-06-30")

    assert (staged / "SKILL.md").read_text() == "# Method\n"
    assert (staged / "scripts" / "validate_pine.py").exists()
    assert (staged / "scripts" / "analyze_backtest.py").exists()


def test_audit_counts_shell_loop_candidates():
    stream = "\n".join(
        [
            '{"type":"item.completed","item":{"type":"command_execution","command":"uv run python -m quantforge.pine.cli backtest /tmp/work/ema.pine | tee baseline.txt"}}',
            '{"type":"item.completed","item":{"type":"command_execution","command":"for n in 1 2 3 4 5; do uv run python -m quantforge.pine.cli backtest /tmp/work/candidate_${n}.pine | tee candidate_${n}_fit.txt; done"}}',
            '{"type":"item.completed","item":{"type":"command_execution","command":"for n in 1 2 3 4 5; do uv run python -m quantforge.pine.cli backtest /tmp/work/candidate_${n}.pine | tee candidate_${n}_validation.txt; done"}}',
        ]
    )

    audit = summarize_trial_audit(
        stream=stream,
        optimized="/tmp/work/candidate_2.pine",
        work_pine="/tmp/work/ema.pine",
    )

    assert audit["n_backtests"] == 11
    assert audit["candidate_backtests"] == 10
    assert audit["validation_backtests"] == 5
    assert audit["optimization_attempted"] is True


def test_audit_counts_work_file_fit_validation_runs():
    split = split_train_window("2024-01-01", "2024-06-30")
    stream = "\n".join(
        [
            '{"type":"item.completed","item":{"type":"command_execution","command":"uv run python -m quantforge.pine.cli backtest /tmp/work/ema.pine --start 2024-01-01 --end 2024-06-30"}}',
            '{"type":"item.completed","item":{"type":"command_execution","command":"uv run python -m quantforge.pine.cli backtest /tmp/work/ema.pine --start 2024-01-01 --end 2024-05-06"}}',
            '{"type":"item.completed","item":{"type":"command_execution","command":"uv run python -m quantforge.pine.cli backtest /tmp/work/ema.pine --start 2024-05-07 --end 2024-06-30"}}',
            '{"type":"item.completed","item":{"type":"command_execution","command":"uv run python -m quantforge.pine.cli backtest /tmp/work/ema.pine --start 2024-01-01 --end 2024-05-06"}}',
            '{"type":"item.completed","item":{"type":"command_execution","command":"uv run python -m quantforge.pine.cli backtest /tmp/work/ema.pine --start 2024-05-07 --end 2024-06-30"}}',
            '{"type":"item.completed","item":{"type":"command_execution","command":"uv run python -m quantforge.pine.cli backtest /tmp/work/ema.pine --start 2024-01-01 --end 2024-05-06"}}',
            '{"type":"item.completed","item":{"type":"command_execution","command":"uv run python -m quantforge.pine.cli backtest /tmp/work/ema.pine --start 2024-05-07 --end 2024-06-30"}}',
        ]
    )

    audit = summarize_trial_audit(
        stream=stream,
        optimized="/tmp/work/optimized.pine",
        work_pine="/tmp/work/ema.pine",
        internal_split=split,
    )

    assert audit["n_backtests"] == 7
    assert audit["candidate_backtests"] == 6
    assert audit["validation_backtests"] == 3
    assert audit["optimization_attempted"] is True
    assert audit["no_op"] is False
