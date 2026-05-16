"""QuantForge-owned auto-tune scheduler CLI."""

from __future__ import annotations

import json

import click

from pathlib import Path

from quantforge.auto_tune_scheduler import AutoTuneJob, load_job_file, run_daemon, run_once


@click.group("auto-tune")
def auto_tune_group():
    """QuantForge-owned scheduler for strategy auto-tune decisions."""


def _job_from_options(**kwargs) -> AutoTuneJob:
    return AutoTuneJob(**kwargs)


COMMON_OPTIONS = [
    click.option("--pine", default=""),
    click.option("--strategy", default=""),
    click.option("--windows", default=""),
    click.option("--symbol", default="BTC/USDT:USDT"),
    click.option("--exchange", default="bitget"),
    click.option("--timeframe", default="1h"),
    click.option("--regime", default="trend_2024h1"),
    click.option("--seeds", default="1"),
    click.option("--providers", default="claude,codex"),
    click.option("--news-file", default=""),
    click.option("--out", default="eval/optimizer_ab/results/auto_tune_report.json"),
    click.option("--history", default="eval/optimizer_ab/results/auto_tune_history.jsonl"),
    click.option("--lock", default="eval/optimizer_ab/results/auto_tune.lock"),
    click.option("--heartbeat", default="eval/optimizer_ab/results/auto_tune_heartbeat.json"),
    click.option("--control-state", default="eval/optimizer_ab/results/trading_control.json"),
    click.option("--state", default="eval/optimizer_ab/results/auto_tune_jobs_state.json"),
    click.option("--runs-dir", default="eval/optimizer_ab/results/auto_tune_runs"),
    click.option("--failed-dir", default="eval/optimizer_ab/results/auto_tune_failed"),
    click.option("--optimizer-results-csv", default="eval/optimizer_ab/results/auto_tune.csv"),
    click.option("--optimizer-trials-dir", default="eval/optimizer_ab/results/auto_tune_trials"),
    click.option("--deploy-metric", default="oos_sharpe"),
    click.option("--registry", default=""),
    click.option("--promotion-report", default="eval/optimizer_ab/results/promotion_pipeline.json"),
    click.option("--shadow-report", default="eval/optimizer_ab/results/shadow_compare.json"),
    click.option("--no-apply-control", "apply_control", flag_value=False, default=True),
    click.option("--execute", is_flag=True, help="Allow eval.auto_tune to launch optimizer when its gate permits."),
    click.option("--auto-deploy", is_flag=True, help="Promote the best optimizer candidate after shadow comparison."),
]


def _apply_common_options(func):
    for option in reversed(COMMON_OPTIONS):
        func = option(func)
    return func


def _resolve_jobs(kwargs):
    job_file = kwargs.pop("job_file", "")
    job_id = kwargs.pop("job_id", "")
    if job_file:
        jobs = load_job_file(Path(job_file))
        if job_id:
            jobs = [j for j in jobs if j.job_id == job_id or j.strategy == job_id]
        if not jobs:
            raise click.ClickException("no enabled auto-tune jobs matched")
        return jobs
    if not kwargs.get("pine") or not kwargs.get("strategy") or not kwargs.get("windows"):
        raise click.ClickException("--pine, --strategy, and --windows are required without --job-file")
    return [_job_from_options(**kwargs)]


@auto_tune_group.command("run-once")
@click.option("--job-file", default="", type=click.Path(exists=True))
@click.option("--job-id", default="")
@_apply_common_options
def run_once_cmd(job_file, job_id, **kwargs):
    """Run one QuantForge-owned auto-tune decision cycle."""
    jobs = _resolve_jobs({"job_file": job_file, "job_id": job_id, **kwargs})
    result = run_once(jobs[0])
    click.echo(json.dumps({
        "returncode": result.returncode,
        "report_path": result.report_path,
        "ran_at": result.ran_at,
        "command": result.command,
    }, indent=2))
    raise SystemExit(result.returncode)


@auto_tune_group.command("daemon")
@click.option("--interval-sec", default=3600, show_default=True, type=int)
@click.option("--max-runs", default=None, type=int)
@click.option("--job-file", default="", type=click.Path(exists=True))
@click.option("--job-id", default="")
@_apply_common_options
def daemon_cmd(interval_sec, max_runs, job_file, job_id, **kwargs):
    """Run QuantForge-owned auto-tune cycles on an internal interval."""
    jobs = _resolve_jobs({"job_file": job_file, "job_id": job_id, **kwargs})
    code = 0
    for job in jobs:
        code = run_daemon(job, interval_sec=interval_sec, max_runs=max_runs)
        if code != 0:
            break
    raise SystemExit(code)
