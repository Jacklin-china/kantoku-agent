"""Read the Volcengine billing account balance using the existing SDK."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from volcengine.ApiInfo import ApiInfo
from volcengine.base.Service import Service
from volcengine.Credentials import Credentials
from volcengine.ServiceInfo import ServiceInfo

from kantoku.adapters import BalanceCredentialMissing, BalanceUnavailable
from kantoku.config import ConfigError, get_settings


def account_balance(*, credentials: tuple[str, str] | None = None) -> dict:
    settings = get_settings().video
    try:
        access_key, secret_key = (credentials if credentials is not None
                                  else (settings.access_key(), settings.secret_key()))
    except ConfigError:
        # 详情（环境变量名与 .env 提示）只留在服务端日志，界面只说清楚发生了什么。
        raise BalanceCredentialMissing("未配置火山引擎密钥，无法读取账户余额") from None
    if not access_key or not secret_key:
        raise BalanceCredentialMissing("未配置火山引擎密钥，无法读取账户余额")
    service = Service(
        ServiceInfo("open.volcengineapi.com", {"Accept": "application/json"},
                    Credentials(access_key, secret_key, "billing", "cn-north-1"), 5, 5),
        {"balance": ApiInfo("GET", "/", {"Action": "QueryBalanceAcct",
                                         "Version": "2022-01-01"}, {}, {})},
    )
    service.set_scheme("https")
    # The SDK may load ambient credentials during initialization.
    service.set_ak(access_key)
    service.set_sk(secret_key)
    try:
        payload = json.loads(service.get("balance", {}))
        if payload.get("ResponseMetadata", {}).get("Error"):
            raise ValueError("provider rejected request")
        balance = Decimal(payload["Result"]["AvailableBalance"])
        if not balance.is_finite():
            raise ValueError("invalid balance")
        return {"platform": "火山引擎", "balances": [{"currency": "CNY", "amount": str(balance)}]}
    except (ValueError, KeyError, TypeError, InvalidOperation):
        raise BalanceUnavailable("创作额度查询失败，请检查财务查询权限或稍后重试") from None
    except Exception:
        raise BalanceUnavailable("创作额度查询失败，请检查网络或财务查询权限") from None
    finally:
        service.session.close()
