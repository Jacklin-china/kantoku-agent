"""外部系统 Adapter。

余额类 Adapter 的失败必须能被子界面区分：`credential_missing`（没配密钥）与
`upstream_error`（上游失败）在界面上是完全不同的两件事，都不能显示成「余额 0」。
"""

from __future__ import annotations

from collections.abc import Callable

from kantoku.config import ToolError

CREDENTIAL_MISSING = "credential_missing"
CREDENTIAL_INVALID = "credential_invalid"
SERVICE_MISMATCH = "service_mismatch"
UPSTREAM_ERROR = "upstream_error"


class BalanceUnavailable(ToolError):
    """余额无法确定，并带有稳定的原因码。

    `code` 供界面判定展示哪个出口（去配置 / 重试），`str(error)` 仍是可直接展示的中文说明。
    """

    code = UPSTREAM_ERROR

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message, detail=detail)


class BalanceCredentialMissing(BalanceUnavailable):
    """没有配置查询所需的密钥。"""

    code = CREDENTIAL_MISSING


class BalanceCredentialInvalid(BalanceUnavailable):
    """密钥存在但被服务商拒绝。"""

    code = CREDENTIAL_INVALID


class BalanceServiceMismatch(BalanceUnavailable):
    """当前配置的服务商与余额接口不匹配。"""

    code = SERVICE_MISMATCH


def balance_snapshot(read: Callable[[], dict], platform: str) -> dict:
    """读取余额；失败时返回显式的 `unavailable`，绝不用空余额冒充 0。

    成功：``{"platform": ..., "balances": [...]}``
    失败：``{"platform": ..., "balances": [], "unavailable": {"code", "message"}}``
    """

    try:
        return read()
    except BalanceUnavailable as error:
        return {"platform": platform, "balances": [],
                "unavailable": {"code": error.code, "message": str(error)}}
    except ToolError as error:
        return {"platform": platform, "balances": [],
                "unavailable": {"code": UPSTREAM_ERROR, "message": str(error)}}
