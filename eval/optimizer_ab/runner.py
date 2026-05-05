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
from datetime import datetime, timezone
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = Path(__file__).resolve().parent
SKILL_SRC = Path.home() / ".openclaw" / "skills" / "quantforge-optimizer"

FINAL_RE = re.compile(r"FINAL_OUTPUT:\s*([^\s\"'\\]+)")


_DATE_RANGE_RE = re.compile(
    r"--start\s+\d{4}-\d{2}-\d{2}\s+--end\s+\d{4}-\d{2}-\d{2}"
)


def _sanitize_dates(text: str, train_start: str, train_end: str) -> str:
    """Replace any hardcoded `--start YYYY-MM-DD --end YYYY-MM-DD` snippet
    with the trial's pinned training window. Without this, the agent can
    copy the example dates from SKILL.md / scripts and end up backtesting
    outside the training window — which would breach the air gap.
    """
    return _DATE_RANGE_RE.sub(f"--start {train_start} --end {train_end}", text)


def stage_skill(method_dir, work_root, train_start=None, train_end=None):
    if not SKILL_SRC.exists():
        raise SystemExit(f"Canonical skill missing: {SKILL_SRC}")
    method_skill = method_dir / "SKILL.md"
    if not method_skill.exists():
        raise SystemExit(f"Method SKILL.md missing: {method_skill}")
    staged = work_root / "skill"
    shutil.copytree(SKILL_SRC, staged)
    shutil.copy(method_skill, staged / "SKILL.md")
    log = staged / "knowledge" / "optimization_log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("")

    # Air-gap hardening: rewrite any hardcoded date ranges in SKILL.md and
    # script docstrings so the agent cannot accidentally copy them.
    if train_start and train_end:
        for path in [staged / "SKILL.md",
                     *(staged / "scripts").glob("*.py"),
                     *(staged / "references").glob("*.md")]:
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


def build_prompt(skill_dir, src, work_path, output_path,
                 symbol, timeframe, exchange,
                 train_start, train_end, max_iters, seed):
    return (
        "You are an expert quantitative trading strategy optimizer.\n\n"
        "FROZEN TRAINING WINDOW — every backtest MUST use these dates:\n"
        f"    --start {train_start} --end {train_end}\n"
        "A separate evaluator owns the hidden out-of-sample window.\n"
        "Do NOT backtest outside the training window.\n\n"
        f"Read the closed-loop protocol at {skill_dir}/SKILL.md.\n\n"
        "## Task\n"
        f"- Original (read-only): {src}\n"
        f"- Working copy (edit only this): {work_path}\n"
        f"- Symbol: {symbol}  Timeframe: {timeframe}  Exchange: {exchange}\n\n"
        "## Stop conditions\n"
        f"At most {max_iters} iterations OR Gate 1 passes:\n"
        "  PF > 1.2 AND MaxDD < 15% AND total_trades >= 30.\n\n"
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


def invoke_claude(prompt, model, max_turns, timeout_s, log_path):
    cmd = [
        "claude", "--print", "--verbose",
        "--permission-mode", "bypassPermissions",
        "--output-format", "stream-json",
        "--model", model,
        "--max-turns", str(max_turns),
    ]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(PROJECT_ROOT),
        env=os.environ.copy(),
        text=True, bufsize=1,
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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(EVAL_ROOT / "test_set.yaml"))
    p.add_argument("--method", required=True)
    p.add_argument("--strategy", required=True)
    p.add_argument("--regime", required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--out", required=True)
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
        method_dir, work_root,
        train_start=str(train["start"]),
        train_end=str(train["end"]),
    )

    work_pine = work_root / src.name
    shutil.copy(str(src), str(work_pine))
    out_pine = work_root / "optimized.pine"
    log_path = work_root / "claude_stream.log"

    prompt = build_prompt(
        skill_dir, src, work_pine, out_pine,
        defaults.get("symbol", "BTC/USDT:USDT"),
        defaults.get("timeframe", "1h"),
        defaults.get("exchange", "bitget"),
        str(train["start"]), str(train["end"]),
        int(trial_cfg.get("max_iterations", 5)),
        a.seed,
    )
    started = datetime.now(timezone.utc).isoformat()
    rc, stream = invoke_claude(
        prompt,
        trial_cfg["model"],
        int(trial_cfg.get("max_turns", 80)),
        int(trial_cfg.get("timeout_seconds", 1800)),
        log_path,
    )
    finished = datetime.now(timezone.utc).isoformat()

    final = extract_final(stream)
    optimized = final if (final and Path(final).exists()) else (
        str(out_pine) if out_pine.exists() else None
    )
    cost = extract_cost(stream)
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
        "stream_log": str(log_path),
        "work_dir": str(work_root),
        "optimized_pine": optimized,
        "train_window": {"start": str(train["start"]), "end": str(train["end"])},
        "symbol": defaults.get("symbol"),
        "timeframe": defaults.get("timeframe"),
        "exchange": defaults.get("exchange"),
        "model": trial_cfg["model"],
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(record, indent=2))
    status = "OK" if optimized else "FAIL"
    print(f"[runner:{status}] {trial_id} rc={rc} cost=${cost:.2f}  optimized={optimized!r}")
    return 0 if optimized else 1


if __name__ == "__main__":
    raise SystemExit(main())
