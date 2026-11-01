"""Read the DeepSeek account balance without making a model request."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from kantoku.adapters import (
    BalanceCredentialInvalid,
    BalanceCredentialMissing,
    BalanceServiceMismatch,
    BalanceUnavailable,
)
from kantoku.config import ConfigError, get_settings


def account_balance() -> dict:
    settings = get_settings().llm
    if settings.base_url.rstrip("/") not in {
        "https://api.deepseek.com", "https://api.deepseek.com/v1",
    }:
        raise BalanceServiceMismatch("当前文字服务不是 DeepSeek，无法查询余额")
    try:
        key = settings.chat_api_key()
    except ConfigError:
        # 详情（环境变量名与 .env 提示）只留在服务端日志，界面只说清楚发生了什么。
        raise BalanceCredentialMissing("未配置 DeepSeek 密钥，无法读取账户余额") from None
    if not key:
        raise BalanceCredentialMissing("未配置 DeepSeek 密钥，无法读取账户余额")
    request = Request(
        "https://api.deepseek.com/user/balance",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310 - fixed official URL
            payload = json.loads(response.read(65537))
        amounts = []
        for item in payload["balance_infos"]:
            currency = item["currency"]
            total = Decimal(item["total_balance"])
            if currency not in {"CNY", "USD"} or not total.is_finite():
                raise ValueError("invalid balance")
            amounts.append({"currency": currency, "amount": str(total)})
        if not amounts:
            raise ValueError("empty balance")
        return {"platform": "DeepSeek", "balances": amounts}
    except HTTPError as error:
        if error.code in {401, 403}:
            raise BalanceCredentialInvalid("文字服务密钥无效或没有查询权限") from None
        raise BalanceUnavailable("文字服务余额查询失败，请稍后重试") from None
    except (URLError, TimeoutError, OSError, ValueError, TypeError, KeyError,
            InvalidOperation) as error:
        raise BalanceUnavailable("文字服务余额查询失败，请检查网络和配置") from error
