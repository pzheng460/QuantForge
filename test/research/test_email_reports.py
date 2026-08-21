"""Unit tests for the research email delivery layer (config parsing only —
never touches the network)."""
from __future__ import annotations

import os

import pytest

from apps.research import email_reports


def _write_secrets(tmp_path, smtp_block: str) -> str:
    path = tmp_path / "secrets.toml"
    path.write_text("[SMTP]\n" + smtp_block, encoding="utf-8")
    return str(path)


def test_password_with_group_whitespace_is_stripped(monkeypatch, tmp_path):
    """Gmail app passwords are often pasted with group separators (regular
    spaces or NBSP copied from a web page); the loader must strip all
    whitespace so auth does not break on a non-ASCII credential."""
    path = _write_secrets(
        tmp_path,
        'HOST = "smtp.gmail.com"\n'
        'PORT = 587\n'
        'USERNAME = "pzheng460@gmail.com"\n'
        'PASSWORD = "abcd\u00a0efgh\u00a0ijkl\u00a0mnop"\n'
        'FROM = "pzheng460@gmail.com"\n'
        'TO = "pzheng460@gmail.com"\n',
    )
    monkeypatch.setattr(email_reports, "SECRETS", path)
    cfg = email_reports.load_smtp_config()
    assert cfg["password"] == "abcdefghijklmnop"
    assert cfg["password"].isascii()


def test_missing_password_reports_reason(monkeypatch, tmp_path):
    path = _write_secrets(
        tmp_path,
        'HOST = "smtp.gmail.com"\nPORT = 587\n'
        'USERNAME = "pzheng460@gmail.com"\nPASSWORD = ""\n'
        'FROM = "pzheng460@gmail.com"\nTO = "pzheng460@gmail.com"\n',
    )
    monkeypatch.setattr(email_reports, "SECRETS", path)
    with pytest.raises(email_reports.EmailNotConfigured, match="PASSWORD"):
        email_reports.load_smtp_config()


def test_missing_recipients_reports_reason(monkeypatch, tmp_path):
    path = _write_secrets(
        tmp_path,
        'HOST = "smtp.gmail.com"\nPORT = 587\n'
        'USERNAME = "u@gmail.com"\nPASSWORD = "abc"\n'
        'FROM = "u@gmail.com"\nTO = ""\n',
    )
    monkeypatch.setattr(email_reports, "SECRETS", path)
    with pytest.raises(email_reports.EmailNotConfigured, match="TO"):
        email_reports.load_smtp_config()


def test_multiple_recipients_parsed(monkeypatch, tmp_path):
    path = _write_secrets(
        tmp_path,
        'HOST = "smtp.gmail.com"\nPORT = 587\n'
        'USERNAME = "u@gmail.com"\nPASSWORD = "abcdefghijklmnop"\n'
        'FROM = "u@gmail.com"\nTO = "a@gmail.com, b@gmail.com"\n',
    )
    monkeypatch.setattr(email_reports, "SECRETS", path)
    cfg = email_reports.load_smtp_config()
    assert cfg["to"] == ["a@gmail.com", "b@gmail.com"]
