"""小云雀官方异步协议；只提交一次，查询与下载可独立恢复。"""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from loguru import logger
from PIL import Image, UnidentifiedImageError

from kantoku.config import ConfigError, ToolError
from kantoku.config.observability import current_run_id, current_trace_id
from kantoku.config.runtime_paths import (
    atomic_write_bytes,
    resolve_runtime_path,
    validate_runtime_path,
)

from .models import VideoGenerationResult
from .provider import VideoPreparationError


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ToolError("视频媒体地址发生重定向，请核对访问地址")


class XiaoyunqueVideoProvider:
    """公网参考图须按内容指纹配置，未配置时不发布本地文件或退成纯文生视频。"""

    provider_id = "xiaoyunque"
    req_key = "pippit_iv2v_v20_cvtob_with_vinput"

    def __init__(self, settings, *, client=None, read_url=None, store=None, tos_client=None,
                 credentials=None):
        self.settings = settings
        self._client = client
        # An injected client has no verifiable account identity.  Only clients
        # constructed here can be matched to a billing credential pair.
        self._client_credentials = None
        self._read_url = read_url or self._download
        self._store = store
        self._tos_client = tos_client
        self._credentials = credentials

    def _access_credentials(self):
        if self._credentials is not None:
            return self._credentials
        return self.settings.access_key(), self.settings.secret_key()

    def readiness(self):
        try:
            if not self.settings.enabled:
                raise ConfigError("请先配置小云雀视频服务")
            if self.settings.model != self.req_key:
                raise ConfigError("请配置小云雀 2.0 有参考模型")
            if not self.settings.pricing_confirmed:
                raise ConfigError("请先配置小云雀费用预估和单次上限")
            if self._client is None:
                if self.settings.authentication == "api_key":
                    self.settings.api_key()
                else:
                    self._access_credentials()
            if self.settings.tos_bucket:
                if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", self.settings.tos_bucket):
                    raise ConfigError("对象存储桶名称不合法")
                if not re.fullmatch(r"(?:cn|ap)-[a-z]+(?:-\d+)?", self.settings.tos_region):
                    raise ConfigError("对象存储地域不合法")
                if self._store is None:
                    raise ConfigError("本地参考图片服务不可用")
                if self._tos_client is None:
                    self._access_credentials()
            elif not self.settings.reference_urls:
                raise ConfigError("小云雀参考图访问地址尚未配置")
            return None
        except ConfigError as error:
            return error.message

    @staticmethod
    def _check_url(url):
        parsed = urlsplit(url)
        domains = ("volces.com", "volcengine.com", "byteimg.com", "douyinvod.com",
                   "douyinstatic.com", "pstatp.com", "bytedance.com")
        if (parsed.scheme != "https" or not parsed.hostname or parsed.port not in {None, 443}
                or parsed.username or parsed.password
                or not any(parsed.hostname == host or parsed.hostname.endswith("." + host)
                           for host in domains)):
            raise ToolError("视频媒体必须使用受信任的火山引擎 HTTPS 存储地址")

    def _download(self, url, limit):
        self._check_url(url)
        with build_opener(_NoRedirect()).open(url, timeout=self.settings.timeout_s) as response:
            content = response.read(limit + 1)
        if len(content) > limit:
            raise ToolError("视频媒体超过本地容量限制")
        return content

    def preflight(self, request):
        if reason := self.readiness():
            raise ConfigError(reason)
        if not request.prompt.strip() or len(request.prompt) > 2000 or not request.source_images:
            raise ToolError("小云雀需要参考图和不超过 2000 字的提示词")
        if self.settings.tos_bucket:
            for image in request.source_images:
                self._local_reference(image)
            return
        for image in request.source_images:
            url = self.settings.reference_urls.get(image["sha256"])
            if not url:
                raise ToolError("当前镜头的参考图访问地址尚未配置")
            self._check_url(url)
            content = self._read_url(url, 20 * 1024 * 1024)
            if hashlib.sha256(content).hexdigest() != image["sha256"]:
                raise ToolError("公网参考图与故事固定图片不一致，未提交视频生成")

    def _local_reference(self, image):
        artifact = self._store.get_artifact(image["artifact_id"])
        path = validate_runtime_path(Path(artifact.location))
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ToolError("小云雀参考图片不能超过 20 MB")
        content = path.read_bytes()
        if len(content) > 20 * 1024 * 1024:
            raise ToolError("小云雀参考图片不能超过 20 MB")
        if hashlib.sha256(content).hexdigest() != image["sha256"]:
            raise ToolError("视频参考图片内容已变化，未提交视频生成")
        try:
            with Image.open(io.BytesIO(content)) as picture:
                if (picture.format not in {"JPEG", "PNG"}
                        or max(picture.size) > 4096):
                    raise ToolError("参考图须为 PNG 或 JPEG，分辨率不能超过 4096 像素")
                content_type = "image/png" if picture.format == "PNG" else "image/jpeg"
                picture.verify()
        except (UnidentifiedImageError, OSError, ValueError):
            raise ToolError("视频参考图片无法读取") from None
        return content, content_type

    def _upload_references(self, request):
        import tos

        client = self._tos_client or tos.TosClientV2(
            *self._access_credentials(),
            f"https://tos-{self.settings.tos_region}.volces.com", self.settings.tos_region,
            max_retry_count=0, request_timeout=self.settings.timeout_s,
            follow_redirect_times=0,
        )
        urls = []
        for image in request.source_images:
            content, content_type = self._local_reference(image)
            # 每次使用独立对象名，禁止覆盖现有对象；不改变桶的访问策略。
            key = f"kantoku/video-references/{uuid4().hex}/{image['sha256']}"
            try:
                client.put_object(self.settings.tos_bucket, key, content=content,
                                  content_type=content_type, acl=tos.ACLType.ACL_Private,
                                  forbid_overwrite=True)
                url = client.pre_signed_url(tos.HttpMethodType.Http_Method_Get,
                    self.settings.tos_bucket, key, expires=12 * 60 * 60).signed_url
                self._check_url(url)
                remote = self._read_url(url, 20 * 1024 * 1024)
                if hashlib.sha256(remote).hexdigest() != image["sha256"]:
                    raise ToolError("已上传参考图与原图不一致，未提交视频生成")
            except ToolError:
                raise
            except Exception as error:
                logger.bind(component="video.reference_upload", provider=self.provider_id,
                    run_id=current_run_id(), trace_id=current_trace_id()).warning(
                        "video reference upload failed error_type={}", type(error).__name__)
                raise ToolError("参考图上传或访问失败，请检查对象存储权限") from None
            urls.append(url)
        return urls

    def _call(self, action, payload):
        started = time.monotonic()
        if self._client is None and self.settings.authentication == "api_key":
            request = Request(
                "https://visual.volcengineapi.com?Action=" + action + "&Version=2022-08-31",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + self.settings.api_key()},
                method="POST",
            )
            try:
                with build_opener(_NoRedirect()).open(
                    request, timeout=self.settings.timeout_s
                ) as response:
                    raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise ToolError("小云雀响应过大")
                result = json.loads(raw)
            except ToolError:
                raise
            except Exception as error:
                # 供应商或 HTTP 错误内容可能包含敏感数据；提交结果由能力层按未知账单处理。
                logger.bind(component="video.provider", provider=self.provider_id,
                    run_id=current_run_id(), trace_id=current_trace_id(), operation=action).warning(
                        "video provider request failed error_type={}", type(error).__name__)
                raise ToolError("小云雀接口请求未完成") from None
            if not isinstance(result, dict):
                raise ToolError("小云雀返回无效响应")
            request_id = result.get("request_id")
            logger.bind(component="video.provider", provider=self.provider_id,
                model=self.req_key, run_id=current_run_id(), trace_id=current_trace_id(),
                provider_request_id=request_id[:200] if isinstance(request_id, str) else None,
                operation=action).info("video provider response elapsed_ms={}",
                                      round((time.monotonic() - started) * 1000))
            return result
        if self._client is None:
            from volcengine.visual.VisualService import VisualService

            self._client = VisualService()
            self._client.set_scheme("https")
            self._client.set_host("visual.volcengineapi.com")
            self._client.service_info.credentials.service = "cv"
            self._client.service_info.credentials.region = "cn-north-1"
            access_key, secret_key = self._access_credentials()
            self._client.set_ak(access_key)
            self._client.set_sk(secret_key)
            self._client_credentials = (access_key, secret_key)
            self._client.set_connection_timeout(self.settings.timeout_s)
            self._client.set_socket_timeout(self.settings.timeout_s)
        self._client.api_info[action].query.update(Action=action, Version="2022-08-31")
        try:
            raw = self._client.common_json_handler(action, payload)
        except Exception as error:
            # 接口错误可能回显包含临时签名的请求，不能将原文写入日志或错误链。
            logger.bind(component="video.provider", provider=self.provider_id,
                run_id=current_run_id(), trace_id=current_trace_id(), operation=action).warning(
                    "video provider request failed error_type={}", type(error).__name__)
            raise ToolError("小云雀接口请求未完成") from None
        result = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        if not isinstance(result, dict):
            raise ToolError("小云雀返回无效响应")
        request_id = result.get("request_id")
        logger.bind(component="video.provider", provider=self.provider_id,
            model=self.req_key, run_id=current_run_id(), trace_id=current_trace_id(),
            provider_request_id=request_id[:200] if isinstance(request_id, str) else None,
            operation=action).info("video provider response elapsed_ms={}",
                                  round((time.monotonic() - started) * 1000))
        return result

    def generate(self, request):
        seconds = request.config.get("duration_seconds", 15)
        hint = ("～15s" if seconds <= 15 else "～30s" if seconds <= 30
                else "40～60s" if seconds <= 60 else "1min+")
        try:
            references = (self._upload_references(request) if self.settings.tos_bucket else
                          [self.settings.reference_urls[item["sha256"]]
                           for item in request.source_images])
        except Exception as error:
            # 此分支在任何视频提交之前，允许能力层释放视频预算；不泄露签名链接。
            message = error.message if isinstance(error, ToolError) else "视频参考图准备失败"
            raise VideoPreparationError(message) from None
        result = self._call("CVSync2AsyncSubmitTask", {
            "req_key": self.req_key, "prompt": request.prompt,
            "img_url_list": references,
            "duration": hint, "language": "Chinese", "enable_watermark": True,
        })
        # 没有任务号的提交拒绝不推定零费用，交由能力层保留未知账单。
        task_id = (result.get("data") or {}).get("task_id")
        if result.get("code") != 10000 or not isinstance(task_id, str) or not task_id:
            code = result.get("code")
            raise ToolError("小云雀未返回可核验任务编号",
                            detail=f"code={code if isinstance(code, int) else 'invalid'}")
        return VideoGenerationResult(status="pending", provider_job_id=task_id)

    def query(self, request, task_id):
        result = self._call("CVSync2AsyncGetResult", {"req_key": self.req_key, "task_id": task_id})
        if result.get("code") != 10000:
            code = result.get("code")
            raise ToolError("小云雀查询未通过",
                            detail=f"code={code if isinstance(code, int) else 'invalid'}")
        data = result.get("data") or {}
        if data.get("status") in {"processing", "in_queue", "generating"}:
            return VideoGenerationResult(status="pending", provider_job_id=task_id)
        if data.get("status") != "done" or not data.get("video_url"):
            raise ToolError("小云雀结果不存在、已过期或尚未提供有效视频")
        # 下载失败时任务号已在原账本中，恢复只查询和取回同一视频，绝不重生。
        content = self._read_url(data["video_url"], 200 * 1024 * 1024)
        if len(content) < 16 or b"ftyp" not in content[:64]:
            raise ToolError("小云雀结果不是可保存的 MP4 视频")
        identity = hashlib.sha256(request.request_id.encode()).hexdigest()
        path = resolve_runtime_path(Path(self.settings.output_dir) / f"{identity}.mp4")
        try:
            atomic_write_bytes(path, content, replace=False)
        except FileExistsError:
            if path.read_bytes() != content:
                raise ToolError("同一视频任务返回不同内容，已保留原文件") from None
        # API 未提供实扣金额，保留待结算，不能按零结算。
        return VideoGenerationResult(status="succeeded", provider_job_id=task_id,
                                     location=str(path))
