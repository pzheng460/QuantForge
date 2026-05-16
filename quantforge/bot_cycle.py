"""One-shot autonomous bot cycle for cron and manual operations."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quantforge.audit_report import build_audit_report
from quantforge.alerts import emit_alert, should_alert_cycle
from quantforge.auto_tune_scheduler import AutoTuneJob, PromotionRunner, Runner, load_job_file, run_once
from quantforge.bot_preflight import run_bot_preflight
from quantforge.bot_status import build_bot_status, write_bot_status
from quantforge.risk_control import RiskConfig, check_risk


def run_bot_cycle(
    strategy_id: str,
    *,
    job_file: str | Path,
    mode: str = "paper",
    policy_path: str | Path | None = None,
    request_path: str | Path | None = None,
    approvals_path: str | Path | None = None,
    registry_path: str | Path | None = None,
    ledger_path: str | Path | None = None,
    cron_file: str | Path | None = None,
    require_promoted: bool = False,
    risk_role: str | None = None,
    auto_rollback: bool = False,
    risk_json_out: str | Path | None = None,
    audit_json_out: str | Path | None = None,
    audit_markdown_out: str | Path | None = None,
    status_json_out: str | Path | None = None,
    cycle_json_out: str | Path | None = None,
    alert_jsonl_path: str | Path | None = None,
    alert_webhook_url: str | None = None,
    alert_on_success: bool = False,
    runner: Runner | None = None,
    promotion_runner: PromotionRunner | None = None,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    subprocess_log: dict[str, Any] = {"captured": False}

    preflight = run_bot_preflight(
        strategy_id,
        mode=mode,
        job_file=job_file,
        policy_path=policy_path,
        request_path=request_path,
        approvals_path=approvals_path,
        registry_path=registry_path,
        cron_file=cron_file,
        require_promoted=require_promoted,
    )
    if not preflight["passed"]:
        report = _cycle_report(
            strategy_id,
            mode=mode,
            passed=False,
            phase="preflight",
            returncode=2,
            preflight=preflight,
            auto_tune=None,
        )
        _maybe_emit_cycle_alert(
            report,
            alert_jsonl_path=alert_jsonl_path,
            alert_webhook_url=alert_webhook_url,
            alert_on_success=alert_on_success,
        )
        _write_optional(cycle_json_out, report)
        return report

    job = _select_job(strategy_id, job_file)
    effective_runner = runner
    if effective_runner is None:
        captured: list[subprocess.CompletedProcess[str]] = []

        def effective_runner(command: list[str], workdir: str):
            result = run_quiet_subprocess(command, workdir)
            captured.append(result)
            return result

        subprocess_log = {"captured": True, "runs": []}
    else:
        captured = []

    result = run_once(job, runner=effective_runner, promotion_runner=promotion_runner, cwd=cwd)
    auto_tune = asdict(result)
    if captured:
        subprocess_log["runs"] = [_subprocess_result_dict(item) for item in captured]
    risk = None
    if risk_role:
        risk = check_risk(
            strategy_id,
            role=risk_role,
            ledger_path=ledger_path,
            control_state=job.control_state,
            registry_path=registry_path or job.registry or None,
            auto_rollback=auto_rollback,
            out=risk_json_out,
            config=RiskConfig(),
        )

    audit = build_audit_report(
        strategy_id,
        auto_tune_path=result.report_path,
        promotion_path=result.deployment_report_path or None,
        shadow_path=job.shadow_report,
        risk_path=risk_json_out,
        json_out=audit_json_out,
        markdown_out=audit_markdown_out,
    )
    status = build_bot_status(
        strategy_id,
        registry_path=registry_path or job.registry or None,
        ledger_path=ledger_path,
        control_state_path=job.control_state,
        approvals_path=approvals_path,
        auto_tune_state_path=job.state,
        risk_report_path=risk_json_out,
        audit_report_path=audit_json_out,
    )
    if status_json_out:
        write_bot_status(status, status_json_out)

    risk_failed = bool(risk and risk.get("decision", {}).get("action") != "observe")
    passed = result.returncode == 0 and not risk_failed
    report = _cycle_report(
        strategy_id,
        mode=mode,
        passed=passed,
        phase="completed" if passed else "risk" if risk_failed else "auto_tune",
        returncode=0 if passed else 2 if risk_failed else result.returncode,
        preflight=preflight,
        auto_tune=auto_tune,
        subprocess_log=subprocess_log,
        risk=risk,
        audit=audit,
        status=status,
    )
    _write_optional(cycle_json_out, report)
    _maybe_emit_cycle_alert(
        report,
        alert_jsonl_path=alert_jsonl_path,
        alert_webhook_url=alert_webhook_url,
        alert_on_success=alert_on_success,
    )
    return report


def _select_job(strategy_id: str, job_file: str | Path) -> AutoTuneJob:
    for job in load_job_file(job_file):
        if job.strategy == strategy_id or job.job_id == strategy_id:
            return job
    raise ValueError(f"no enabled auto-tune job matched {strategy_id}")


def _cycle_report(
    strategy_id: str,
    *,
    mode: str,
    passed: bool,
    phase: str,
    returncode: int,
    preflight: dict[str, Any],
    auto_tune: dict[str, Any] | None,
    subprocess_log: dict[str, Any] | None = None,
    risk: dict[str, Any] | None = None,
    audit: dict[str, Any] | None = None,
    status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "strategy_id": strategy_id,
        "mode": mode,
        "passed": passed,
        "phase": phase,
        "returncode": returncode,
        "preflight": preflight,
        "auto_tune": auto_tune,
        "subprocess": subprocess_log or {"captured": False},
        "risk": risk,
        "audit": audit,
        "status": status,
    }


def run_quiet_subprocess(command: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True)


def _maybe_emit_cycle_alert(
    report: dict[str, Any],
    *,
    alert_jsonl_path: str | Path | None,
    alert_webhook_url: str | None,
    alert_on_success: bool,
) -> None:
    should_alert, severity, event_type = should_alert_cycle(report)
    if not should_alert and not alert_on_success:
        return
    emit_alert(
        event_type,
        report["strategy_id"],
        severity=severity,
        payload={
            "phase": report["phase"],
            "returncode": report["returncode"],
            "mode": report["mode"],
            "passed": report["passed"],
            "preflight_errors": (report.get("preflight") or {}).get("errors", []),
            "risk_decision": ((report.get("risk") or {}).get("decision") or {}),
        },
        jsonl_path=alert_jsonl_path,
        webhook_url=alert_webhook_url,
    )


def _subprocess_result_dict(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _write_optional(path: str | Path | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True))
