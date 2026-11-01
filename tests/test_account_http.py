"""Exercise the current HTTP transport with an isolated local account."""

import base64
import io
import json
import os
import subprocess
import sys
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image
from test_auth_http import request
from test_web_studio import app, server  # noqa: F401

from kantoku.capabilities.video.xiaoyunque import XiaoyunqueVideoProvider
from kantoku.config import ConfigError, logging_setup
from kantoku.config import settings as settings_module
from kantoku.shells import web_studio


def _authorized_headers(server: int, token: str) -> dict:  # noqa: F811
    credentials = json.dumps({"username": "user1", "password": "password-for-test"})
    login_headers = {"Content-Type": "application/json", "X-Kantoku-Login": "1"}
    status, headers, _ = request(server, "POST", "/api/auth/register", credentials, login_headers)
    assert status == 200
    cookie = headers["Set-Cookie"].split(";")[0]
    return {"X-Studio-Token": token, "Cookie": cookie, "Content-Type": "application/json"}


@pytest.mark.skipif(os.name != "nt", reason="Windows credential manager")
def test_text_key_persists_without_echo_and_other_providers_rejected(app, server, monkeypatch):  # noqa: F811
    import ctypes

    name = "KANTOKU_TEST_" + uuid4().hex.upper()
    monkeypatch.setattr(settings_module, "ROOT", settings_module.ROOT / uuid4().hex)
    fake = SimpleNamespace(
        llm=SimpleNamespace(api_key_env=name,
                            chat_api_key=lambda: settings_module._read_managed_key(name)),
        image=SimpleNamespace(provider="alibaba-qwen-image", api_key_env="IMAGE_KEY",
                              access_key_env="IMAGE_AK", secret_key_env="IMAGE_SK",
                              api_key=lambda: "example-image-key"),
        video=SimpleNamespace(authentication="access_key", api_key_env="VIDEO_KEY",
                              access_key_env="VIDEO_AK", secret_key_env="VIDEO_SK",
                              access_key=lambda: "example-video-id",
                              secret_key=lambda: "example-video-key"),
    )
    monkeypatch.setattr(web_studio, "get_settings", lambda: fake)
    headers = _authorized_headers(server, app.token)
    try:
        body = json.dumps({"provider": "text", "fields": {"api_key": "example-secret-key"}})
        token_headers = {"X-Studio-Token": app.token, "Content-Type": "application/json"}
        status, _, payload = request(server, "POST", "/api/account/provider-credentials", body,
                                     token_headers)
        assert status != 200
        status, _, payload = request(server, "POST", "/api/account/provider-credentials", body,
                                     headers)
        assert status == 200 and "example-secret-key" not in str(payload)
        assert settings_module._read_managed_key(name) == "example-secret-key"
        code = (
            "import pathlib,sys; from types import SimpleNamespace as S; "
            "from kantoku.config import settings; "
            "settings.ROOT=pathlib.Path(sys.argv[1]); "
            "settings.get_settings=lambda:S(llm=S(api_key_env=sys.argv[2]), "
            "image=S(api_key_env='I',access_key_env='IA',secret_key_env='IS'), "
            "video=S(api_key_env='V',access_key_env='VA',secret_key_env='VS')); "
            "assert settings._require_env(sys.argv[2]) == 'example-secret-key'"
        )
        result = subprocess.run([sys.executable, "-B", "-c", code, str(settings_module.ROOT), name],
                                capture_output=True, timeout=30, check=False)
        assert result.returncode == 0
        denied = json.dumps({"provider": "image", "fields": {"api_key": "different-secret"}})
        assert request(server, "POST", "/api/account/provider-credentials", denied,
                       headers)[0] != 200
        monkeypatch.setenv(name, "external-secret")
        assert request(server, "POST", "/api/account/provider-credentials", body, headers)[0] != 200
        assert settings_module._read_managed_key(name) == "example-secret-key"
    finally:
        api = ctypes.WinDLL("advapi32", use_last_error=True)
        delete = api.CredDeleteW
        delete.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong]
        delete.restype = ctypes.c_int
        assert delete(settings_module._credential_target(name), 1, 0)


