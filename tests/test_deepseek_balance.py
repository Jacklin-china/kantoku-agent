"""Offline balance contract tests; no live provider or billable request."""

import json
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from kantoku.adapters import balance_snapshot
from kantoku.adapters import deepseek_balance
from kantoku.config import ConfigError, ToolError


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, _size):
        return json.dumps(self.payload).encode()


def test_provider_balance_is_returned_as_currency_not_token_count(monkeypatch):
    settings = SimpleNamespace(base_url="https://api.deepseek.com/v1",
                               chat_api_key=lambda: "fake-key")
    monkeypatch.setattr(deepseek_balance, "get_settings", lambda: SimpleNamespace(llm=settings))
    requests = []

    def response(request, timeout):
        requests.append((request.full_url, timeout))
        return FakeResponse({"balance_infos": [{"currency": "CNY", "total_balance": "13.25"}]})

    monkeypatch.setattr(deepseek_balance, "urlopen", response)
    result = deepseek_balance.account_balance()
    assert result == {"platform": "DeepSeek", "balances": [{"currency": "CNY", "amount": "13.25"}]}
    assert requests == [("https://api.deepseek.com/user/balance", 10)]


def test_balance_permission_failure_is_not_zero(monkeypatch):
    settings = SimpleNamespace(base_url="https://api.deepseek.com",
                               chat_api_key=lambda: "fake-key")
    monkeypatch.setattr(deepseek_balance, "get_settings", lambda: SimpleNamespace(llm=settings))

    def denied(request, timeout):
        raise HTTPError(request.full_url, 403, "denied", {}, None)

    monkeypatch.setattr(deepseek_balance, "urlopen", denied)
    with pytest.raises(ToolError, match="查询权限"):
        deepseek_balance.account_balance()


def test_missing_credential_is_reported_as_unconfigured(monkeypatch):
    def missing() -> str:
        raise ConfigError("DEEPSEEK_API_KEY 未配置")

    settings = SimpleNamespace(base_url="https://api.deepseek.com", chat_api_key=missing)
    monkeypatch.setattr(deepseek_balance, "get_settings", lambda: SimpleNamespace(llm=settings))
    snapshot = balance_snapshot(deepseek_balance.account_balance, "DeepSeek")
    assert snapshot["balances"] == []
    assert snapshot["unavailable"]["code"] == "credential_missing"
    assert "未配置" in snapshot["unavailable"]["message"]


def test_network_failure_is_marked_retryable_not_zero(monkeypatch):
    settings = SimpleNamespace(base_url="https://api.deepseek.com",
                               chat_api_key=lambda: "fake-key")
    monkeypatch.setattr(deepseek_balance, "get_settings", lambda: SimpleNamespace(llm=settings))

    def offline(request, timeout):
        raise URLError("connection reset")

    monkeypatch.setattr(deepseek_balance, "urlopen", offline)
    snapshot = balance_snapshot(deepseek_balance.account_balance, "DeepSeek")
    assert snapshot["balances"] == []
    assert snapshot["unavailable"]["code"] == "upstream_error"
