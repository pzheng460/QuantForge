"""Tests for quantforge.cron_helper — managed crontab block."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from quantforge import cron_helper


@pytest.fixture
def fake_crontab(monkeypatch):
    """Replace read_crontab/write_crontab with in-memory storage."""
    state = {"body": ""}

    def fake_read():
        return state["body"]

    def fake_write(body: str):
        state["body"] = body if body.endswith("\n") else body + "\n"

    monkeypatch.setattr(cron_helper, "read_crontab", fake_read)
    monkeypatch.setattr(cron_helper, "write_crontab", fake_write)
    return state


def test_strip_block_is_idempotent(fake_crontab):
    fake_crontab["body"] = (
        "0 * * * * other_user_job\n"
        f"{cron_helper.BEGIN_MARKER}\n"
        "*/30 * * * * managed_command\n"
        f"{cron_helper.END_MARKER}\n"
    )
    stripped = cron_helper._strip_block(fake_crontab["body"])
    assert "managed_command" not in stripped
    assert "other_user_job" in stripped
    # Running it again is a no-op.
    assert cron_helper._strip_block(stripped) == stripped


def test_install_appends_block_and_status_reports_lines(fake_crontab):
    fake_crontab["body"] = "0 * * * * unrelated\n"
    s = cron_helper.install(["ema_crossover"])
    assert s["installed"] is True
    assert len(s["lines"]) == 1
    assert "ema_crossover" in s["lines"][0]
    # User's original line is preserved.
    assert "unrelated" in fake_crontab["body"]


def test_install_replaces_prior_block_idempotently(fake_crontab):
    cron_helper.install(["ema_crossover"])
    cron_helper.install(["bb_squeeze", "ema_crossover"])
    s = cron_helper.status()
    assert s["installed"] is True
    assert len(s["lines"]) == 2
    # Should not have two BEGIN markers (only one block at a time).
    assert fake_crontab["body"].count(cron_helper.BEGIN_MARKER) == 1


def test_install_with_empty_list_removes_block(fake_crontab):
    cron_helper.install(["ema_crossover"])
    assert cron_helper.is_installed() is True
    cron_helper.install([])
    assert cron_helper.is_installed() is False


def test_remove_leaves_user_lines_intact(fake_crontab):
    fake_crontab["body"] = (
        "# my own backup script\n"
        "0 3 * * * /home/me/backup.sh\n"
    )
    cron_helper.install(["ema_crossover"])
    cron_helper.remove()
    assert "backup.sh" in fake_crontab["body"]
    assert cron_helper.BEGIN_MARKER not in fake_crontab["body"]


def test_status_when_not_installed(fake_crontab):
    fake_crontab["body"] = ""
    s = cron_helper.status()
    assert s == {"installed": False, "lines": []}


def test_install_uses_schedule_argument(fake_crontab):
    cron_helper.install(["s1"], schedule="0 9 * * *")
    s = cron_helper.status()
    assert s["lines"][0].startswith("0 9 * * *")


def test_install_threads_webhook_url(fake_crontab):
    cron_helper.install(["s1"], alert_webhook_url="https://hooks.slack.com/X")
    s = cron_helper.status()
    assert "--alert-webhook-url https://hooks.slack.com/X" in s["lines"][0]


def test_read_crontab_returns_empty_on_no_crontab(monkeypatch):
    """`crontab -l` exits 1 + writes 'no crontab' to stderr when there's none."""
    class FakeResult:
        returncode = 1
        stdout = ""
        stderr = "no crontab for user\n"

    def fake_run(*args, **kwargs):
        return FakeResult()

    monkeypatch.setattr("subprocess.run", fake_run)
    assert cron_helper.read_crontab() == ""