def test_managed_key_precedence_isolation_and_redaction(monkeypatch, tmp_path):
    name = "KANTOKU_TEST_TEXT_KEY"
    monkeypatch.delenv(name, raising=False)
    fake = SimpleNamespace(
        llm=SimpleNamespace(api_key_env=name),
        image=SimpleNamespace(api_key_env="IMAGE", access_key_env="IMAGE_AK",
                              secret_key_env="IMAGE_SK"),
        video=SimpleNamespace(api_key_env="VIDEO", access_key_env="VIDEO_AK",
                              secret_key_env="VIDEO_SK"),
    )
    env_file = tmp_path / "config.env"
    env_file.write_text(f"{name}=original-example-key\n", encoding="utf-8")
    monkeypatch.setattr(settings_module, "ENV_PATH", env_file)
    monkeypatch.setattr(settings_module, "get_settings", lambda: fake)
    monkeypatch.setattr(settings_module, "_read_managed_key", lambda _name: "vault-example-key")
    monkeypatch.setattr(logging_setup, "get_settings", lambda: fake)
    monkeypatch.setattr(logging_setup, "_read_managed_key", lambda _name: "vault-example-key")
    assert settings_module._require_env(name) == "vault-example-key"
    assert "vault-example-key" not in logging_setup.redact_secrets("vault-example-key")
    monkeypatch.setattr(logging_setup, "_read_managed_key", lambda _name: "next-example-key")
    assert "vault-example-key" not in logging_setup.redact_secrets("vault-example-key")
    monkeypatch.setenv(name, "environment-example-key")
    assert settings_module._require_env(name) == "environment-example-key"
    assert settings_module.managed_text_key_allowed(fake, name)
    fake.image.api_key_env = name
    assert not settings_module.managed_text_key_allowed(fake, name)


def test_provider_credentials_require_account_and_never_echo_values(app, server, monkeypatch):  # noqa: F811
    def missing():
        raise ConfigError("credential missing")

    monkeypatch.setattr(web_studio, "get_settings", lambda: SimpleNamespace(
        llm=SimpleNamespace(chat_api_key=lambda: "fake-text-secret"),
        image=SimpleNamespace(provider="alibaba-qwen-image", api_key=missing),
        video=SimpleNamespace(authentication="access_key", access_key=lambda: "fake-id",
                              secret_key=lambda: "fake-video-secret"),
    ))
    token = {"X-Studio-Token": app.token}
    assert request(server, "GET", "/api/account/provider-credentials", headers=token)[0] != 200
    headers = _authorized_headers(server, app.token)
    status, _, payload = request(
        server, "GET", "/api/account/provider-credentials", headers=headers,
    )
    assert status == 200
    result = json.loads(payload)
    assert result["editable"] is False
    providers = {item["id"]: item for item in result["providers"]}
    assert providers["text"]["fields"][0]["configured"] is True
    assert providers["image"]["fields"][0]["configured"] is False
    assert all(field["configured"] for field in providers["video"]["fields"])
    secrets = ("fake-text-secret", "fake-id", "fake-video-secret")
    assert all(secret not in str(payload) for secret in secrets)


def test_video_balance_uses_session_and_refuses_changed_cached_account(app, server, monkeypatch):  # noqa: F811
    provider = XiaoyunqueVideoProvider(SimpleNamespace(
        authentication="access_key", access_key=lambda: "environment-ak",
        secret_key=lambda: "environment-sk",
    ))
    provider._credentials = ("session-ak", "session-sk")
    app.comic_videos.capability.provider = provider
    calls = []
    monkeypatch.setattr(web_studio, "volcengine_balance", lambda **kwargs: (
        calls.append(kwargs.get("credentials"))
        or {"balances": [{"amount": "3", "currency": "CNY"}]}
    ))
    headers = _authorized_headers(server, app.token)
    monkeypatch.setattr(web_studio, "get_settings", lambda: SimpleNamespace(
        llm=SimpleNamespace(chat_api_key=lambda: "text-secret"),
        image=SimpleNamespace(provider="alibaba-qwen-image", api_key=lambda: "image-secret"),
        video=provider.settings,
    ))
    status, _, payload = request(
        server, "GET", "/api/account/provider-credentials", headers=headers,
    )
    video = next(item for item in json.loads(payload)["providers"] if item["id"] == "video")
    assert status == 200 and video["session_credentials_configured"] is True
    assert video["credentials_configurable"] is False
    assert "session-sk" not in str(payload)
    status, _, payload = request(server, "GET", "/api/account/creative-balance", headers=headers)
    assert status == 200 and json.loads(payload)["balances"][0]["amount"] == "3"
    assert calls == [("session-ak", "session-sk")]
    provider._client = object()
    provider._client_credentials = ("different-ak", "different-sk")
    status, _, payload = request(server, "GET", "/api/account/creative-balance", headers=headers)
    assert status == 200 and json.loads(payload)["unavailable"]["code"] == "service_mismatch"
    assert len(calls) == 1
    assert "session-sk" not in str(payload) and "different-sk" not in str(payload)
    provider._credentials = None
    provider._client = None
    status, _, payload = request(server, "GET", "/api/account/creative-balance", headers=headers)
    assert status == 200 and json.loads(payload)["balances"][0]["amount"] == "3"
    assert calls[-1] is None
    provider._client = object()
    provider._client_credentials = ("environment-ak", "environment-sk")
    status, _, payload = request(server, "GET", "/api/account/creative-balance", headers=headers)
    assert status == 200 and json.loads(payload)["balances"][0]["amount"] == "3"
    provider._client_credentials = ("previous-ak", "previous-sk")
    status, _, payload = request(server, "GET", "/api/account/creative-balance", headers=headers)
    assert status == 200 and json.loads(payload)["unavailable"]["code"] == "service_mismatch"


