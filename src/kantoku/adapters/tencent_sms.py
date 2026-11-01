"""Tencent Cloud SMS transport for a future authenticated login flow.

This adapter only sends a supplied code. Issuing, limiting and verifying codes
belongs to the authentication service; the adapter must never be exposed as an
HTTP endpoint on its own.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_HOST = "sms.tencentcloudapi.com"
_CONTENT_TYPE = "application/json; charset=utf-8"


class SmsDeliveryError(RuntimeError):
    """A sanitized delivery failure; never contains the code or credentials."""


@dataclass(frozen=True)
class TencentSmsConfig:
    secret_id: str = field(repr=False)
    secret_key: str = field(repr=False)
    app_id: str
    sign_name: str
    template_id: str
    region: str = "ap-guangzhou"

    def validate(self) -> None:
        if any(not value.strip() for value in (
            self.secret_id, self.secret_key, self.app_id, self.sign_name,
            self.template_id, self.region,
        )):
            raise SmsDeliveryError("短信服务尚未完成配置")


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _send(request: Request) -> bytes:
    with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed HTTPS host
        return response.read(65537)


def send_verification_code(
    config: TencentSmsConfig,
    phone: str,
    code: str,
    *,
    valid_minutes: int = 5,
    now: int | None = None,
    transport: Callable[[Request], bytes] = _send,
) -> None:
    """Send one Chinese mobile verification code; never automatically retry.

    The configured SMS template must have code and validity (minutes) variables
    in that order. A successful API response means accepted, not handset delivery.
    """
    config.validate()
    if not re.fullmatch(r"\+861[3-9][0-9]{9}", phone):
        raise SmsDeliveryError("手机号格式不正确")
    if not re.fullmatch(r"[0-9]{6}", code) or not 1 <= valid_minutes <= 10:
        raise SmsDeliveryError("验证码参数不正确")
    timestamp = int(time.time()) if now is None else now
    date = datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d")
    payload = json.dumps({
        "PhoneNumberSet": [phone],
        "SmsSdkAppId": config.app_id,
        "SignName": config.sign_name,
        "TemplateId": config.template_id,
        "TemplateParamSet": [code, str(valid_minutes)],
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    canonical_headers = f"content-type:{_CONTENT_TYPE}\nhost:{_HOST}\n"
    signed_headers = "content-type;host"
    canonical = "\n".join((
        "POST", "/", "", canonical_headers, signed_headers,
        hashlib.sha256(payload).hexdigest(),
    ))
    scope = f"{date}/sms/tc3_request"
    string_to_sign = "\n".join((
        "TC3-HMAC-SHA256", str(timestamp), scope,
        hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    ))
    date_key = _sign(("TC3" + config.secret_key).encode("utf-8"), date)
    service_key = _sign(date_key, "sms")
    signing_key = _sign(service_key, "tc3_request")
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (
        f"TC3-HMAC-SHA256 Credential={config.secret_id}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    request = Request(
        f"https://{_HOST}", data=payload, method="POST", headers={
            "Content-Type": _CONTENT_TYPE, "Host": _HOST,
            "Authorization": authorization, "X-TC-Action": "SendSms",
            "X-TC-Version": "2021-01-11", "X-TC-Region": config.region,
            "X-TC-Timestamp": str(timestamp),
        },
    )
    try:
        raw = transport(request)
        if len(raw) > 65536:
            raise SmsDeliveryError("短信服务返回内容过长")
        envelope = json.loads(raw)
        response = envelope["Response"]
        if "Error" in response:
            error_code = response["Error"].get("Code", "Unknown")
            if not isinstance(error_code, str) or not re.fullmatch(
                r"[A-Za-z0-9.]{1,80}", error_code
            ):
                error_code = "Unknown"
            raise SmsDeliveryError(f"短信服务拒绝请求（{error_code}）")
        statuses = response["SendStatusSet"]
        if len(statuses) != 1 or statuses[0].get("Code") != "Ok":
            raise SmsDeliveryError("短信服务未接受发送请求")
    except SmsDeliveryError:
        raise
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, TypeError) as exc:
        raise SmsDeliveryError("短信服务请求失败，请检查服务配置和网络") from exc
