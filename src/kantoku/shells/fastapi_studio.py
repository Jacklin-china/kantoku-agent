"""FastAPI transport for the existing studio application and runtime."""

from __future__ import annotations

import gzip
import inspect
import json
import mimetypes
import re
import secrets
import socket
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.responses import JSONResponse as FastAPIJSONResponse
from loguru import logger
from starlette.concurrency import run_in_threadpool
from swagger_ui_bundle import swagger_ui_path

from kantoku.adapters import balance_snapshot
from kantoku.adapters.deepseek_balance import account_balance
from kantoku.config import KantokuError, ToolError
from kantoku.config.observability import public_error, request_trace
from kantoku.core import budget
from kantoku.core.authentication import AuthenticationError
from kantoku.domains.comic.models import ImageVersionRef
from kantoku.shells.web_studio import STATIC, StudioApplication

_SAFE_TRACE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
_DEV_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}
_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; "
    "style-src 'self'; script-src 'self'; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'"
)

# 允许压缩的静态资源类型。woff2/png/jpg 本身已压缩，再 gzip 只会浪费 CPU。
_COMPRESSIBLE_STATIC = ("text/", "application/javascript", "application/json", "image/svg+xml")
_STATIC_TYPES = frozenset({
    "text/javascript", "application/javascript", "text/css", "text/html",
    "application/json", "image/svg+xml", "application/manifest+json",
})
# Windows 的注册表映射缺 woff2，且有时把 .js 认成 text/plain；
# 字体类型不对会被浏览器拒绝加载，所以这里显式补上。
for _suffix, _type in ((".woff2", "font/woff2"), (".woff", "font/woff"),
                       (".js", "text/javascript"), (".mjs", "text/javascript")):
    mimetypes.add_type(_type, _suffix)


def static_asset_response(path: Path, request: Request) -> Response:
    """带内容哈希的前端资源：长缓存 + gzip。

    Vite 产出的文件名里带 hash，内容一变文件名就变，因此可以放心让浏览器
    长期缓存。此前这些文件被打上 no-store，每次进页面都要重新下载
    index.js / index.css / zpix.woff2 合计约 1.8MB，这是「页面很卡」的主因。
    """
    data = path.read_bytes()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    headers = {
        "Content-Type": mime if mime in _STATIC_TYPES or not mime.startswith("text/") else f"{mime}; charset=utf-8",
        "Cache-Control": "public, max-age=31536000, immutable",
    }
    accepts = request.headers.get("accept-encoding", "").lower()
    if "gzip" in accepts and mime.startswith(_COMPRESSIBLE_STATIC) and len(data) > 1024:
        data = gzip.compress(data, compresslevel=6)
        headers["Content-Encoding"] = "gzip"
        headers["Vary"] = "Accept-Encoding"
    return Response(data, headers=headers)


class JSONResponse(FastAPIJSONResponse):
    """Preserve the existing JSON wire format for established clients."""

    def render(self, content) -> bytes:
        return json.dumps(content, ensure_ascii=False).encode("utf-8")