def test_account_profile_requires_login_and_password_change(app, server):  # noqa: F811
    token = {"X-Studio-Token": app.token}
    assert request(server, "GET", "/api/account/profile", headers=token)[0] == 400
    credentials = json.dumps({"username": "user1", "password": "password-for-test"})
    login_headers = {"Content-Type": "application/json", "X-Kantoku-Login": "1"}
    status, headers, _ = request(server, "POST", "/api/auth/register", credentials, login_headers)
    assert status == 200
    cookie = headers["Set-Cookie"].split(";")[0]
    authorized = {**token, "Cookie": cookie, "Content-Type": "application/json"}
    status, _, payload = request(server, "GET", "/api/account/profile", headers=authorized)
    assert status == 200 and json.loads(payload)["username"] == "user1"
    status, _, payload = request(server, "POST", "/api/account/profile", json.dumps({
        "nickname": "新昵称", "avatar": None,
    }), authorized)
    assert status == 200 and json.loads(payload)["nickname"] == "新昵称"
    status, _, _ = request(server, "POST", "/api/account/password", json.dumps({
        "old_password": "bad-password", "new_password": "new-password",
    }), authorized)
    assert status == 400
    status, _, _ = request(server, "POST", "/api/account/password", json.dumps({
        "old_password": "password-for-test", "new_password": "new-password",
    }), authorized)
    assert status == 200
    assert request(server, "GET", "/api/account/profile", headers=authorized)[0] == 200


def test_avatar_upload_and_removal_round_trip(app, server):  # noqa: F811
    authorized = _authorized_headers(server, app.token)
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    avatar = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

    status, _, payload = request(server, "POST", "/api/account/profile", json.dumps({
        "nickname": "头像用户", "avatar": avatar,
    }), authorized)
    assert status == 200 and json.loads(payload)["avatar"] == avatar

    # 不带 remove_avatar 时沿用历史语义：保留既有头像。
    status, _, payload = request(server, "POST", "/api/account/profile", json.dumps({
        "nickname": "头像用户", "avatar": None,
    }), authorized)
    assert status == 200 and json.loads(payload)["avatar"] == avatar

    status, _, payload = request(server, "POST", "/api/account/profile", json.dumps({
        "nickname": "头像用户", "avatar": None, "remove_avatar": True,
    }), authorized)
    assert status == 200 and json.loads(payload)["avatar"] is None
    _, _, payload = request(server, "GET", "/api/account/profile", headers=authorized)
    assert json.loads(payload)["avatar"] is None


def test_legacy_server_keeps_account_routes_and_auth(app, monkeypatch):  # noqa: F811
    monkeypatch.setattr(app, "provider_credentials_status", lambda: {"editable": False})
    monkeypatch.setattr(app, "creative_balance", lambda: {"balances": [{"amount": "7"}]})
    saved = []
    monkeypatch.setattr(app, "save_provider_credentials", lambda body: saved.append(body) or {})
    legacy = web_studio.make_legacy_server(app)
    thread = threading.Thread(target=legacy.serve_forever, daemon=True)
    thread.start()
    try:
        port = legacy.server_port
        token = {"X-Studio-Token": app.token, "Content-Type": "application/json"}
        key_body = json.dumps({"provider": "text", "fields": {"api_key": "test-only-secret"}})
        assert request(port, "POST", "/api/account/provider-credentials", key_body, token)[0] == 401
        assert not saved
        credentials = json.dumps({"username": "user1", "password": "password-for-test"})
        status, headers, _ = request(port, "POST", "/api/auth/register", credentials, {
            "Content-Type": "application/json", "X-Kantoku-Login": "1",
        })
        assert status == 200
        cookie = headers["Set-Cookie"].split(";")[0]
        assert request(port, "GET", "/api/account/profile", headers=token)[0] == 401
        assert request(port, "GET", "/api/account/provider-credentials", headers=token)[0] == 401
        headers = {**token, "Cookie": cookie, "Content-Type": "application/json"}
        assert request(port, "POST", "/api/account/provider-credentials", key_body,
                       headers)[0] == 200
        assert len(saved) == 1
        status, _, payload = request(
            port, "GET", "/api/account/provider-credentials", headers=headers,
        )
        assert status == 200 and json.loads(payload)["editable"] is False
        status, _, payload = request(port, "GET", "/api/account/creative-balance", headers=headers)
        assert status == 200 and json.loads(payload)["balances"][0]["amount"] == "7"
        assert request(port, "GET", "/account")[0] == 200
        assert request(port, "POST", "/api/account/profile", json.dumps({
            "nickname": "新昵称", "avatar": None,
        }), headers)[0] == 200
        assert request(port, "POST", "/api/account/password", json.dumps({
            "old_password": "password-for-test", "new_password": "new-password",
        }), headers)[0] == 200
    finally:
        legacy.shutdown()
        legacy.server_close()
        thread.join()
