"""带预算、幂等和 Artifact 持久化的视频能力服务。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from kantoku.config import BudgetError, ExternalJobPending, ToolError
from kantoku.config.observability import public_error
from kantoku.config.runtime_paths import validate_runtime_path
from kantoku.config.settings import VideoSettings
from kantoku.core import budget
from kantoku.core.runtime.models import ArtifactLifecycle, ArtifactRecord, ArtifactType
from kantoku.core.runtime.store import RuntimeStore

from .models import VideoGenerationRequest
from .provider import VideoPreparationError, VideoProvider


class VideoService:
    """Provider 外层的付费安全边界。"""

    def __init__(
        self, store: RuntimeStore, provider: VideoProvider, settings: VideoSettings
    ) -> None:
        self.store = store
        self.provider = provider
        self.settings = settings

    def generate(self, request: VideoGenerationRequest) -> ArtifactRecord:
        """通过 preflight → reserve → provider → settle 创建 VIDEO Artifact。"""
        serialized = json.dumps({"request": request.model_dump(mode="json"),
            "provider": self.provider.provider_id}, sort_keys=True, ensure_ascii=False)
        identity = hashlib.sha256(serialized.encode()).hexdigest()
        existing = budget.get_reservation(request.request_id)
        for artifact in self.store.list_artifacts(request.run_id, type=ArtifactType.VIDEO):
            if artifact.metadata.get("request_id") == request.request_id:
                if (artifact.metadata.get("request_sha256") is None
                        and artifact.metadata.get("mock") is True and not request.source_images
                        and request.input_sha256 is None
                        and artifact.metadata.get("image_artifact_id") == request.image_artifact_id
                        and artifact.metadata.get("config", {}) == request.config):
                    return artifact  # 仅兼容历史 Mock 的只读重放，不触发供应商或预算。
                if artifact.metadata.get("request_sha256") != identity:
                    raise ToolError("视频请求编号已绑定其他输入")
                return artifact
        if existing and (existing.idempotency_key != identity
                         or existing.run_id not in {None, request.run_id}
                         or existing.provider not in {None, self.provider.provider_id}):
            raise ToolError("视频预算请求编号已绑定其他输入")
        if not self.settings.enabled and not existing:
            raise ToolError("视频能力未启用", detail="video.enabled=false")
        if not existing and self.settings.estimated_fen > self.settings.max_fen:
            raise BudgetError(
                "视频预算不足",
                detail=(f"本次需 {self.settings.estimated_fen} 分，"
                        f"上限 {self.settings.max_fen} 分"),
            )
        if not existing:
            self._validate_images(request)
            self.provider.preflight(request)
            budget.reserve(
                reservation_id=request.request_id, job="video_generation", project=request.project,
                episode="video", shot_no=request.shot_no, kind="video",
                est_fen=self.settings.estimated_fen, model=self.settings.model,
                run_id=request.run_id,
                conversation_id=request.conversation_id, provider=self.provider.provider_id,
                idempotency_key=identity,
            )
        return self._execute(request, identity)

    def _validate_images(self, request):
        image = self.store.get_artifact(request.image_artifact_id)
        if image.type is not ArtifactType.IMAGE or (
            not request.source_images and image.run_id != request.run_id
        ):
            raise ToolError("视频输入必须是同一 Run 的图片 Artifact")
        if request.source_images:
            if request.source_images[0]["artifact_id"] != image.id or not request.input_sha256:
                raise ToolError("视频固定输入身份不一致")
            for source in request.source_images:
                artifact = self.store.get_artifact(source["artifact_id"])
                if (artifact.type != ArtifactType.IMAGE or artifact.run_id != source["run_id"]
                        or artifact.lifecycle_state != ArtifactLifecycle.ACTIVE
                        or artifact.metadata.get("origin") != "real" or not artifact.location):
                    raise ToolError("视频参考图片已不可用")
                path = validate_runtime_path(Path(artifact.location))
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != source[
                    "sha256"
                ]:
                    raise ToolError("视频参考图片内容已变化")

    def _execute(self, request, identity):
        reservation = budget.get_reservation(request.request_id)
        if reservation.status == "released":
            raise ToolError("原视频请求已结束，请新建生成请求")
        try:
            if reservation.status in {"submitted", "unknown", "succeeded", "settled"}:
                if not reservation.provider_job_id or not hasattr(self.provider, "query"):
                    raise ExternalJobPending("视频提交结果待核对，请勿重新运行",
                                             needs_reconciliation=True)
                result = self.provider.query(request, reservation.provider_job_id)
            elif budget.claim_submission(request.request_id):
                result = self.provider.generate(request)
            else:
                raise ExternalJobPending("视频请求已被领取，请查询原请求")
        except ExternalJobPending:
            raise
        except VideoPreparationError as error:
            # 仅供应商明确证明视频请求尚未发出时释放；其他未知状态仍保留。
            latest = budget.get_reservation(request.request_id)
            if latest.status == "submitted" and latest.provider_job_id is None:
                budget.mark_outcome(request.request_id, "failed")
                budget.release(request.request_id)
            public_error(error, component="video.prepare", run_id=request.run_id,
                         request_id=request.request_id, provider=self.provider.provider_id)
            raise
        except Exception as error:
            failure = public_error(error, component="video.provider", run_id=request.run_id,
                                   request_id=request.request_id,
                                   provider=self.provider.provider_id)
            latest = budget.get_reservation(request.request_id)
            if latest.status == "submitted" and latest.provider_job_id is None:
                budget.mark_outcome(request.request_id, "unknown")
                raise ExternalJobPending("视频提交结果未知，请查询原请求，勿重复生成 · "
                                         + failure["error_id"],
                                         needs_reconciliation=True) from None
            if latest.status == "submitted":
                budget.mark_outcome(request.request_id, "unknown",
                                    provider_job_id=latest.provider_job_id)
            raise ExternalJobPending(
                "视频结果尚未核验，可继续查询原任务 · " + failure["error_id"],
                needs_reconciliation=True) from None
        if result.provider_job_id and reservation.status not in {"succeeded", "settled"}:
            latest = budget.get_reservation(request.request_id)
            if latest.status == "submitted":
                budget.mark_submitted(request.request_id, provider_job_id=result.provider_job_id)
        if result.status in {"pending", "unknown"}:
            if result.status == "unknown" and reservation.status in {"submitted", "unknown"}:
                budget.mark_outcome(request.request_id, "unknown",
                                    provider_job_id=result.provider_job_id)
            message = ("视频结果尚未核验，请继续查询原任务" if result.status == "unknown"
                       else "视频正在生成" if result.provider_job_id else "视频结果待核对")
            raise ExternalJobPending(message,
                needs_reconciliation=result.status == "unknown" or not bool(result.provider_job_id))
        if result.status != "succeeded" or result.location is None:
            outcome = "unknown" if result.status == "unknown" else "failed"
            budget.mark_outcome(
                request.request_id, outcome, provider_job_id=result.provider_job_id
            )
            if outcome == "failed":
                budget.release(request.request_id)
            raise ToolError("视频生成未成功", detail=result.error or result.status)
        try:
            return self._save_result(request, result, reservation, identity)
        except BudgetError:
            raise
        except Exception as error:
            failure = public_error(error, component="video.artifact", run_id=request.run_id,
                                   request_id=request.request_id)
            raise ExternalJobPending("视频保存未完成，可查询原任务恢复 · " + failure["error_id"],
                                     needs_reconciliation=True) from None

    def _save_result(self, request, result, reservation, identity):
        if reservation.status not in {"succeeded", "settled"}:
            budget.mark_outcome(
                request.request_id, "succeeded", provider_job_id=result.provider_job_id
            )
        if result.actual_fen is not None:
            budget.settle(request.request_id, result.actual_fen)
        return self.store.create_artifact(
            type=ArtifactType.VIDEO,
            run_id=request.run_id,
            node_id=request.node_id,
            source=f"video.{self.provider.provider_id}",
            location=result.location,
            metadata={
                "request_id": request.request_id,
                "image_artifact_id": request.image_artifact_id,
                "provider_job_id": result.provider_job_id,
                "mock": result.mock,
                "origin": "mock" if result.mock else "real",
                "input_sha256": request.input_sha256,
                "request_sha256": identity,
                "sha256": (hashlib.sha256(validate_runtime_path(
                    Path(result.location)).read_bytes()).hexdigest() if not result.mock else None),
                "config": request.config,
            },
        )
