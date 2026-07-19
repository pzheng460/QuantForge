"""Single-trial runner.

Stages an isolated skill dir (canonical skill + method's SKILL.md), launches
Claude Code via subprocess to run one TiMi optimization trial, captures the
optimized .pine path. Does not score OOS — that's holdout_eval.py's job.

Usage:
    uv run python -m eval.optimizer_ab.runner \\
        --method baseline \\
        --strategy quantforge/pine/strategies/momentum_adx.pine \\
        --regime trend_2024h1 --seed 1 \\
        --out eval/optimizer_ab/results/trial.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from quantforge.agent_providers import build_agent_command, resolve_model

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = Path(__file__).resolve().parent
CANONICAL_SKILL = PROJECT_ROOT / ".claude" / "skills" / "quantforge-optimizer"

FINAL_RE = re.compile(r"FINAL_OUTPUT:\s*([^\s\"'\\]+)")


_DATE_RANGE_RE = re.compile(r"--start\s+\d{4}-\d{2}-\d{2}\s+--end\s+\d{4}-\d{2}-\d{2}")


def _sanitize_dates(text: str, train_start: str, train_end: str) -> str:
    """Replace any hardcoded `--start YYYY-MM-DD --end YYYY-MM-DD` snippet
    with the trial's pinned training window. Without this, the agent can
    copy the example dates from SKILL.md / scripts and end up backtesting
    outside the training window — which would breach the air gap.
    """
    return _DATE_RANGE_RE.sub(f"--start {train_start} --end {train_end}", text)


def stage_skill(method_dir, work_root, train_start=None, train_end=None):
    if not CANONICAL_SKILL.exists():
        raise SystemExit(f"Claude skill missing: {CANONICAL_SKILL}")
    method_skill = method_dir / "SKILL.md"
    if not method_skill.exists():
        raise SystemExit(f"Method SKILL.md missing: {method_skill}")
    staged = work_root / "skill"
    shutil.copytree(CANONICAL_SKILL, staged)
    shutil.copy(method_skill, staged / "SKILL.md")
    log = staged / "knowledge" / "optimization_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("")

    # Air-gap hardening: rewrite any hardcoded date ranges in SKILL.md and
    # script docstrings so the agent cannot accidentally copy them.
    if train_start and train_end:
        for path in [
            staged / "SKILL.md",
            *(staged / "scripts").glob("*.py"),
            *(staged / "references").glob("*.md"),
        ]:
            if not path.is_file():
                continue
            try:
                txt = path.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            new = _sanitize_dates(txt, train_start, train_end)
            if new != txt:
                path.write_text(new)
    return staged


def split_train_window(train_start: str, train_end: str, train_fraction: float = 0.7):
    start = datetime.strptime(train_start, "%Y-%m-%d").date()
    end = datetime.strptime(train_end, "%Y-%m-%d").date()
    if end <= start:
        return {
            "fit_start": train_start,
            "fit_end": train_end,
            "validation_start": train_start,
            "validation_end": train_end,
        }
    total_days = (end - start).days + 1
    fit_days = max(1, int(total_days * train_fraction))
    fit_end = start + timedelta(days=fit_days - 1)
    if fit_end >= end:
        fit_end = start + timedelta(days=max(0, total_days - 2))
    validation_start = fit_end + timedelta(days=1)
    return {
        "fit_start": start.isoformat(),
        "fit_end": fit_end.isoformat(),
        "validation_start": validation_start.isoformat(),
        "validation_end": end.isoformat(),
    }


def build_prompt(
    skill_dir,
    src,
    work_path,
    output_path,
    symbol,
    timeframe,
    exchange,
    train_start,
    train_end,
    max_iters,
    seed,
    internal_split=None,
):
    internal_split = internal_split or split_train_window(train_start, train_end)
    return (
        "You are an expert quantitative trading strategy optimizer.\n\n"
        "FROZEN TRAINING WINDOW — every backtest MUST use these dates:\n"
        f"    --start {train_start} --end {train_end}\n"
        "A separate evaluator owns the hidden out-of-sample window.\n"
        "Do NOT backtest outside the training window.\n\n"
        "INTERNAL TRAIN/VALIDATION SPLIT — visible to you and required:\n"
        f"  fit window:        --start {internal_split['fit_start']} --end {internal_split['fit_end']}\n"
        f"  validation window: --start {internal_split['validation_start']} --end {internal_split['validation_end']}\n"
        "Use the fit window to propose candidates. Use the validation window\n"
        "to select among candidates. The full frozen training window is only\n"
        "for final confirmation after selection.\n\n"
        f"Read the closed-loop protocol at {skill_dir}/SKILL.md.\n\n"
        "## Task\n"
        f"- Original (read-only): {src}\n"
        f"- Working copy (edit only this): {work_path}\n"
        f"- Symbol: {symbol}  Timeframe: {timeframe}  Exchange: {exchange}\n\n"
        "## ANTI-FABRICATION CONTRACT — read carefully\n"
        "The harness audits every Bash tool call you make. It will compare\n"
        "the metrics you report against the actual stdout of the backtest\n"
        "you ran. Trials that emit FINAL_OUTPUT without at least one real\n"
        "Bash backtest invocation are flagged and rejected.\n\n"
        "Specifically you MUST:\n"
        "  1. Run the baseline backtest via the Bash tool (not just describe it).\n"
        "  2. Quote actual numbers from the backtest's stdout — never estimate,\n"
        "     never reuse memorised numbers, never claim a strategy passes\n"
        "     Gate 1 without seeing real PF/MaxDD/trades values from stdout.\n"
        "  3. If you propose a code change, you MUST re-run the backtest on\n"
        "     the modified file to verify the claimed improvement.\n"
        "Hallucinated metrics will fail the trial regardless of FINAL_OUTPUT.\n\n"
        "## Robust optimization requirement\n"
        "Gate 1 passing is NOT enough to stop. Even if the baseline passes,\n"
        "you must test at least 3 distinct candidate variants before final\n"
        "selection. A candidate variant can be a parameter-neighborhood\n"
        "perturbation, constrained-grid result, or a small risk-control edit.\n"
        "You may still select the original baseline, but only after running\n"
        "and comparing those candidates.\n\n"
        "For each candidate you MUST run both:\n"
        "  1. fit-window backtest\n"
        "  2. validation-window backtest\n"
        "Name/log these as candidate_N_fit and candidate_N_validation.\n\n"
        "Score candidates by validation-first robust_score, not raw IS Sharpe:\n"
        "  fit_score = fit_PF - 2*fit_MaxDD\n"
        "  val_score = val_PF - 2*val_MaxDD\n"
        "  robust_score = val_score - 0.5*max(0, fit_score - val_score)\n"
        "                 - 0.01*abs(val_trades - baseline_val_trades)\n"
        "Reject candidates with validation trades < 10, validation PF < 1.0,\n"
        "or validation MaxDD >= 15%.\n"
        "Prefer candidates whose nearby parameter perturbations keep PF > 1.0\n"
        "and MaxDD < 15%.\n\n"
        "## Stop conditions\n"
        f"At most {max_iters} iterations after the baseline, but never fewer\n"
        "than 3 candidates with fit+validation backtests unless the strategy cannot be parsed or\n"
        "every candidate fails syntax validation. Gate values must come from\n"
        "Bash backtests executed in the current session.\n\n"
        "## Required final action\n"
        f"1. Run:  cp {work_path} {output_path}\n"
        "2. Print, on a line by itself, exactly:\n"
        f"       FINAL_OUTPUT: {output_path}\n"
        "Without this sentinel the trial is marked failed.\n\n"
        "## Backtest command template\n"
        "uv run python -m quantforge.pine.cli backtest <pine_file> \\\n"
        f"    --symbol {symbol} --timeframe {timeframe} --exchange {exchange} \\\n"
        f"    --start {train_start} --end {train_end}\n\n"
        f"(Trial seed: {seed}.)\n\n"
        "Begin.\n"
    )


def invoke_agent(prompt, provider, model, max_turns, timeout_s, log_path):
    cmd = build_agent_command(
        provider,
        model,
        project_dir=PROJECT_ROOT,
        max_turns=max_turns,
    )
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(PROJECT_ROOT),
        env=os.environ.copy(),
        text=True,
        bufsize=1,
    )
    proc.stdin.write(prompt)
    proc.stdin.close()
    chunks = []
    deadline = time.time() + max(60, timeout_s)
    with open(log_path, "w") as logf:
        while True:
            if proc.poll() is not None:
                rest = proc.stdout.read()
                if rest:
                    chunks.append(rest)
                    logf.write(rest)
                break
            if time.time() > deadline:
                proc.kill()
                proc.wait()
                logf.write("\n[harness] TIMEOUT\n")
                return 124, "".join(chunks)
            line = proc.stdout.readline()
            if not line:
                time.sleep(0.05)
                continue
            chunks.append(line)
            logf.write(line)
            logf.flush()
    return proc.returncode or 0, "".join(chunks)


def extract_final(stream):
    m = FINAL_RE.search(stream)
    return m.group(1).strip("\"' ") if m else None


def extract_cost(stream):
    last = 0.0
    for line in stream.splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        if obj.get("type") == "result" and "total_cost_usd" in obj:
            last = float(obj["total_cost_usd"])
    return last


def count_real_backtests(stream):
    """Return how many Bash tool calls ran `pine.cli backtest`. Trials with
    zero real backtests are flagged as `lazy` — the agent fabricated metrics
    or skipped optimization entirely. The air gap still holds (holdout_eval
    is run independently) but the trial does not represent real optimizer
    work and should be excluded from baseline distributions."""
    n = 0
    for line in stream.splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        if obj.get("type") == "item.completed":
            item = obj.get("item") or {}
            if item.get("type") == "command_execution":
                cmd = item.get("command", "") or ""
                if "pine.cli backtest" in cmd or "pine.cli optimize" in cmd:
                    n += 1
        if obj.get("type") != "assistant":
            continue
        for item in obj.get("message", {}).get("content", []):
            if item.get("type") != "tool_use" or item.get("name") != "Bash":
                continue
            cmd = item.get("input", {}).get("command", "") or ""
            if "pine.cli backtest" in cmd or "pine.cli optimize" in cmd:
                n += 1
    return n


def _extract_command(obj):
    if obj.get("type") == "item.completed":
        item = obj.get("item") or {}
        if item.get("type") == "command_execution":
            return item.get("command", "") or ""
    if obj.get("type") == "assistant":
        commands = []
        for item in obj.get("message", {}).get("content", []):
            if item.get("type") == "tool_use" and item.get("name") == "Bash":
                commands.append(item.get("input", {}).get("command", "") or "")
        return "\n".join(commands)
    return ""


def _backtest_commands(stream):
    commands = []
    for line in stream.splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        cmd = _extract_command(obj)
        if "pine.cli backtest" in cmd or "pine.cli optimize" in cmd:
            commands.append(cmd)
    return commands


def _uses_date_range(cmd, start, end):
    return f"--start {start}" in cmd and f"--end {end}" in cmd


def _validation_commands(commands, internal_split=None):
    validation_start = (internal_split or {}).get("validation_start")
    validation_end = (internal_split or {}).get("validation_end")
    return [
        cmd
        for cmd in commands
        if (
            "validation" in cmd.lower()
            or "_val" in cmd.lower()
            or (
                validation_start
                and validation_end
                and _uses_date_range(cmd, validation_start, validation_end)
            )
        )
    ]


def _fit_or_validation_commands(commands, internal_split=None):
    fit_start = (internal_split or {}).get("fit_start")
    fit_end = (internal_split or {}).get("fit_end")
    validation_start = (internal_split or {}).get("validation_start")
    validation_end = (internal_split or {}).get("validation_end")
    if not all([fit_start, fit_end, validation_start, validation_end]):
        return []
    return [
        cmd
        for cmd in commands
        if (
            _uses_date_range(cmd, fit_start, fit_end)
            or _uses_date_range(cmd, validation_start, validation_end)
        )
    ]


def _command_weight(cmd):
    m = re.search(r"for\s+\w+\s+in\s+([0-9 ]+)\s*;", cmd)
    if not m:
        return 1
    return len([x for x in m.group(1).split() if x.strip()])


def summarize_trial_audit(stream, optimized, work_pine, internal_split=None):
    commands = _backtest_commands(stream)
    work_name = Path(work_pine).name
    split_commands = set(_fit_or_validation_commands(commands, internal_split))
    candidate_commands = [
        cmd
        for cmd in commands
        if (
            work_name not in cmd
            or "candidate" in cmd.lower()
            or "optimized" in cmd.lower()
            or cmd in split_commands
        )
    ]
    validation_commands = _validation_commands(candidate_commands, internal_split)
    candidate_backtests = sum(_command_weight(cmd) for cmd in candidate_commands)
    validation_backtests = sum(_command_weight(cmd) for cmd in validation_commands)
    optimization_attempted = candidate_backtests >= 6 and validation_backtests >= 3
    optimized_path = Path(optimized) if optimized else None
    work_path = Path(work_pine)
    no_op = (not optimization_attempted) or (
        optimized_path is not None
        and optimized_path.exists()
        and work_path.exists()
        and optimized_path.read_text() == work_path.read_text()
        and len(candidate_commands) == 0
    )
    return {
        "n_backtests": sum(_command_weight(cmd) for cmd in commands),
        "candidate_backtests": candidate_backtests,
        "validation_backtests": validation_backtests,
        "optimization_attempted": optimization_attempted,
        "no_op": no_op,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(EVAL_ROOT / "test_set.yaml"))
    p.add_argument("--method", required=True)
    p.add_argument("--strategy", required=True)
    p.add_argument("--regime", required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--agent-provider", choices=["claude", "codex"], default=None)
    p.add_argument("--model", default=None)
    a = p.parse_args()

    cfg = yaml.safe_load(Path(a.config).read_text())
    defaults = cfg.get("defaults") or {}
    trial_cfg = cfg["trial"]
    train = cfg["regimes"][a.regime]["train_period"]

    method_dir = EVAL_ROOT / "methods" / a.method
    if not (method_dir / "SKILL.md").exists():
        print(f"ERROR: method SKILL.md missing at {method_dir}", file=sys.stderr)
        return 2

    src = (PROJECT_ROOT / a.strategy).resolve()
    if not src.exists():
        print(f"ERROR: strategy not found: {src}", file=sys.stderr)
        return 2

    trial_id = f"{a.method}__{src.stem}__{a.regime}__s{a.seed}__{uuid.uuid4().hex[:6]}"
    work_root = Path(tempfile.mkdtemp(prefix=f"qf_ab_{trial_id}_"))
    skill_dir = stage_skill(
        method_dir,
        work_root,
        train_start=str(train["start"]),
        train_end=str(train["end"]),
    )

    work_pine = work_root / src.name
    shutil.copy(str(src), str(work_pine))
    out_pine = work_root / "optimized.pine"
    provider = a.agent_provider or trial_cfg.get("agent_provider", "claude")
    config_provider = trial_cfg.get("agent_provider", "claude")
    config_model = trial_cfg.get("model") if provider == config_provider else None
    model = resolve_model(provider, a.model or config_model)
    log_path = work_root / f"{provider}_stream.log"
    internal_split = split_train_window(str(train["start"]), str(train["end"]))

    prompt = build_prompt(
        skill_dir,
        src,
        work_pine,
        out_pine,
        defaults.get("symbol", "BTC/USDT:USDT"),
        defaults.get("timeframe", "1h"),
        defaults.get("exchange", "bitget"),
        str(train["start"]),
        str(train["end"]),
        int(trial_cfg.get("max_iterations", 5)),
        a.seed,
        internal_split,
    )
    started = datetime.now(timezone.utc).isoformat()
    rc, stream = invoke_agent(
        prompt,
        provider,
        model,
        int(trial_cfg.get("max_turns", 80)),
        int(trial_cfg.get("timeout_seconds", 1800)),
        log_path,
    )
    finished = datetime.now(timezone.utc).isoformat()

    final = extract_final(stream)
    optimized = (
        final
        if (final and Path(final).exists())
        else (str(out_pine) if out_pine.exists() else None)
    )
    cost = extract_cost(stream)
    audit = summarize_trial_audit(stream, optimized, work_pine, internal_split)
    record = {
        "trial_id": trial_id,
        "method": a.method,
        "strategy": a.strategy,
        "strategy_name": src.stem,
        "regime": a.regime,
        "seed": a.seed,
        "started_at": started,
        "finished_at": finished,
        "returncode": rc,
        "cost_usd": cost,
        "n_backtests": audit["n_backtests"],
        "candidate_backtests": audit["candidate_backtests"],
        "validation_backtests": audit["validation_backtests"],
        "optimization_attempted": audit["optimization_attempted"],
        "no_op": audit["no_op"],
        "lazy_warning": audit["n_backtests"] == 0,
        "stream_log": str(log_path),
        "work_dir": str(work_root),
        "optimized_pine": optimized,
        "train_window": {"start": str(train["start"]), "end": str(train["end"])},
        "internal_split": split_train_window(str(train["start"]), str(train["end"])),
        "symbol": defaults.get("symbol"),
        "timeframe": defaults.get("timeframe"),
        "exchange": defaults.get("exchange"),
        "agent_provider": provider,
        "model": model,
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(record, indent=2))
    status = "OK" if optimized else "FAIL"
    print(
        f"[runner:{status}] {trial_id} rc={rc} cost=${cost:.2f}  optimized={optimized!r}"
    )
    return 0 if optimized else 1


if __name__ == "__main__":
    raise SystemExit(main())
