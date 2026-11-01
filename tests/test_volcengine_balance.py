"""Offline tests for the read-only billing balance adapter."""

import json
from types import SimpleNamespace

import pytest

from kantoku.adapters import volcengine_balance
from kantoku.config import ToolError


def test_balance_uses_configured_credentials_and_real_amount(monkeypatch):
    settings = SimpleNamespace(access_key=lambda: "fake-ak", secret_key=lambda: "fake-sk")
    monkeypatch.setattr(volcengine_balance, "get_settings", lambda: SimpleNamespace(video=settings))
    calls = []

    class FakeService:
        def __init__(self, service_info, api_info):
            assert service_info.host == "open.volcengineapi.com"
            assert api_info["balance"].query["Action"] == "QueryBalanceAcct"
            self.session = SimpleNamespace(close=lambda: calls.append(("close",)))

        def set_scheme(self, value): calls.append(("scheme", value))
        def set_ak(self, value): calls.append(("ak", value))
        def set_sk(self, value): calls.append(("sk", value))
        def get(self, api, params):
            assert (api, params) == ("balance", {})
            return json.dumps({"Result": {"AvailableBalance": "42.50"}})

    monkeypatch.setattr(volcengine_balance, "Service", FakeService)
    assert volcengine_balance.account_balance()["balances"] == [
        {"currency": "CNY", "amount": "42.50"},
    ]
    assert ("ak", "fake-ak") in calls and ("sk", "fake-sk") in calls
    assert calls[-1] == ("close",)


def test_invalid_response_does_not_look_like_zero_balance(monkeypatch):
    settings = SimpleNamespace(access_key=lambda: "fake-ak", secret_key=lambda: "fake-sk")
    monkeypatch.setattr(volcengine_balance, "get_settings", lambda: SimpleNamespace(video=settings))

    class FakeService:
        def __init__(self, *_):
            self.session = SimpleNamespace(close=lambda: None)
        def set_scheme(self, _): pass
        def set_ak(self, _): pass
        def set_sk(self, _): pass
        def get(self, *_): return json.dumps({"ResponseMetadata": {"Error": {"Code": "Denied"}}})

    monkeypatch.setattr(volcengine_balance, "Service", FakeService)
    with pytest.raises(ToolError, match="权限"):
        volcengine_balance.account_balance()


def test_balance_prefers_explicit_session_credentials(monkeypatch):
    settings = SimpleNamespace(access_key=lambda: "other-ak", secret_key=lambda: "other-sk")
    monkeypatch.setattr(volcengine_balance, "get_settings", lambda: SimpleNamespace(video=settings))
    seen = []

    class FakeService:
        def __init__(self, *_): self.session = SimpleNamespace(close=lambda: None)
        def set_scheme(self, _): pass
        def set_ak(self, value): seen.append(value)
        def set_sk(self, value): seen.append(value)
        def get(self, *_): return json.dumps({"Result": {"AvailableBalance": "2.50"}})

    monkeypatch.setattr(volcengine_balance, "Service", FakeService)
    assert volcengine_balance.account_balance(credentials=("session-ak", "session-sk"))[
        "balances"
    ][0]["amount"] == "2.50"
    assert seen == ["session-ak", "session-sk"]
