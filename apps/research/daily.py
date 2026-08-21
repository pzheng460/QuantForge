"""Daily research automation: refresh multi-asset data, regenerate the three
research reports, and (optionally) email them to the configured recipients.

Runs every step independently — a failure in one step (e.g. Schwab chain
unavailable) is logged and reported but does not abort the rest. Tied to a
systemd user timer (`research-daily.timer`).
"""
from __future__ import annotations

import os
import traceback
from datetime import date, datetime, timezone

from . import config, warehouse
from .download import fetch_crypto


def _run(name: str, fn) -> None:
    """Run one daily step; record the result string on failure."""
    print(f"\n[{name}]", flush=True)
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        print(f"[{name}] FAILED: {type(exc).__name__}: {exc}", flush=True)


def _today_reports() -> list[str]:
    d = date.today().isoformat()
    paths = [
        os.path.join(config.ROOT, "reports", f"crypto_research_{d}.md"),
        os.path.join(config.ROOT, "reports", f"options_research_{d}.md"),
        os.path.join(config.ROOT, "reports", f"technical_screen_{d}.md"),
    ]
    return [p for p in paths if os.path.exists(p)]


def run_daily(send_email: bool = False) -> dict:
    """Run the daily research pipeline. Returns a summary dict for logging."""
    t0 = datetime.now(timezone.utc)
    step_results: list[str] = []

    def step(name: str, fn) -> None:
        try:
            fn()
            step_results.append(f"{name}: ok")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            step_results.append(f"{name}: FAILED {type(exc).__name__}: {exc}")

    # 1. crypto: refresh OHLCV/funding/OI (idempotent) -> duckdb
    step("crypto download", lambda: (fetch_crypto(), warehouse.import_crypto()))
    # 2. crypto research report
    from . import crypto_research

    step("crypto 报告", crypto_research.build_report)
    # 3. options: live chain snapshot + report (walk the universe, skip bad ticks)
    from . import options_research

    step("options 链+报告", options_research.build_report)
    step("options 入库", warehouse.import_option_chains)
    # 4. technical screen
    from . import technical

    step("technical 报告", lambda: technical.build_report())

    reports = _today_reports()
    summary = {
        "date": date.today().isoformat(),
        "steps": step_results,
        "reports": reports,
        "email": "skipped",
    }

    if send_email:
        from .email_reports import EmailNotConfigured, send_reports

        try:
            body_lines = [
                "QuantForge 每日研究报告（多资产）",
                f"生成时间: {datetime.now(timezone.utc).isoformat()} UTC",
                "",
                "执行步骤:",
                *[f"- {s}" for s in step_results],
                "",
                "附件为 Markdown 源报告（crypto / options / technical）。",
                "更多历史报告见 reports/ 目录；完整证据库见 apps/research/KNOWLEDGE.md。",
            ]
            sent = send_reports(
                f"QuantForge 研究日报 {date.today().isoformat()}",
                reports,
                body="\n".join(body_lines),
            )
            summary["email"] = "ok -> " + ", ".join(sent["to"])
        except EmailNotConfigured as exc:
            summary["email"] = f"未配置（不影响报告生成）: {exc}"
        except Exception as exc:  # noqa: BLE001
            summary["email"] = f"发送失败: {type(exc).__name__}: {exc}"

    elapsed = (datetime.now(timezone.utc) - t0).total_seconds()
    summary["elapsed_s"] = round(elapsed, 1)
    print("\n== daily 汇总 ==")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    return summary


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="research daily")
    ap.add_argument(
        "--email", dest="send_email", action="store_true", default=False,
        help="报告生成后发送邮件（未配置 SMTP 时仅提示，不中断）",
    )
    ap.add_argument(
        "--no-email", dest="no_email", action="store_true",
        help="显式跳过邮件（配合 --email 时优先）",
    )
    args = ap.parse_args(argv)
    run_daily(send_email=args.send_email and not args.no_email)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
