"""供应商适配契约：能力层依赖它，具体 Provider 实现它。

放在 `providers/` 而不是 `capabilities/`，是为了让依赖方向统一向下：
`capabilities` 与 `providers` 都只导入本模块，Provider 不会反向依赖能力层。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from kantoku.schemas.media import ImageGenerationResult

RequestIdentityValue = str | int | bool


@dataclass(frozen=True, slots=True)
class ProviderAccessResult:
    """不创建任务的供应商接入诊断结果。"""

    authenticated: bool | None
    service_ready: bool
    code: str | int | None
    message: str | None
    request_id: str | None


class ImageProvider(Protocol):
    """真实与本地假供应商共同遵守的最小异步接口。"""

    model_id: str

    def generation_identity(self) -> Mapping[str, RequestIdentityValue]:
        """返回会改变生成结果或账单归属的非敏感配置。"""

    def check_access(self) -> ProviderAccessResult:
        """验证供应商鉴权；不得创建任务或产生生成费用。"""

    def validate_request(
        self,
        *,
        prompt: str,
        shot_no: int,
        reference_urls: Sequence[str],
        seed: int | None,
    ) -> None:
        """在预算预占和网络请求之前拒绝确定无效的参数。"""

    def submit(
        self,
        *,
        prompt: str,
        shot_no: int,
        client_request_id: str,
        reference_urls: Sequence[str],
        seed: int | None,
    ) -> str:
        """提交一次付费任务并返回供应商任务 ID。"""

    def query(self, provider_job_id: str) -> ImageGenerationResult:
        """查询已提交任务；查询可以重试，但不得重新提交。"""


__all__ = ["ImageProvider", "ProviderAccessResult", "RequestIdentityValue"]
