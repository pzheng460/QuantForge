"""Bidirectional agent review for optimizer A/B trials.

This stage reads completed Claude/Codex optimizer trials, pairs matching cells,
and asks each provider to review the other provider's optimized Pine output.
The review is intentionally read-only: reviewers can inspect code, logs, and
visible fit/validation windows, then emit structured improvement factors.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import time
from pathlib import Path

from quantforge.agent_providers import build_agent_command, resolve_model

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REVIEW_RE = re.compile(r"REVIEW_OUTPUT:\s*")


def read_rows(csv_path):
    with open(csv_path) as f:
        return list(csv.DictReader(f))


def _cell_key(row):
    return (
        row.get("method"),
        row.get("strategy_name"),
        row.get("regime"),
        str(row.get("seed")),
    )


def pair_provider_trials(rows, provider_a, provider_b):
    by_key = {}
    for row in rows:
        by_key.setdefault(_cell_key(row), {})[row.get("agent_provider")] = row
    pairs = []
    for providers in by_key.values():
        if provider_a in providers and provider_b in providers:
            pairs.append((providers[provider_a], providers[provider_b]))
    return pairs


def load_trial(row_or_path):
    path = row_or_path if isinstance(row_or_path, (str, Path)) else row_or_path["trial_json"]
    return json.loads(Path(path).read_text())


def build_review_prompt(trial, reviewer_provider):
    split = trial.get("internal_split") or {}
    holdout = trial.get("holdout") or {}
    return (
        "You are performing a read-only cross-review of another agent's "
        "QuantForge Pine strategy optimization.\n\n"
        "Do not edit files. Do not reveal or infer any hidden holdout beyond "
        "the metrics already present in the trial JSON. You may inspect the "
        "optimized Pine file, the stream log, and rerun visible training-window "
        "fit/validation backtests if needed.\n\n"
        f"Reviewer provider: {reviewer_provider}\n"
        f"Reviewed provider: {trial.get('agent_provider')}\n"
        f"Trial id: {trial.get('trial_id')}\n"
        f"Optimized Pine: {trial.get('optimized_pine')}\n"
        f"Stream log: {trial.get('stream_log')}\n"
        f"Fit window: {split.get('fit_start')} to {split.get('fit_end')}\n"
        f"Validation window: {split.get('validation_start')} to {split.get('validation_end')}\n"
        f"Holdout metrics already recorded: {json.dumps(holdout, sort_keys=True)}\n\n"
        "Review goals:\n"
        "1. Identify the most plausible improvement factors that explain or "
        "could improve this candidate.\n"
        "2. Flag overfit, low trade count, hidden leakage risk, missing risk "
        "controls, or weak validation evidence.\n"
        "3. Prefer concrete factors that can become next optimization knobs: "
        "parameter region, regime filter, stop/exit logic, direction filter, "
        "position sizing, or validation protocol change.\n\n"
        "Return exactly one line beginning with REVIEW_OUTPUT: followed by a "
        "single JSON object with this schema:\n"
        "{\n"
        '  "decision": "accept|reject|needs_retest",\n'
        '  "robustness_score": 0,\n'
        '  "overfit_risk": 0,\n'
        '  "improvement_factors": [\n'
        '    {"factor": "...", "evidence": "...", "expected_effect": "..."}\n'
        "  ],\n"
        '  "blocking_issues": ["..."]\n'
        "}\n"
    )


def _message_texts(stream):
    for line in stream.splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "item.completed":
            item = obj.get("item") or {}
            if item.get("type") == "agent_message" and item.get("text"):
                yield item["text"]
        if obj.get("type") == "result" and obj.get("result"):
            yield obj["result"]
        if obj.get("type") == "assistant":
            for item in (obj.get("message") or {}).get("content", []):
                if item.get("type") == "text" and item.get("text"):
                    yield item["text"]


def _decode_review_after_sentinel(text):
    match = REVIEW_RE.search(text)
    if not match:
        return None
    payload = text[match.end():].strip()
    try:
        obj, _ = json.JSONDecoder().raw_decode(payload)
        return obj
    except json.JSONDecodeError:
        return None


def extract_review_output(stream):
    for text in [*_message_texts(stream), stream]:
        parsed = _decode_review_after_sentinel(text)
        if parsed is not None:
            return parsed
    return None


def invoke_reviewer(prompt, provider, model, max_turns, timeout_s, log_path):
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
                logf.write("\n[harness] REVIEW_TIMEOUT\n")
                return 124, "".join(chunks)
            line = proc.stdout.readline()
            if not line:
                time.sleep(0.05)
                continue
            chunks.append(line)
            logf.write(line)
            logf.flush()
    return proc.returncode or 0, "".join(chunks)


def review_trial(trial_path, reviewer_provider, out_dir, model=None,
                 max_turns=40, timeout_s=900):
    trial_path = Path(trial_path)
    trial = load_trial(trial_path)
    reviewer_model = resolve_model(reviewer_provider, model)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"{trial_path.stem}__reviewed_by_{reviewer_provider}"
    log_path = out_dir / f"{base}.log"
    json_path = out_dir / f"{base}.json"
    prompt = build_review_prompt(trial, reviewer_provider)
    rc, stream = invoke_reviewer(
        prompt,
        reviewer_provider,
        reviewer_model,
        max_turns,
        timeout_s,
        log_path,
    )
    parsed = extract_review_output(stream)
    record = {
        "trial_id": trial.get("trial_id"),
        "reviewed_provider": trial.get("agent_provider"),
        "reviewer_provider": reviewer_provider,
        "reviewer_model": reviewer_model,
        "returncode": rc,
        "review_log": str(log_path),
        "review": parsed,
    }
    json_path.write_text(json.dumps(record, indent=2))
    return record, json_path


def summarize_review_records(records):
    rows = []
    for rec in records:
        review = rec.get("review") or {}
        factors = review.get("improvement_factors") or []
        if not factors:
            factors = [{}]
        for factor in factors:
            rows.append({
                "trial_id": rec.get("trial_id"),
                "reviewed_provider": rec.get("reviewed_provider"),
                "reviewer_provider": rec.get("reviewer_provider"),
                "decision": review.get("decision"),
                "robustness_score": review.get("robustness_score"),
                "overfit_risk": review.get("overfit_risk"),
                "factor": factor.get("factor"),
                "evidence": factor.get("evidence"),
                "expected_effect": factor.get("expected_effect"),
            })
    return rows


def write_factor_summary(records, summary_csv):
    rows = summarize_review_records(records)
    fieldnames = [
        "trial_id", "reviewed_provider", "reviewer_provider", "decision",
        "robustness_score", "overfit_risk", "factor", "evidence",
        "expected_effect",
    ]
    summary_csv = Path(summary_csv)
    summary_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def run_cross_review(csv_path, provider_a, provider_b, out_dir,
                     model=None, provider_models=None,
                     max_turns=40, timeout_s=900, dry_run=False,
                     summary_csv=None):
    rows = read_rows(csv_path)
    outputs = []
    records = []
    for left, right in pair_provider_trials(rows, provider_a, provider_b):
        for reviewed, reviewer in ((left, provider_b), (right, provider_a)):
            trial = load_trial(reviewed)
            out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            base = f"{Path(reviewed['trial_json']).stem}__reviewed_by_{reviewer}"
            if dry_run:
                prompt_path = out_dir / f"{base}.prompt.txt"
                prompt_path.write_text(build_review_prompt(trial, reviewer))
                outputs.append(prompt_path)
                continue
            record, json_path = review_trial(
                reviewed["trial_json"],
                reviewer,
                out_dir,
                model=(provider_models or {}).get(reviewer, model),
                max_turns=max_turns,
                timeout_s=timeout_s,
            )
            records.append(record)
            outputs.append(json_path)
    if summary_csv and records:
        write_factor_summary(records, summary_csv)
    return outputs


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--providers", default="claude,codex",
                   help="Two providers to cross-review, e.g. claude,codex.")
    p.add_argument("--out-dir", default="eval/optimizer_ab/results/cross_reviews")
    p.add_argument("--summary-csv", default="",
                   help="Optional CSV that flattens improvement_factors.")
    p.add_argument("--model", default=None)
    p.add_argument("--claude-model", default=None)
    p.add_argument("--codex-model", default=None)
    p.add_argument("--max-turns", type=int, default=40)
    p.add_argument("--timeout-seconds", type=int, default=900)
    p.add_argument("--dry-run", action="store_true",
                   help="Write review prompts without invoking agents.")
    a = p.parse_args()

    providers = [x.strip() for x in a.providers.split(",") if x.strip()]
    if len(providers) != 2:
        print("--providers expects exactly two providers, e.g. claude,codex")
        return 2
    outputs = run_cross_review(
        a.csv,
        providers[0],
        providers[1],
        a.out_dir,
        model=a.model,
        provider_models={"claude": a.claude_model, "codex": a.codex_model},
        max_turns=a.max_turns,
        timeout_s=a.timeout_seconds,
        dry_run=a.dry_run,
        summary_csv=a.summary_csv or None,
    )
    print(f"[cross-review] wrote {len(outputs)} file(s) to {a.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