def create_fastapi_app(studio: StudioApplication) -> FastAPI:
    """Create an isolated transport around the existing application instance."""
    api = FastAPI(title="Kantoku API", version="0.0.1", docs_url=None, redoc_url=None,
                  openapi_url=None, default_response_class=JSONResponse)
    closing = threading.Event()
    api.state.close_streams = closing.set

    @api.middleware("http")
    async def boundary(request: Request, call_next):
        supplied = request.headers.get("X-Trace-ID", "")
        trace_id = supplied if _SAFE_TRACE.fullmatch(supplied) else f"trace-{uuid4().hex[:12]}"
        request.state.trace_id = trace_id
        port = request.scope["server"][1]
        local_host = f"127.0.0.1:{port}"
        host_ok = request.headers.get("Host", "") in {local_host, f"localhost:{port}"}
        origin = request.headers.get("Origin")
        origin_ok = origin is None or origin in {
            f"http://{local_host}", f"http://localhost:{port}", *_DEV_ORIGINS,
        }
        cross_site = request.headers.get("Sec-Fetch-Site") == "cross-site"
        unauthenticated = {"/api/auth/login", "/api/auth/register"}
        protected_path = (request.url.path.startswith(("/api/", "/comic/projects", "/media/"))
                          or request.method in {"POST", "PUT", "PATCH", "DELETE"})
        docs_path = request.url.path in {"/docs", "/openapi.json"} or request.url.path.startswith(
            "/docs/assets/"
        )
        event_stream = (request.method == "GET" and request.url.path.startswith("/api/runs/")
                        and request.url.path.endswith("/events/stream"))
        token_required = (request.method != "OPTIONS" and protected_path and not event_stream
                          and request.url.path not in {
                              "/api/session", "/api/auth/status", *unauthenticated,
                          })
        bad_token = token_required and not secrets.compare_digest(
            request.headers.get("X-Studio-Token", ""), studio.token,
        )
        with request_trace(trace_id):
            logger.bind(component="api", request_id=trace_id).info(
                "request start method={} path={}", request.method, request.url.path,
            )
            try:
                if not host_ok or not origin_ok or cross_site or bad_token:
                    message = ("请从本机工作台入口访问" if request.method == "GET"
                               else "会话验证失败，请刷新页面")
                    response = JSONResponse({"error": message}, status_code=403)
                elif (request.method != "OPTIONS" and (protected_path or docs_path)
                      and request.url.path not in {"/api/auth/status", *unauthenticated}
                      and await run_in_threadpool(studio.authentication.registered)
                      and not await run_in_threadpool(
                          studio.authentication.valid_session,
                          request.cookies.get("kantoku_session", ""),
                      )):
                    response = JSONResponse({"error": "请先登录"}, status_code=401)
                else:
                    response = await call_next(request)
            except Exception as error:
                failure = public_error(error, component="api")
                response = JSONResponse(
                    {**failure, "error": failure["safe_message"]}, status_code=500,
                )
            response.headers["X-Trace-ID"] = trace_id
            # 带内容哈希的前端资源由静态路由自己声明长缓存；其余一律不缓存。
            # 旧实现对所有响应无条件写 no-store，导致每次进页面都把
            # index.js(572KB)+index.css(334KB)+zpix.woff2(944KB) 重新下一遍。
            if not response.headers.get("Cache-Control"):
                response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
            response.headers["Content-Security-Policy"] = _CONTENT_SECURITY_POLICY
            if response.headers.get("Content-Type") == "application/json":
                response.headers["Content-Type"] = "application/json; charset=utf-8"
            if origin in _DEV_ORIGINS:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Credentials"] = "true"
                response.headers["Access-Control-Expose-Headers"] = "X-Trace-ID"
                response.headers["Access-Control-Allow-Headers"] = (
                    "Content-Type, X-Studio-Token, X-Trace-ID, X-Kantoku-Login"
                )
                response.headers["Access-Control-Allow-Methods"] = (
                    "GET, POST, PUT, PATCH, DELETE, OPTIONS"
                )
                response.headers["Vary"] = "Origin"
            logger.bind(component="api", request_id=trace_id).info(
                "request end method={} path={}", request.method, request.url.path,
            )
            return response

    @api.exception_handler(OSError)
    @api.exception_handler(ValueError)
    @api.exception_handler(KantokuError)
    def application_error(request: Request, error: Exception):
        parts = request.url.path.removeprefix("/api").strip("/").split("/")
        context = {"component": "api"}
        if len(parts) >= 3 and parts[:2] == ["comic", "projects"]:
            context["project_id"] = parts[2]
            if len(parts) >= 5 and parts[3] == "assets":
                context["asset_id"] = parts[4]
        elif len(parts) >= 3 and parts[:2] == ["comic", "storyboards"]:
            context["storyboard_id"] = parts[2]
        elif len(parts) >= 3 and parts[:2] == ["comic", "shots"]:
            context["shot_id"] = parts[2]
        elif len(parts) >= 2 and parts[0] == "artifacts":
            context["artifact_id"] = parts[1]
        failure = getattr(error, "_kantoku_public_failure", None)
        if failure is None:
            failure = public_error(error, **context)
        status = 500 if isinstance(error, OSError) and request.method != "GET" else 400
        message = str(error) if isinstance(error, AuthenticationError) else failure["safe_message"]
        return JSONResponse({**failure, "error": message, "safe_message": message},
                            status_code=status)

    @api.get("/api/auth/status")
    def auth_status(request: Request):
        registered = studio.authentication.registered()
        return {"registered": registered, "authenticated": not registered or
                studio.authentication.valid_session(request.cookies.get("kantoku_session", ""))}

    @api.get("/api/session")
    def session():
        return {"token": studio.token}

    def require_account(request: Request) -> None:
        if not studio.authentication.valid_session(request.cookies.get("kantoku_session", "")):
            raise AuthenticationError("请先登录")

    @api.get("/api/account/profile")
    def account_profile(request: Request):
        require_account(request)
        cookie = request.cookies.get("kantoku_session", "")
        return {**studio.authentication.profile(),
                "session_started_at": studio.authentication.session_started_at(cookie)}

    @api.get("/api/account/provider-credentials")
    def provider_credentials(request: Request):
        require_account(request)
        return studio.provider_credentials_status()

    @api.post("/api/account/provider-credentials")
    async def update_provider_credentials(request: Request):
        require_account(request)
        if int(request.headers.get("Content-Length", "0")) > 4096:
            raise AuthenticationError("请求内容过长")
        body = await request.json()
        if not isinstance(body, dict):
            raise AuthenticationError("请求格式不正确")
        return await run_in_threadpool(studio.save_provider_credentials, body)

    @api.get("/api/account/text-balance")
    def text_balance(request: Request):
        require_account(request)
        return balance_snapshot(account_balance, "DeepSeek")

    @api.get("/api/account/creative-balance")
    def creative_balance(request: Request):
        require_account(request)
        return balance_snapshot(studio.creative_balance, "火山引擎")

    @api.post("/api/account/profile")
    async def update_account_profile(request: Request):
        require_account(request)
        if int(request.headers.get("Content-Length", "0")) > 710000:
            raise AuthenticationError("头像文件过大")
        body = await request.json()
        if (not isinstance(body, dict) or not isinstance(body.get("nickname"), str)
                or not isinstance(body.get("remove_avatar", False), bool)):
            raise AuthenticationError("资料格式不正确")
        return await run_in_threadpool(
            studio.authentication.update_profile, body["nickname"], body.get("avatar"),
            body.get("remove_avatar", False),
        )

    @api.post("/api/account/password")
    async def update_account_password(request: Request):
        require_account(request)
        if int(request.headers.get("Content-Length", "0")) > 4096:
            raise AuthenticationError("请求内容过长")
        body = await request.json()
        if (not isinstance(body, dict) or not isinstance(body.get("old_password"), str)
                or not isinstance(body.get("new_password"), str)):
            raise AuthenticationError("请求格式不正确")
        await run_in_threadpool(
            studio.authentication.change_password, body["old_password"],
            body["new_password"], request.cookies.get("kantoku_session", ""),
        )
        return {"updated": True}

    @api.get("/docs", include_in_schema=False)
    def docs():
        return HTMLResponse("""<!doctype html><html><head><meta charset="utf-8">
<title>Kantoku API</title>
<link rel="stylesheet" href="/docs/assets/swagger-ui.css"></head>
<body><div id="swagger-ui"></div>
<script src="/docs/assets/swagger-ui-bundle.js"></script>
<script src="/docs/assets/init.js"></script></body></html>""")

    @api.get("/openapi.json", include_in_schema=False)
    def openapi():
        return api.openapi()

    @api.get("/docs/assets/{asset}", include_in_schema=False)
    def docs_asset(asset: str):
        if asset == "init.js":
            source = """window.onload = () => SwaggerUIBundle({
url: '/openapi.json', dom_id: '#swagger-ui',
requestInterceptor: async request => {
  if (request.url.endsWith('/api/auth/login') || request.url.endsWith('/api/auth/register')) {
    request.headers['X-Kantoku-Login'] = '1';
  }
  if (!request.url.endsWith('/api/auth/status') &&
      !request.url.endsWith('/api/auth/login') &&
      !request.url.endsWith('/api/auth/register')) {
    const response = await fetch('/api/session', {credentials: 'same-origin'});
    if (response.ok) request.headers['X-Studio-Token'] = (await response.json()).token;
  }
  return request;
}});"""
            return Response(source, media_type="application/javascript")
        if asset not in {"swagger-ui.css", "swagger-ui-bundle.js"}:
            return JSONResponse({"error": "页面资源不存在"}, status_code=404)
        return Response((swagger_ui_path / asset).read_bytes(), media_type=(
            "text/css" if asset.endswith(".css") else "application/javascript"
        ))

    @api.options("/{path:path}")
    def options(path: str):
        return Response(status_code=204)

    @api.post("/api/auth/{action}")
    async def auth(request: Request, action: str):
        if action == "logout":
            studio.authentication.revoke_session(request.cookies.get("kantoku_session", ""))
            response = JSONResponse({"authenticated": False})
            response.headers["Set-Cookie"] = (
                "kantoku_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
            )
            return response
        if action not in {"register", "login"}:
            return JSONResponse({"error": "页面不存在"}, status_code=404)
        if (request.headers.get("X-Kantoku-Login") != "1" or
                request.headers.get("Content-Type", "").split(";")[0] != "application/json"):
            return JSONResponse({"error": "请求验证失败"}, status_code=403)
        try:
            length = int(request.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024:
                raise AuthenticationError("请求格式不正确")
            payload = json.loads(await request.body())
            if (not isinstance(payload, dict) or not isinstance(payload.get("username"), str)
                    or not isinstance(payload.get("password"), str)):
                raise AuthenticationError("请求格式不正确")
            operation = (studio.authentication.register if action == "register"
                         else studio.authentication.login)
            token = await run_in_threadpool(operation, payload["username"], payload["password"])
            response = JSONResponse({"authenticated": True})
            response.headers["Set-Cookie"] = (
                f"kantoku_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000"
            )
            return response
        except AuthenticationError as error:
            failure = public_error(error, component="authentication")
            return JSONResponse({**failure, "error": str(error)}, status_code=400)
        except (ValueError, OSError) as error:
            failure = public_error(error, component="api")
            return JSONResponse({**failure, "error": failure["safe_message"]}, status_code=400)
        except Exception as error:
            failure = public_error(error, component="api")
            return JSONResponse({**failure, "error": failure["safe_message"]}, status_code=503)

    @api.get("/api/runs")
    def runs():
        return {"runs": studio.list_core_runs()}

    @api.get("/api/runs/{run_id}")
    def run(run_id: str):
        return studio.get_core_run(run_id)

    @api.get("/api/runs/{run_id}/artifacts")
    def run_artifacts(run_id: str):
        return {"artifacts": studio.list_core_artifacts(run_id)}

    @api.get("/api/runs/{run_id}/events")
    def run_events(run_id: str, request: Request):
        after = int(request.query_params.get("after", "0") or 0)
        return {"events": studio.list_core_events(run_id, max(after, 0))}

    @api.get("/api/runs/{run_id}/events/stream")
    def run_event_stream(run_id: str, request: Request):
        query = parse_qs(request.url.query)
        if not secrets.compare_digest(query.get("token", [""])[0], studio.token):
            return JSONResponse({"error": "事件流会话验证失败"}, status_code=403)
        after = int(query.get("after", ["0"])[0] or 0)
        if request.headers.get("Last-Event-ID"):
            after = max(after, int(request.headers["Last-Event-ID"]))
        cookie = request.cookies.get("kantoku_session", "")

        def generate():
            cursor = after
            deadline = time.time() + 600
            while time.time() < deadline and not closing.is_set():
                try:
                    if (studio.authentication.registered()
                            and not studio.authentication.valid_session(cookie)):
                        return
                    events = studio.list_core_events(run_id, cursor)
                    for event in events:
                        cursor = max(cursor, int(event["sequence"]))
                        body = json.dumps(event, ensure_ascii=False)
                        yield (f"id: {event['sequence']}\nevent: {event['event_type']}\n"
                               f"data: {body}\n\n").encode()
                    terminal = studio.core_run_status(run_id) in {
                        "completed", "failed", "cancelled",
                    }
                    yield b": keep-alive\n\n"
                    if terminal and not events:
                        yield b"event: end\ndata: {}\n\n"
                        return
                    closing.wait(1.0)
                except (KantokuError, OSError, ValueError):
                    yield b"event: error\ndata: {}\n\n"
                    return

        return StreamingResponse(generate(), media_type="text/event-stream", headers={
            "Cache-Control": "no-store", "X-Accel-Buffering": "no",
        })

    @api.get("/api/approvals")
    def approvals():
        return {"approvals": studio.list_core_approvals()}

    @api.get("/api/batches")
    def batches():
        return {"batches": studio.list_core_batches()}

    @api.get("/api/skills")
    def skills():
        return {"skills": studio.list_core_skills()}

    @api.get("/api/comic/works")
    def works():
        return studio.comic_workspace.list()

    @api.get("/api/state")
    def state():
        return studio.state()

    @api.get("/api/comic/works/legacy-preview")
    def legacy_preview(request: Request):
        query = parse_qs(request.url.query)
        return studio.comic_workspace.legacy_preview(
            query.get("conversation_id", [""])[0], studio.comic_stories,
        )

    @api.get("/api/comic/works/{work_id}")
    def work(work_id: str):
        return studio.comic_workspace.get(work_id)

    @api.get("/api/comic/works/{work_id}/conversation")
    def work_conversation(work_id: str):
        return studio.comic_workspace.conversation(work_id)

    @api.get("/api/comic/works/{work_id}/requests/{request_id}")
    def work_request(work_id: str, request_id: str):
        return studio.comic_work_turn_status(work_id, request_id)

    @api.get("/api/comic/works/{work_id}/image-tasks/{task_id}/requests/{request_id}")
    def task_request(work_id: str, task_id: str, request_id: str):
        return studio.comic_workspace.turn_status(work_id, task_id, request_id)

    @api.get("/api/comic/works/{work_id}/image-tasks")
    def work_tasks(work_id: str):
        return {"tasks": studio.comic_workspace.get(work_id)["tasks"]}

    @api.get("/api/comic/works/{work_id}/image-tasks/{task_id}")
    def work_task(work_id: str, task_id: str):
        return studio.comic_workspace.get_task(work_id, task_id)

    @api.get("/api/comic/works/{work_id}/settings")
    def work_settings(work_id: str):
        studio.comic_workspace.get(work_id)
        return studio.list_comic_assets(work_id)

    @api.get("/api/comic/works/{work_id}/story")
    @api.get("/api/comic/works/{work_id}/story/{operation}")
    def work_story(work_id: str, request: Request, operation: str = "read"):
        query = parse_qs(request.url.query)
        version = int(query["version"][0]) if "version" in query else None
        return studio.comic_work_story(work_id, operation, version=version)

    @api.get("/api/comic/conversations/{conversation_id}/works")
    def conversation_works(conversation_id: str):
        return studio.comic_media.conversation_works(conversation_id)

    @api.get("/api/comic/conversations/{conversation_id}/story")
    @api.get("/api/comic/conversations/{conversation_id}/story/{operation}")
    def conversation_story(conversation_id: str, request: Request, operation: str = "read"):
        query = parse_qs(request.url.query)
        if operation == "read":
            version = int(query["version"][0]) if "version" in query else None
            return studio.get_comic_story(conversation_id, version=version)
        if operation == "images":
            return studio.comic_story_images(conversation_id)
        if operation == "versions":
            return studio.comic_story_versions(conversation_id)
        if operation == "requests":
            return studio.comic_story_requests(conversation_id)
        if operation == "video":
            return studio.comic_videos.view(conversation_id)
        return JSONResponse({"error": "故事接口不存在"}, status_code=404)

    @api.get("/api/comic/projects/{project_id}")
    @api.get("/comic/projects/{project_id}")
    def project(project_id: str, request: Request):
        query = parse_qs(request.url.query)
        version = int(query["version"][0]) if "version" in query else None
        if version is not None and version < 1:
            raise ToolError("作品版本必须大于零")
        return studio.get_comic_project(project_id, version=version)

    @api.get("/api/comic/projects/{project_id}/{section}")
    @api.get("/comic/projects/{project_id}/{section}")
    def project_section(project_id: str, section: str, request: Request):
        query = parse_qs(request.url.query)
        version = int(query["version"][0]) if "version" in query else None
        if version is not None and version < 1:
            raise ToolError("作品版本必须大于零")
        owner = query.get("conversation_id", [""])[0]
        if section == "context":
            return studio.get_comic_context(project_id, task=query.get("task", [None])[0],
                                            version=version, asset_ids=query.get("asset_id", []))
        if section == "assets":
            return studio.list_comic_assets(project_id, project_version=version)
        if section == "images":
            brief = int(query["brief_version"][0]) if "brief_version" in query else None
            return studio.list_comic_images(project_id, owner, brief_version=brief)
        if section == "image-workbench":
            return studio.comic_workbench_state(project_id, owner)
        if section == "references":
            return studio.get_comic_references(project_id, owner)
        if section in {"image-analyses", "edit-instructions", "image-comparisons"}:
            kind = {"image-analyses": "analysis", "edit-instructions": "edit",
                    "image-comparisons": "comparison"}[section]
            image = (ImageVersionRef(image_id=query["image_id"][0],
                                     version=int(query.get("image_version", ["1"])[0]))
                     if "image_id" in query else None)
            return studio.comic_image_reports(project_id, owner, kind, None, image=image,
                before=query.get("before", [None])[0], limit=int(query.get("limit", ["50"])[0]))
        if section == "tasks":
            return studio.list_comic_project_tasks(project_id)
        if section == "storyboards":
            return studio.list_comic_storyboards(project_id)
        if section == "prompts":
            return {"prompts": [studio.comic_prompts.payload(item)
                                for item in studio.comic_prompts.list(project_id)]}
        if section == "director-spec":
            return studio.get_comic_director(project_id)
        return JSONResponse({"error": "Comic API 路径不存在"}, status_code=404)

    @api.get("/api/comic/projects/{project_id}/{section}/{item_id}")
    @api.get("/comic/projects/{project_id}/{section}/{item_id}")
    def project_item(project_id: str, section: str, item_id: str, request: Request):
        query = parse_qs(request.url.query)
        version = int(query["version"][0]) if "version" in query else None
        if version is not None and version < 1:
            raise ToolError("作品版本必须大于零")
        if section == "assets":
            return studio.get_comic_asset(project_id, item_id, version=version)
        if section == "director-spec" and item_id == "versions":
            return studio.list_comic_director_versions(project_id)
        if section in {"image-analyses", "edit-instructions", "image-comparisons"}:
            kind = {"image-analyses": "analysis", "edit-instructions": "edit",
                    "image-comparisons": "comparison"}[section]
            image = (ImageVersionRef(image_id=query["image_id"][0],
                                     version=int(query.get("image_version", ["1"])[0]))
                     if "image_id" in query else None)
            return studio.comic_image_reports(project_id, query.get("conversation_id", [""])[0],
                kind, item_id, image=image, before=query.get("before", [None])[0],
                limit=int(query.get("limit", ["50"])[0]))
        return JSONResponse({"error": "Comic API 路径不存在"}, status_code=404)

    @api.get("/api/comic/projects/{project_id}/assets/{asset_id}/versions")
    @api.get("/comic/projects/{project_id}/assets/{asset_id}/versions")
    def asset_versions(project_id: str, asset_id: str):
        return studio.list_comic_asset_versions(project_id, asset_id)

    @api.get("/api/comic/images/{image_id}/versions")
    def image_versions(image_id: str, request: Request):
        query = parse_qs(request.url.query)
        return studio.comic_image_versions(image_id, query.get("conversation_id", [""])[0],
            before=int(query["before"][0]) if "before" in query else None,
            limit=int(query["limit"][0]) if "limit" in query else None)

    @api.get("/api/comic/images/{image_id}/review-draft")
    def review_draft(image_id: str, request: Request):
        query = parse_qs(request.url.query)
        return studio.comic_review_draft(image_id, int(query.get("version", ["1"])[0]),
                                         query.get("conversation_id", [""])[0])

    @api.get("/api/comic/storyboards/{storyboard_id}")
    def storyboard(storyboard_id: str):
        return studio.get_comic_storyboard(storyboard_id)

    @api.get("/api/comic/storyboards/{storyboard_id}/shots")
    def storyboard_shots(storyboard_id: str):
        return {"shots": [item.model_dump(mode="json")
                          for item in studio.comic_storyboards.list_shots(storyboard_id)]}

    @api.get("/api/comic/storyboards/{storyboard_id}/versions")
    def storyboard_versions(storyboard_id: str):
        return {"versions": [item.model_dump(mode="json")
                             for item in studio.comic_storyboards.versions(storyboard_id)]}

    @api.get("/api/comic/shots/{shot_id}")
    def shot(shot_id: str):
        return studio.comic_storyboards.get_shot(shot_id).model_dump(mode="json")

    @api.get("/api/comic/shots/{shot_id}/versions")
    def shot_versions(shot_id: str):
        return {"versions": [item.model_dump(mode="json")
                             for item in studio.comic_storyboards.shot_versions(shot_id)]}

    @api.get("/api/comic/shots/{shot_id}/prompt")
    def prompt(shot_id: str, request: Request):
        query = parse_qs(request.url.query)
        version = int(query["version"][0]) if "version" in query else None
        if version is not None and version < 1:
            raise ToolError("Prompt 版本必须大于零")
        return studio.comic_prompts.payload(studio.comic_prompts.get(shot_id, version=version))

    @api.get("/api/comic/shots/{shot_id}/prompt/versions")
    def prompt_versions(shot_id: str, request: Request):
        query = parse_qs(request.url.query)
        if "version" in query and int(query["version"][0]) < 1:
            raise ToolError("Prompt 版本必须大于零")
        return {"versions": [studio.comic_prompts.payload(item)
                             for item in studio.comic_prompts.versions(shot_id)]}

    @api.get("/api/conversations")
    def conversations(request: Request):
        query = parse_qs(request.url.query)
        return {"conversations": studio.list_conversations(
            domain=query.get("domain", [None])[0], query=query.get("q", [""])[0],
        )}

    @api.get("/api/conversations/{conversation_id}")
    def conversation(conversation_id: str):
        return studio.conversation(conversation_id)

    @api.get("/api/conversations/{conversation_id}/messages")
    def conversation_messages(conversation_id: str, request: Request):
        query = parse_qs(request.url.query)
        items = studio.runtime_store.list_conversation_messages(
            conversation_id, limit=int(query.get("limit", ["50"])[0]),
            before_id=query.get("before", [None])[0], after_id=query.get("after", [None])[0],
            message_ids=query.get("id"),
        )
        return {"messages": [item.model_dump(mode="json") for item in items],
                "next_before": items[0].id if items else None}

    @api.get("/api/artifacts")
    def artifacts(request: Request):
        query = parse_qs(request.url.query)
        run_id = query.get("run_id", [None])[0]
        return {"artifacts": studio.query_core_artifacts(
            run_id=run_id, type_name=query.get("type", [None])[0],
            domain=query.get("domain", [None])[0],
            lifecycle_state=query.get("lifecycle", [None if query.get("run_id") else "active"])[0],
        )}

    @api.get("/api/artifacts/{artifact_id}")
    def artifact(artifact_id: str):
        return studio.get_core_artifact(artifact_id)

    @api.get("/api/artifacts/{artifact_id}/content")
    def artifact_content(artifact_id: str):
        content = studio.get_core_artifact_content(artifact_id)
        return Response(content.read_bytes(), media_type=(
            mimetypes.guess_type(content.name)[0] or "application/octet-stream"
        ))

    @api.get("/api/batches/{batch_id}")
    def batch(batch_id: str):
        return studio._batch_payload(batch_id)

    async def payload(request: Request) -> dict:
        length = int(request.headers.get("Content-Length", "0"))
        path = request.url.path
        maximum = 65536
        if request.method == "POST" and re.fullmatch(
            r"/api/comic/(?:shots/[^/]+/external-image|projects/[^/]+/images|"
            r"images/[^/]+/restore-file)", path,
        ):
            maximum = 7 * 1024 * 1024
        if (request.method == "POST" and re.fullmatch(
                r"/api/comic/(?:conversations|works)/[^/]+/story/[^/]+", path)
                or request.method == "PUT" and re.fullmatch(
                    r"(?:/api)?/comic/conversations/[^/]+/story", path)):
            maximum = 1024 * 1024
        if not 0 < length <= maximum:
            raise ToolError("请求内容过长或为空")
        data = json.loads(await request.body())
        if not isinstance(data, dict):
            raise ToolError("请求格式不合法")
        return data

    @api.post("/api/comic/works", status_code=201)
    async def create_work(request: Request):
        return await run_in_threadpool(studio.comic_workspace.create, await payload(request))

    @api.post("/api/comic/works/legacy-import")
    async def import_legacy(request: Request):
        return await run_in_threadpool(
            studio.comic_workspace.import_legacy, await payload(request), studio.comic_stories,
        )

    @api.post("/api/comic/works/{work_id}/turns")
    async def work_turn(work_id: str, request: Request):
        return await run_in_threadpool(studio.comic_work_turn, work_id, await payload(request))

    @api.post("/api/comic/works/{work_id}/delete")
    async def delete_work(work_id: str, request: Request):
        return await run_in_threadpool(
            studio.comic_workspace.delete, work_id, await payload(request),
        )

    @api.post("/api/comic/works/{work_id}/recover-plans")
    async def recover_plans(work_id: str, request: Request):
        return await run_in_threadpool(
            studio.comic_workspace.recover_plans, work_id, await payload(request),
        )

    @api.post("/api/comic/works/{work_id}/image-tasks", status_code=201)
    async def create_work_task(work_id: str, request: Request):
        return await run_in_threadpool(
            studio.comic_workspace.create_task, work_id, await payload(request),
        )

    @api.post("/api/comic/works/{work_id}/image-tasks/{task_id}/turns")
    async def task_turn(work_id: str, task_id: str, request: Request):
        return await run_in_threadpool(
            studio.comic_task_turn, work_id, task_id, await payload(request),
        )

    @api.post("/api/comic/works/{work_id}/image-tasks/{task_id}/settings-sync")
    async def sync_settings(work_id: str, task_id: str, request: Request):
        return await run_in_threadpool(
            studio.comic_workspace.sync_settings, work_id, task_id, await payload(request),
        )

    @api.post("/api/comic/works/{work_id}/settings")
    async def publish_settings(work_id: str, request: Request):
        return await run_in_threadpool(
            studio.comic_workspace.publish_setting, work_id, await payload(request),
        )

    @api.post("/api/comic/works/{work_id}/rename")
    async def rename_work(work_id: str, request: Request):
        return await run_in_threadpool(
            studio.comic_workspace.rename, work_id, await payload(request),
        )

    @api.post("/api/conversations", status_code=201)
    async def create_conversation(request: Request):
        return await run_in_threadpool(studio.create_conversation, await payload(request))

    @api.post("/api/conversations/{conversation_id}/messages/stream")
    async def stream_conversation(conversation_id: str, request: Request):
        data = await payload(request)
        trace_id = request.state.trace_id
        data["_trace_id"] = trace_id
        cookie = request.cookies.get("kantoku_session", "")

        def generate():
            try:
                for event_name, event_payload in studio.stream_conversation(conversation_id, data):
                    if (studio.authentication.registered()
                            and not studio.authentication.valid_session(cookie)):
                        break
                    encoded = json.dumps(event_payload, ensure_ascii=False)
                    yield f"event: {event_name}\ndata: {encoded}\n\n".encode()
            except Exception as error:
                failure = public_error(error, component="conversation",
                                       conversation_id=conversation_id, trace_id=trace_id)
                encoded = json.dumps(failure, ensure_ascii=False)
                yield f"event: error\ndata: {encoded}\n\n".encode()

        return StreamingResponse(generate(), media_type="text/event-stream", headers={
            "Cache-Control": "no-store", "X-Accel-Buffering": "no", "X-Trace-ID": trace_id,
        })

    @api.patch("/api/conversations/{conversation_id}")
    async def update_conversation(conversation_id: str, request: Request):
        length = int(request.headers.get("Content-Length", "0"))
        if not 0 < length <= 4096:
            raise ToolError("请求格式不合法")
        data = await payload(request)
        if set(data) == {"fast_domain"}:
            return await run_in_threadpool(studio.set_fast_domain, conversation_id,
                                           data["fast_domain"])
        if set(data) == {"title"}:
            return await run_in_threadpool(studio.rename_conversation, conversation_id,
                                           str(data["title"]))
        raise ToolError("对话更新字段不合法")

    @api.delete("/api/conversations/{conversation_id}")
    def delete_conversation(conversation_id: str):
        return studio.delete_conversation(conversation_id)

    @api.post("/api/runs", status_code=201)
    async def create_run(request: Request):
        return await run_in_threadpool(studio.create_core_run, await payload(request))

    @api.post("/api/runs/{run_id}/resume")
    async def resume_run(run_id: str, request: Request):
        await payload(request)
        return await run_in_threadpool(studio.resume_core_run, run_id)

    @api.post("/api/runs/{run_id}/cancel")
    async def cancel_run(run_id: str, request: Request):
        await payload(request)
        return await run_in_threadpool(studio.cancel_core_run, run_id)

    @api.post("/api/approvals/{approval_id}/{action}")
    async def decide_approval(approval_id: str, action: str, request: Request):
        return await run_in_threadpool(
            studio.decide_core_approval, approval_id, action, await payload(request),
        )

    @api.post("/api/batches", status_code=201)
    async def create_batch(request: Request):
        return await run_in_threadpool(studio.create_core_batch, await payload(request))

    @api.post("/api/batches/{batch_id}/cancel")
    async def cancel_batch(batch_id: str, request: Request):
        await payload(request)
        return await run_in_threadpool(studio.cancel_core_batch, batch_id)

    @api.post("/api/artifacts/{artifact_id}/{action}")
    async def change_artifact(artifact_id: str, action: str, request: Request):
        if action not in {"trash", "restore", "purge"}:
            raise ToolError("不支持的操作")
        return await run_in_threadpool(
            studio.change_core_artifact, artifact_id, action, await payload(request),
        )

    # These endpoints share body handling but retain separate FastAPI routes.
    # Each handler calls the existing application or domain service directly.
    write_routes = [
        ("/api/video/credentials", 200, lambda p, d: studio.configure_video_credentials(d)),
        ("/api/comic/works/{work_id}/story/{operation}", 200,
         lambda p, d: studio.comic_work_story(p["work_id"], p["operation"], d)),
        ("/api/comic/conversations/{conversation_id}/story/{operation}", 200,
         lambda p, d: studio.comic_story_operation(p["conversation_id"], p["operation"], d)),
        ("/api/comic/images/{image_id}/restore-file", 200,
         lambda p, d: studio.restore_comic_image_file(p["image_id"], d)),
        ("/api/comic/projects", 201, lambda p, d: studio.create_comic_project(d)),
        ("/comic/projects", 201, lambda p, d: studio.create_comic_project(d)),
    ]
    project_actions = [
        ("images", 201, lambda p, d: studio.import_comic_image(p["project_id"], d)),
        ("images/{image_id}/delete", 200,
         lambda p, d: studio.delete_comic_image(p["project_id"], p["image_id"], d)),
        ("assets", 201, lambda p, d: studio.create_comic_asset(p["project_id"], d)),
        ("director-spec", 201, lambda p, d: studio.create_comic_director(p["project_id"], d)),
        ("director-spec/restore", 201,
         lambda p, d: studio.restore_comic_director(p["project_id"], d)),
        ("director-spec/delete", 200,
         lambda p, d: studio.delete_comic_director_version(p["project_id"], d)),
        ("director-spec/confirm", 200,
         lambda p, d: studio.confirm_comic_director(p["project_id"], d)),
        ("storyboards", 201, lambda p, d: studio.create_comic_storyboard(p["project_id"], d)),
    ]
    for action in ("restore", "lock", "delete"):
        project_actions.append((f"assets/{{asset_id}}/{action}", 201,
            lambda p, d, operation=action: studio.change_comic_asset(
                p["project_id"], p["asset_id"], operation, d)))
    for route, kind in (("image-analyses", "analysis"), ("edit-instructions", "edit"),
                        ("image-comparisons", "comparison")):
        project_actions.append((route, 201, lambda p, d, operation=kind:
            studio.comic_image_operation(p["project_id"], operation, d)))

    def confirm_instruction(p, d):
        if d.get("instruction_artifact_id") != p["artifact_id"]:
            raise ToolError("确认的指令身份不一致")
        return studio.comic_image_operation(p["project_id"], "confirm", d)

    project_actions.append(("edit-instructions/{artifact_id}/confirm", 200, confirm_instruction))
    for suffix, status, handler in project_actions:
        for prefix in ("/api/comic/projects/{project_id}/", "/comic/projects/{project_id}/"):
            write_routes.append((prefix + suffix, status, handler))

    write_routes.extend([
        ("/api/comic/storyboards/{storyboard_id}/shots", 201,
         lambda p, d: studio.create_comic_shot(p["storyboard_id"], d)),
        ("/api/comic/storyboards/{storyboard_id}/restore", 201,
         lambda p, d: studio.restore_comic_storyboard(p["storyboard_id"], d)),
        ("/api/comic/shots/{shot_id}/prompt/compile", 201,
         lambda p, d: studio.compile_comic_prompt(p["shot_id"], d)),
        ("/api/comic/shots/{shot_id}/prompt/codex-task", 200,
         lambda p, d: studio.export_comic_codex_task(p["shot_id"], d)),
        ("/api/comic/shots/{shot_id}/prompt/confirm", 200,
         lambda p, d: studio.confirm_comic_prompt(p["shot_id"], d)),
        ("/api/comic/shots/{shot_id}/external-image", 201,
         lambda p, d: studio.import_comic_external_image(p["shot_id"], d)),
        ("/api/comic/shots/{shot_id}/prompt/restore", 201,
         lambda p, d: studio.restore_comic_prompt(p["shot_id"], d)),
        ("/api/comic/shots/{shot_id}/prompt/delete", 200,
         lambda p, d: studio.delete_comic_prompt(p["shot_id"], d)),
    ])
    for action in ("restore", "delete"):
        write_routes.append((f"/api/comic/shots/{{shot_id}}/{action}", 201,
            lambda p, d, operation=action: studio.change_comic_shot(p["shot_id"], operation, d)))

    def decide_media(p, d):
        if d.get("decision") not in {"approve", "reject"}:
            raise ToolError("费用确认操作无效")
        return studio.decide_media_cost(p["conversation_id"], p["request_id"],
                                        d["decision"] == "approve")

    write_routes.append(("/api/conversations/{conversation_id}/media-jobs/{request_id}/approval",
                         200, decide_media))

    def register_json_route(path, status, handler, method):
        async def endpoint(request: Request, **params):
            return await run_in_threadpool(handler, params, await payload(request))

        parameters = [inspect.Parameter("request", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                        annotation=Request)]
        parameters.extend(inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                            annotation=str)
                          for name in re.findall(r"\{([^}]+)\}", path))
        endpoint.__signature__ = inspect.Signature(parameters)
        endpoint.__name__ = method.lower() + "_" + re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_")
        api.add_api_route(path, endpoint, methods=[method], status_code=status, openapi_extra={
            "requestBody": {"required": True, "content": {"application/json": {
                "schema": {"type": "object"},
            }}},
        })

    for path, status, handler in write_routes:
        register_json_route(path, status, handler, "POST")

    put_routes = [
        ("projects/{project_id}/brief", lambda p, d: studio.update_comic_brief(p["project_id"], d)),
        ("projects/{project_id}/assets/{asset_id}",
         lambda p, d: studio.edit_comic_asset(p["project_id"], p["asset_id"], d)),
        ("storyboards/{storyboard_id}",
         lambda p, d: studio.edit_comic_storyboard(p["storyboard_id"], d)),
        ("shots/{shot_id}", lambda p, d: studio.edit_comic_shot(p["shot_id"], d)),
        ("shots/{shot_id}/prompt", lambda p, d: studio.edit_comic_prompt(p["shot_id"], d)),
        ("images/{image_id}/selection", lambda p, d: studio.select_comic_image(p["image_id"], d)),
        ("projects/{project_id}/references",
         lambda p, d: studio.bind_comic_references(p["project_id"], d)),
        ("projects/{project_id}/image-workbench",
         lambda p, d: studio.save_comic_workbench(p["project_id"], d)),
        ("images/{image_id}/review-draft",
         lambda p, d: studio.save_comic_review_draft(p["image_id"], d)),
        ("conversations/{conversation_id}/story",
         lambda p, d: studio.comic_story_operation(p["conversation_id"], "save", d)),
    ]
    for suffix, handler in put_routes:
        for prefix in ("/api/comic/", "/comic/"):
            register_json_route(prefix + suffix, 200, handler, "PUT")

    @api.get("/media/{task_id:path}")
    def media(task_id: str):
        task = studio.task(task_id)
        result = budget.load_generation_result(task.request_id)
        if result is None or result.path is None or not result.path.is_file():
            raise ToolError("原图片不可用")
        return Response(result.path.read_bytes(), media_type="image/png")

    @api.get("/{path:path}", include_in_schema=False)
    def frontend(path: str, request: Request):
        from urllib.parse import unquote

        route = "/" + path
        app_routes = {"/", "/workspace", "/runs", "/approvals", "/batches", "/assets", "/account",
                      "/history", "/skills", "/settings", "/tasks"}
        if route in app_routes or route.startswith(
            ("/runs/", "/domain/", "/workspace/", "/tasks/")
        ):
            html = (STATIC / "index.html").read_text(encoding="utf-8")
            return HTMLResponse(html.replace("__TOKEN__", studio.token))
        if route.startswith("/assets/"):
            candidate = (STATIC / unquote(route).removeprefix("/")).resolve()
            if not candidate.is_relative_to(STATIC.resolve()) or not candidate.is_file():
                return JSONResponse({"error": "页面资源不存在"}, status_code=404)
            return static_asset_response(candidate, request)
        return JSONResponse({"error": "页面不存在"}, status_code=404)

    @api.post("/api/{action:path}", include_in_schema=False)
    async def legacy_action(action: str, request: Request):
        await run_in_threadpool(studio.start, action, await payload(request))
        return JSONResponse({"accepted": True}, status_code=202)

    original_openapi = api.openapi
    documented_schema = None

    def documented_openapi():
        nonlocal documented_schema
        if documented_schema is not None:
            return documented_schema
        schema = original_openapi()
        queries = {
            "/api/artifacts": ("run_id", "type", "domain", "lifecycle"),
            "/api/conversations": ("domain", "q"),
            "/api/conversations/{conversation_id}/messages": ("limit", "before", "after", "id"),
            "/api/runs/{run_id}/events": ("after",),
            "/api/runs/{run_id}/events/stream": ("after", "token"),
            "/api/comic/works/legacy-preview": ("conversation_id",),
            "/api/comic/works/{work_id}/story": ("version",),
            "/api/comic/works/{work_id}/story/{operation}": ("version",),
            "/api/comic/conversations/{conversation_id}/story": ("version",),
            "/api/comic/images/{image_id}/versions": ("conversation_id", "before", "limit"),
            "/api/comic/images/{image_id}/review-draft": ("conversation_id", "version"),
            "/api/comic/shots/{shot_id}/prompt": ("version",),
        }
        for prefix in ("/api/comic/projects", "/comic/projects"):
            queries[f"{prefix}/{{project_id}}"] = ("version",)
            for suffix in ("/{section}", "/{section}/{item_id}"):
                queries[f"{prefix}/{{project_id}}{suffix}"] = (
                    "version", "conversation_id", "task", "asset_id", "brief_version",
                    "image_id", "image_version", "before", "limit",
                )
        for path, methods in schema.get("paths", {}).items():
            for method, operation in methods.items():
                if method in {"post", "put", "patch"} and "requestBody" not in operation:
                    operation["requestBody"] = {"required": True, "content": {
                        "application/json": {"schema": {"type": "object"}},
                    }}
                if method == "get" and path in queries:
                    existing = {item["name"] for item in operation.get("parameters", [])}
                    operation.setdefault("parameters", []).extend(
                        {"name": name, "in": "query", "required": False,
                         "schema": {"type": "string"}}
                        for name in queries[path] if name not in existing
                    )
        documented_schema = schema
        return documented_schema

    api.openapi = documented_openapi
    api.openapi()

    return api


