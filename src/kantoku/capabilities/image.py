"""Shared image capability for lightweight Conversation requests.

Provider submission, budget reservation, and reconciliation remain in the
existing image_gen implementation; this layer only attaches its result to a
Conversation and the common Artifact store.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from loguru import logger
from PIL import Image, ImageOps, UnidentifiedImageError

from kantoku.config import ToolError
from kantoku.config.runtime_paths import validate_runtime_path
from kantoku.core import budget
from kantoku.core.conversations import (
    ConversationMessageRecord,
    MessageRole,
    MessageType,
)
from kantoku.core.runtime.models import ArtifactLifecycle, ArtifactRecord, ArtifactType, utc_now
from kantoku.core.runtime.store import RuntimeStore
from kantoku.tools.image_gen import ImageProvider, gen_image


@dataclass(frozen=True)
class PreparedVisionImage:
    """仅用于视觉请求的内存副本；不替换原图或创建媒体版本。"""

    data: bytes
    mime_type: str
    metadata: dict


def crop_vision_image(data: bytes, mime_type: str, bounds: list[float]) -> PreparedVisionImage:
    """Crop verified source bytes in browser display coordinates; no media is persisted."""
    if len(bounds) != 4 or not all(math.isfinite(value) for value in bounds) or not (
            0 <= bounds[0] < bounds[2] <= 1 and 0 <= bounds[1] < bounds[3] <= 1):
        raise ToolError("局部观察坐标无效")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as original:
                if {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(
                        original.format) != mime_type or (
                        original.width * original.height > 40_000_000):
                    raise ToolError("局部观察图片的格式或容量无效")
                width, height = original.size
                orientation = original.getexif().get(274, 1)
                with ImageOps.exif_transpose(original) as display:
                    box = (math.floor(bounds[0] * display.width),
                           math.floor(bounds[1] * display.height),
                           math.ceil(bounds[2] * display.width),
                           math.ceil(bounds[3] * display.height))
                    with display.crop(box) as crop:
                        alpha = "A" in crop.getbands() or "transparency" in crop.info
                        with crop.convert("RGBA" if alpha else "RGB") as view, BytesIO() as buffer:
                            view.save(buffer, format="PNG")
                            encoded = buffer.getvalue()
                    transforms = {1: [1, 0, 0, 0, 1, 0], 2: [-1, 0, width, 0, 1, 0],
                        3: [-1, 0, width, 0, -1, height], 4: [1, 0, 0, 0, -1, height],
                        5: [0, 1, 0, 1, 0, 0], 6: [0, 1, 0, -1, 0, height],
                        7: [0, -1, width, -1, 0, height], 8: [0, -1, width, 1, 0, 0]}
                    metadata = {"contract": "vision-crop-1",
                        "source_sha256": hashlib.sha256(data).hexdigest(),
                        "raw_size": [width, height], "display_size": list(display.size),
                        "orientation": orientation, "display_to_source": transforms.get(
                            orientation, transforms[1]), "requested_bounds": bounds,
                        "display_pixel_box": list(box),
                        "crop_size": [box[2] - box[0], box[3] - box[1]],
                        "upscaled": False}
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombWarning,
            Image.DecompressionBombError):
        raise ToolError("图片无法解码为有效的局部观察输入") from None
    return PreparedVisionImage(encoded, "image/png", metadata)


def prepare_vision_image(data: bytes, mime_type: str, *, max_side: int = 2048,
                         max_bytes: int = 1024 * 1024) -> PreparedVisionImage:
    """在原显示方向保持归一化坐标，限制上传体积并保留来源指纹。"""
    if max_side < 64 or max_bytes < 16384:
        raise ToolError("视觉传输副本的容量配置过小")
    source_hash = hashlib.sha256(data).hexdigest()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as original:
                actual_mime = {"PNG": "image/png", "JPEG": "image/jpeg",
                               "WEBP": "image/webp"}.get(original.format)
                if actual_mime != mime_type or actual_mime is None:
                    raise ToolError("视觉输入的图片格式与内容不一致")
                if original.width * original.height > 40_000_000:
                    raise ToolError("视觉输入的图片像素超过处理容量")
                orientation = original.getexif().get(274, 1)
                original.load()
                display = ImageOps.exif_transpose(original)
                try:
                    display_size = display.size
                    orientation_applied = orientation in range(2, 9)
                    if (not orientation_applied and max(display_size) <= max_side
                            and len(data) <= max_bytes):
                        encoded, sent_mime, sent_size = data, mime_type, display_size
                    else:
                        display.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
                        alpha = "A" in display.getbands() or "transparency" in display.info
                        mode = "RGBA" if alpha else "RGB"
                        with display.convert(mode) as view:
                            while True:
                                for quality in ([None] if alpha else [94, 90, 86]):
                                    with BytesIO() as buffer:
                                        if alpha:
                                            view.save(buffer, format="PNG", optimize=True)
                                        else:
                                            view.save(buffer, format="JPEG", quality=quality,
                                                      subsampling=0, optimize=True)
                                        encoded = buffer.getvalue()
                                    if len(encoded) <= max_bytes:
                                        break
                                if len(encoded) <= max_bytes:
                                    break
                                if max(view.size) <= 64:
                                    raise ToolError("图片无法在视觉传输容量内保留有效画面")
                                view.thumbnail((max(1, int(view.width * .85)),
                                                max(1, int(view.height * .85))),
                                               Image.Resampling.LANCZOS)
                            sent_size = view.size
                            sent_mime = "image/png" if alpha else "image/jpeg"
                finally:
                    display.close()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombWarning,
            Image.DecompressionBombError):
        raise ToolError("图片无法解码为有效视觉输入",
                        detail="module=capabilities.image stage=prepare_vision") from None
    return PreparedVisionImage(encoded, sent_mime, {
        "contract": "vision-input-1", "source_sha256": source_hash,
        "sha256": hashlib.sha256(encoded).hexdigest(), "source_bytes": len(data),
        "sent_bytes": len(encoded), "display_size": list(display_size),
        "sent_size": list(sent_size), "orientation_applied": orientation_applied,
        "resized": sent_size != display_size,
        "lossy": encoded != data and sent_mime == "image/jpeg",
        "transformed": encoded != data})


@dataclass(frozen=True)
class PreparedImageInput:
    execution_prompt: str
    reference_urls: tuple[str, ...]
    reference_artifact_ids: tuple[str, ...]
    input_artifact_id: str
    sha256: str


def prepare_generation_input(store: RuntimeStore, artifact_id: str,
                             conversation_id: str) -> PreparedImageInput:
    """Adapt the same immutable material used by a local image tool; no provider calls."""
    artifact = store.get_artifact(artifact_id)
    if (artifact.type != ArtifactType.DOCUMENT or artifact.status != "ready"
            or artifact.lifecycle_state != ArtifactLifecycle.ACTIVE
            or artifact.conversation_id != conversation_id
            or artifact.metadata.get("input_contract") != "image-generation-input-1"):
        raise ToolError("生成输入不是当前会话的有效固定任务")
    path = validate_runtime_path(Path(artifact.location))
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ToolError("固定生成任务文件缺失或过大")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != artifact.metadata.get("sha256"):
        raise ToolError("固定生成任务已变化")
    task = json.loads(data)
    if (task.get("contract") != "image-generation-input-1"
            or task.get("task_id") != artifact.metadata.get("task_id")
            or task.get("conversation_id") != conversation_id
            or not isinstance(task.get("execution_prompt"), str)
            or not task["execution_prompt"].strip()
            or not isinstance(task.get("references"), list) or len(task["references"]) > 8):
        raise ToolError("固定生成输入契约无效，未降级为纯文生图")
    urls, ids = [], []
    for order, reference in enumerate(task["references"], 1):
        reference_path = validate_runtime_path(Path(reference["path"]))
        if (reference.get("order") != order or reference_path.parent != path.parent
                or not reference_path.is_file() or reference_path.stat().st_size > 5 * 1024 * 1024):
            raise ToolError("生成参考图缺失、越界或顺序不一致")
        content = reference_path.read_bytes()
        if hashlib.sha256(content).hexdigest() != reference.get("sha256"):
            raise ToolError("生成参考图内容已变化")
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}.get(
            reference_path.suffix.lower())
        if mime is None:
            raise ToolError("生成参考图格式不受支持")
        urls.append(f"data:{mime};base64," + base64.b64encode(content).decode("ascii"))
        ids.append(reference["artifact_id"])
    return PreparedImageInput(task["execution_prompt"], tuple(urls), tuple(ids),
                              artifact.id, digest)


class ConversationImageService:
    """Deliver one paid image without creating a Graph Run or StudioTask."""

    def __init__(self, store: RuntimeStore, provider: ImageProvider) -> None:
        self.store = store
        self.provider = provider

    def generate(
        self, *, conversation_id: str, user_message_id: str,
        generation_request_id: str, prompt: str, estimate_fen: int,
        reference_artifact_id: str | None = None,
        generation_input_artifact_id: str | None = None,
    ) -> ConversationMessageRecord:
        prepared = None
        if generation_input_artifact_id:
            if reference_artifact_id is not None:
                raise ToolError("固定生成任务不能同时使用另一张单图参考")
            prepared = prepare_generation_input(self.store, generation_input_artifact_id,
                                                 conversation_id)
            if prompt != prepared.execution_prompt:
                raise ToolError("提交正文与固定生成任务不一致")
        saved_prompt = self.store.save_conversation_generation(
            generation_request_id, conversation_id, user_message_id, prompt,
        )
        event = logger.bind(
            component="conversation-image", conversation_id=conversation_id,
            generation_request_id=generation_request_id,
            provider=str(self.provider.generation_identity().get("provider", "unknown")),
        )
        event.info("generation start")
        reference_urls: tuple[str, ...] = prepared.reference_urls if prepared else ()
        if reference_artifact_id is not None:
            artifact = self.store.get_artifact(reference_artifact_id)
            if (artifact.conversation_id != conversation_id or artifact.type != ArtifactType.IMAGE
                    or artifact.status != "ready" or not artifact.location):
                raise ToolError("参考图片不是当前聊天的已完成图片")
            source = Path(artifact.location)
            if not source.is_file() or source.stat().st_size > 10_000_000:
                raise ToolError("参考图片不存在或超过供应商大小限制")
            image_bytes = source.read_bytes()
            if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            elif image_bytes.startswith(b"\xff\xd8\xff"):
                mime = "image/jpeg"
            elif image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
                mime = "image/webp"
            else:
                raise ToolError("当前参考图片格式不受支持")
            reference_urls = (
                f"data:{mime};base64," + base64.b64encode(image_bytes).decode("ascii"),
            )
        result = gen_image(
            saved_prompt, 1, conversation_id=conversation_id,
            client_request_id=generation_request_id, provider=self.provider,
            est_fen=estimate_fen, reference_urls=reference_urls,
            **({"input_identity": prepared.sha256} if prepared else {}),
        )
        if result.path is None:
            unknown = result.status == "unknown"
            event.warning("generation not ready status={}", result.status)
            return self.store.add_conversation_message(
                conversation_id, role=MessageRole.ASSISTANT,
                type=MessageType.STATUS if unknown else MessageType.ERROR,
                content=(
                    ("原请求状态未知且缺少供应商任务 ID，需要人工对账；不会重复提交或扣费。"
                     if not result.provider_job_id else
                     "原生成请求仍在查询，稍后可说‘继续任务’；不会重复提交或扣费。")
                    if unknown else "图片生成失败；不会自动重新付费提交。"
                ) + (f" 错误编号：{result.error_id}" if result.error_id else ""),
                event_id=f"generation-status:{generation_request_id}:{result.status}",
            )
        if not result.path.is_file():
            event.error("provider reported missing image file path={}", result.path)
            raise ToolError("生图结果文件不存在；请按错误编号检查日志，不会自动重复提交")

        artifact_id = f"artifact-{uuid5(NAMESPACE_URL, generation_request_id).hex}"
        reservation = budget.get_reservation(generation_request_id)
        if reservation is None:
            raise ToolError("生图结果缺少预算台账")
        if reservation.artifact_id:
            artifact = self.store.get_artifact(reservation.artifact_id)
        else:
            record = ArtifactRecord(
                id=artifact_id, type=ArtifactType.IMAGE, run_id=None,
                conversation_id=conversation_id, node_id="image.generate",
                source="conversation.image.generate", status="ready",
                created_at=utc_now(), location=str(result.path),
                metadata={
                    "origin": "real", "generation_request_id": generation_request_id,
                    "provider_job_id": result.provider_job_id,
                    "parent_artifact_id": reference_artifact_id,
                    **({"generation_input_artifact_id": prepared.input_artifact_id,
                        "generation_input_sha256": prepared.sha256,
                        "reference_artifact_ids": list(prepared.reference_artifact_ids)}
                       if prepared else {}),
                },
            )
            try:
                artifact = self.store.save_artifact(record)
            except ToolError:
                # A concurrent replay may have inserted the deterministic artifact.
                # A different write error still fails when this lookup finds nothing.
                artifact = self.store.get_artifact(artifact_id)
                if artifact.conversation_id != conversation_id:
                    raise ToolError("生图 Artifact 已属于其他对话") from None
            budget.attach_image_artifact(generation_request_id, artifact.id)
        event.info("artifact ready id={}", artifact.id)
        return self.store.add_conversation_message(
            conversation_id, role=MessageRole.ASSISTANT,
            type=MessageType.ARTIFACT, content="图片已生成",
            artifact_id=artifact.id, event_id=f"generation-artifact:{generation_request_id}",
        )
