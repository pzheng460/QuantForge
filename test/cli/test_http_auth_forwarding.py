"""The CLI HTTP helper must forward the operator's QUANTFORGE_API_KEY.

The dashboard requires ``X-API-Key`` whenever it is bound beyond loopback and
auth is installed. CLI commands that manage the running server (``engines``,
``api``, ``strategy run``) talk to exactly that server, so not forwarding the
key would turn every such command into a 401.
"""

from __future__ import annotations

import pytest

import quantforge.cli.commands._http as http


class _FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {}


def test_get_forwards_api_key_header(monkeypatch):
    captured = {}

    def fake_request(method, url, timeout, headers, **kwargs):
        captured.update(method=method, url=url, headers=headers, timeout=timeout)
        return _FakeResponse()

    monkeypatch.setattr(http.requests, "request", fake_request)
    monkeypatch.setenv("QUANTFORGE_API_KEY", "  s3cret  ")
    monkeypatch.setenv("QF_API_URL", "http://127.0.0.1:8000/api")

    http.get("/live/engines")

    assert captured["method"] == "GET"
    assert captured["url"] == "http://127.0.0.1:8000/api/live/engines"
    assert captured["headers"] == {"X-API-Key": "s3cret"}
    assert captured["timeout"] == 10


def test_post_forwards_trimmed_api_key(monkeypatch):
    captured = {}

    def fake_request(method, url, timeout, headers, **kwargs):
        captured.update(method=method, headers=headers)
        return _FakeResponse()

    monkeypatch.setattr(http.requests, "request", fake_request)
    monkeypatch.setenv("QUANTFORGE_API_KEY", "sekret\n")
    http.post("/live/stop/x")
    assert captured["method"] == "POST"
    assert captured["headers"] == {"X-API-Key": "sekret"}


def test_whitespace_only_key_is_not_forwarded(monkeypatch):
    captured = {}

    def fake_request(method, url, timeout, headers, **kwargs):
        captured.update(headers=headers)
        return _FakeResponse()

    monkeypatch.setattr(http.requests, "request", fake_request)
    monkeypatch.setenv("QUANTFORGE_API_KEY", "   ")
    http.get("/live/engines")
    assert captured["headers"] == {}


def test_no_key_env_sends_no_auth_header(monkeypatch):
    captured = {}

    def fake_request(method, url, timeout, headers, **kwargs):
        captured.update(headers=headers)
        return _FakeResponse()

    monkeypatch.setattr(http.requests, "request", fake_request)
    monkeypatch.delenv("QUANTFORGE_API_KEY", raising=False)
    http.get("/live/engines")
    assert captured["headers"] == {}


def test_caller_headers_are_merged_not_overwritten(monkeypatch):
    captured = {}

    def fake_request(method, url, timeout, headers, **kwargs):
        captured.update(headers=headers)
        return _FakeResponse()

    monkeypatch.setattr(http.requests, "request", fake_request)
    monkeypatch.setenv("QUANTFORGE_API_KEY", "sekret")
    http.post("/live/start", headers={"X-Custom": "1"})
    assert captured["headers"]["X-API-Key"] == "sekret"
    assert captured["headers"]["X-Custom"] == "1"


def test_connection_error_raises_server_unreachable(monkeypatch):
    import requests

    def fake_request(*args, **kwargs):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(http.requests, "request", fake_request)
    with pytest.raises(http.ServerUnreachable):
        http.get("/live/engines")
