"""Email delivery for research reports (SMTP).

Credentials live in `.keys/.secrets.toml` under a `[SMTP]` section (the same
0600 secrets file every other app credential uses):

    [SMTP]
    HOST     = "smtp.gmail.com"
    PORT     = 587
    USERNAME = "sender@gmail.com"
    PASSWORD = "...app-specific password..."
    FROM     = "sender@gmail.com"
    TO       = "pzheng460@gmail.com"   # comma-separated for multiple recipients

Gmail needs an App Password (2FA) rather than the account password; generate
one at https://myaccount.google.com/apppasswords and put it in PASSWORD.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path
from typing import Any

import tomllib

from . import config

SECRETS = os.path.join(config.ROOT, ".keys", ".secrets.toml")

DEFAULT_RECIPIENTS = ["pzheng460@gmail.com"]


class EmailNotConfigured(RuntimeError):
    """Raised when SMTP delivery is requested but the secrets file is incomplete."""


def load_smtp_config() -> dict[str, Any]:
    """Read the [SMTP] section; raise EmailNotConfigured with a concrete reason."""
    try:
        with open(SECRETS, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        raise EmailNotConfigured(f"{SECRETS} 不存在")

    sm = data.get("SMTP") or {}
    missing = [
        k for k in ("HOST", "PORT", "USERNAME", "PASSWORD", "FROM")
        if not str(sm.get(k, "")).strip()
    ]
    if missing:
        raise EmailNotConfigured(
            f"{SECRETS} 的 [SMTP] 缺少: {', '.join(missing)}（PASSWORD 用 Gmail 应用专用密码）"
        )
    to = [t.strip() for t in str(sm.get("TO", "")).split(",") if t.strip()]
    if not to:
        raise EmailNotConfigured(
            f"{SECRETS} 的 [SMTP] TO 收件人为空（示例: TO = \"{DEFAULT_RECIPIENTS[0]}\"）"
        )
    return {
        "host": str(sm["HOST"]).strip(),
        "port": int(sm["PORT"]),
        "username": str(sm["USERNAME"]).strip(),
        # App passwords are often pasted with group separators
        # ("xxxx xxxx xxxx xxxx" incl. NBSP from a web page) — drop all
        # whitespace; Gmail app passwords are 16 accents-free letters.
        "password": "".join(ch for ch in str(sm["PASSWORD"]) if not ch.isspace()),
        "from_addr": str(sm["FROM"]).strip(),
        "to": to,
    }


def send_reports(
    subject: str,
    report_paths: list[str],
    body: str | None = None,
) -> dict[str, Any]:
    """Deliver one email with the given markdown reports attached.

    Raises EmailNotConfigured (config) or smtplib errors (delivery); callers in
    the daily job treat failures as non-fatal.
    """
    cfg = load_smtp_config()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from_addr"]
    msg["To"] = ", ".join(cfg["to"])
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(body or "QuantForge 每日研究报告，附件为 Markdown 源文件。")

    attached = []
    for p in report_paths:
        path = Path(p)
        if not path.exists():
            continue
        msg.add_attachment(
            path.read_bytes(), maintype="text", subtype="markdown", filename=path.name
        )
        attached.append(path.name)

    ctx = ssl.create_default_context()
    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=40) as server:
        server.starttls(context=ctx)
        server.login(cfg["username"], cfg["password"])
        server.send_message(msg)
    return {"to": cfg["to"], "attachments": attached, "subject": subject}


def _config_status() -> str:
    try:
        cfg = load_smtp_config()
        return (
            f"已配置 -> 发件人 {cfg['from_addr']} | 收件人 {', '.join(cfg['to'])}\n"
            f"SMTP {cfg['host']}:{cfg['port']}"
        )
    except EmailNotConfigured as exc:
        return f"未配置: {exc}"


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="research email")
    ap.add_argument("action", choices=["config", "test"])
    args = ap.parse_args(argv)
    if args.action == "config":
        print(_config_status())
        print(f"编辑文件: {SECRETS}")
        return 0
    body = (
        "QuantForge 邮件通道测试。\n\n"
        "若收到此信，说明 [SMTP] 配置正确，每日研究报告将以此通道推送。"
    )
    try:
        sent = send_reports("QuantForge Research — 邮件通道测试", [], body=body)
        print("发送成功 ->", ", ".join(sent["to"]))
        return 0
    except EmailNotConfigured as exc:
        print(f"未发送: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"发送失败: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