class StudioFastAPIServer:
    """Run the ASGI app with the existing studio server lifecycle contract."""

    def __init__(self, studio: StudioApplication, port: int = 0) -> None:
        self.app = create_fastapi_app(studio)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.socket.bind(("127.0.0.1", port))
            self.socket.listen(2048)
            self.server_port = self.socket.getsockname()[1]
        except Exception:
            self.socket.close()
            raise
        close_streams = self.app.state.close_streams

        class Server(uvicorn.Server):
            def handle_exit(self, sig, frame):
                close_streams()
                super().handle_exit(sig, frame)

        self.server = Server(uvicorn.Config(
            self.app, host="127.0.0.1", port=self.server_port,
            access_log=False, log_level="warning",
        ))
        self._stopped = threading.Event()
        self._started = threading.Event()

    def serve_forever(self) -> None:
        self._started.set()
        try:
            self.server.run(sockets=[self.socket])
        finally:
            self.app.state.close_streams()
            self._stopped.set()

    def shutdown(self) -> None:
        self.app.state.close_streams()
        self.server.should_exit = True
        if self._started.is_set() and not self._stopped.wait(timeout=30):
            raise RuntimeError("FastAPI server did not shut down")

    def server_close(self) -> None:
        self.shutdown()
        self.socket.close()
