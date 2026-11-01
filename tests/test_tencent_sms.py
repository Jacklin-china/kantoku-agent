"""Offline contract tests: never send a real SMS."""

import json
from urllib.error import URLError

import pytest

from kantoku.adapters.tencent_sms import (
    SmsDeliveryError,
    TencentSmsConfig,
    send_verification_code,
)


@pytest.fixture
def config() -> TencentSmsConfig:
    return TencentSmsConfig("fake-id", "fake-secret", "123", "示例", "456")


def test_sends_one_signed_sms_request(config: TencentSmsConfig) -> None:
    requests = []

    def transport(request):
        requests.append(request)
        return b'{"Response":{"SendStatusSet":[{"Code":"Ok"}]}}'

    send_verification_code(config, "+8613800001234", "123456", now=1700000000,
                           transport=transport)
    assert len(requests) == 1
    request = requests[0]
    assert request.full_url == "https://sms.tencentcloudapi.com"
    assert request.get_header("X-tc-action") == "SendSms"
    assert request.get_header("X-tc-version") == "2021-01-11"
    assert "2023-11-14/sms/tc3_request" in request.get_header("Authorization")
    assert json.loads(request.data) == {
        "PhoneNumberSet": ["+8613800001234"], "SmsSdkAppId": "123",
        "SignName": "示例", "TemplateId": "456", "TemplateParamSet": ["123456", "5"],
    }
    assert "fake-secret" not in repr(config)


@pytest.mark.parametrize("phone,code", [
    ("13800001234", "123456"), ("+8613800001234", "12345"),
    ("+8613800001234", "１２３４５６"),
])
def test_invalid_input_never_calls_provider(config, phone, code):
    def forbidden(_request):
        raise AssertionError("invalid input must not send SMS")

    with pytest.raises(SmsDeliveryError):
        send_verification_code(config, phone, code, transport=forbidden)


@pytest.mark.parametrize("body", [
    b'{"Response":{"Error":{"Code":"AuthFailure"}}}',
    b'{"Response":{"SendStatusSet":[{"Code":"Failed"}]}}',
    b'{"Response":{"SendStatusSet":[]}}',
    b'{"Response":{}}', b'not-json',
])
def test_provider_failure_is_never_reported_as_success(config, body):
    with pytest.raises(SmsDeliveryError):
        send_verification_code(config, "+8613800001234", "123456", transport=lambda _: body)


def test_network_failure_is_not_retried(config):
    calls = 0

    def failed(_request):
        nonlocal calls
        calls += 1
        raise URLError("offline")

    with pytest.raises(SmsDeliveryError, match="短信服务请求失败"):
        send_verification_code(config, "+8613800001234", "123456", transport=failed)
    assert calls == 1
