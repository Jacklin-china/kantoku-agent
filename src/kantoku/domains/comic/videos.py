"""已确认故事的视频交接；任务与恢复由现有 GraphRuntime 执行。"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from kantoku.capabilities.video import VideoGenerationRequest
from kantoku.config import ToolError
from kantoku.core import budget
from kantoku.core.runtime.graph import END, START, WorkflowDefinition, WorkflowNode
from kantoku.core.runtime.models import (
    ArtifactLifecycle,
    ArtifactType,
    ExecutionStatus,
    RuntimeEventType,
)
from kantoku.core.state import RunState

from .models import ComicStoryMutation

VIDEO_WORKFLOW = "comic.story.video"


class ComicVideoRequest(ComicStoryMutation):
    content_sha256: str = Field(min_length=64, max_length=64)
    prompt: str = Field(min_length=1, max_length=2000)
    scope: Literal["shot", "story"] = "story"
    shot_id: str | None = None


class ComicVideoState(RunState):
    conversation_id: str
    project_id: str
    request_id: str
    request: dict
    snapshot: dict
    input_sha256: str
    instance_id: str
    trace_id: str
    artifact_id: str | None = None


class ComicVideoWorkspace:
    def __init__(self, stories, capability, *, instance_id):
        self.stories = stories
        self.media = stories.media
        self.store = stories.runtime
        self.capability = capability
        self.instance_id = instance_id

    def readiness(self):
        if (self.capability.provider.provider_id != "xiaoyunque"
                or not hasattr(self.capability.provider, "readiness")):
            return "请先配置小云雀视频服务"
        return self.capability.provider.readiness()

    def _story_context(self, story, manifest):
        return self.stories._text(story, manifest)

    def workflow(self):
        def generate(state, context):
            snapshot = state.snapshot
            request = VideoGenerationRequest(
                request_id=state.request_id, run_id=context.run_id, node_id=context.node_id,
                conversation_id=state.conversation_id, project=state.project_id,
                image_artifact_id=snapshot["images"][0]["artifact_id"],
                shot_no=snapshot["shots"][0]["sequence_number"],
                # 旧任务沿用原付费输入，新任务使用提交时冻结的完整制作指令。
                prompt=snapshot.get("generation_prompt", state.request["prompt"]),
                source_images=snapshot["images"],
                input_sha256=state.input_sha256,
                config={"duration_seconds": sum(shot["duration_seconds"]
                                                for shot in snapshot["shots"]),
                        "story_id": snapshot["story_id"], "story_version": snapshot["version"],
                        "shot_ids": [shot["shot_id"] for shot in snapshot["shots"]]},
            )
            artifact = self.capability.generate(request)
            if artifact.metadata.get("mock") or artifact.metadata.get("origin") != "real":
                raise ToolError("演示视频不能作为故事生成结果")
            return {"artifact_id": artifact.id}

        return WorkflowDefinition(id=VIDEO_WORKFLOW, domain="comic", state_type=ComicVideoState,
            nodes={"generate": WorkflowNode("generate", generate, retry_limit=0)},
            edges={START: "generate", "generate": END})

    def _owned_run(self, owner, run_id):
        anchor = self.stories.anchor_for(owner)
        run = self.store.get_run(run_id)
        if (run.workflow != VIDEO_WORKFLOW or run.domain != "comic"
                or run.state.get("conversation_id") != owner
                or run.state.get("project_id") != anchor):
            raise ToolError("视频任务不属于当前故事")
        return run

    def prepare(self, request):
        """在认领和付费前核对最新确认；快照引用原图和原顺序，不重选图。"""
        if not request.prompt.strip():
            raise ToolError("请输入视频提示词")
        for run in self.store.list_runs(domain="comic", conversation_id=request.conversation_id,
                                        workflow=VIDEO_WORKFLOW, limit=1000):
            reservation = budget.get_reservation(run.state["request_id"])
            if reservation and reservation.status == "unknown":
                raise ToolError("原视频请求结果待核对，请先查询原任务，不能重新生成")
            previous = run.state["request"]
            if (run.status in {ExecutionStatus.PENDING, ExecutionStatus.RUNNING,
                               ExecutionStatus.WAITING}
                    and previous["content_sha256"] == request.content_sha256
                    and previous["scope"] == request.scope
                    and previous["shot_id"] == request.shot_id):
                raise ToolError("当前故事范围已有视频任务，请查询原任务")
        with self.stories.projects._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            anchor, story = self.stories._prepare(connection, request, bind=False)
            view = self.stories.view(request.conversation_id, connection=connection)
            if (not story or story["content_sha256"] != request.content_sha256
                    or not view["confirmation"] or view["missing_images"]):
                raise ToolError("请先确认当前故事及其图片，再生成视频")
            manifest = view["manifest"]
            shots = (manifest["shots"] if request.scope == "story" else
                     [shot for shot in manifest["shots"] if shot["shot_id"] == request.shot_id])
            if not shots:
                raise ToolError("所选镜头不属于当前已确认故事")
            if request.scope == "story" and request.shot_id is not None:
                raise ToolError("整段故事生成不能同时指定单个镜头")
            sources = {(source["image_id"], source["version"]): source
                       for source in story["fixed_images"]}
            used = set()
            images = []
            for shot in shots:
                identity = (shot["image_id"], shot["image_version"])
                if identity in used:
                    continue
                used.add(identity)
                source = sources[identity]
                artifact = self.store.get_artifact(source["artifact_id"])
                images.append({"artifact_id": artifact.id, "run_id": artifact.run_id,
                               "sha256": source["sha256"], "image_id": source["image_id"],
                               "version": str(source["version"])})
            snapshot = {"story_id": story["story_id"], "version": story["version"],
                        "content_sha256": story["content_sha256"], "title": story["title"],
                        "shots": shots, "images": images}
            if request.scope == "story":
                prompt = self._story_context(story, manifest)
                addition = request.prompt
                copied = view["export_text"].strip()
                if copied and addition.strip().startswith(copied):
                    addition = addition.strip()[len(copied):].lstrip()
                if addition.strip():
                    prompt += "\n成片要求：\n" + addition
                if len(prompt) > 2000:
                    raise ToolError("故事与成片提示词合计超过 2000 字，"
                                    "请精简故事或补充要求后再生成")
                snapshot["generation_prompt"] = prompt
        if reason := self.readiness():
            raise ToolError(reason)
        return anchor, snapshot

    def saved_request(self, request):
        return self.media.request_result(request.anchor_project_id, "story.video", request)

    def bind_request(self, request, run):
        """复用原请求认领与事务结果；不推进故事或作品内容版本。"""
        saved = self.media.claim_request(request.anchor_project_id, "story.video", request,
                                        run_id=run.id)
        if saved:
            return saved
        with self.stories.projects._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self.media._finish(connection, request.anchor_project_id, "story.video", request,
                                      {"run_id": run.id}, advance_project=False)

    def project(self, run):
        state = run.state
        reservation = budget.get_reservation(state["request_id"])
        queryable = bool(reservation and reservation.provider_job_id and run.status in {
            ExecutionStatus.PENDING, ExecutionStatus.RUNNING, ExecutionStatus.WAITING})
        phase = run.status.value
        waiting = next((event.payload for event in reversed(self.store.list_events(run.id))
                        if event.event_type == RuntimeEventType.RUN_WAITING), {}) \
            if run.status == ExecutionStatus.WAITING else {}
        if phase == "completed":
            phase = "succeeded"
        elif phase == "waiting":
            phase = ("generating" if queryable and reservation.status != "unknown"
                     and waiting.get("kind") != "needs_reconciliation" else "unknown")
        elif phase in {"running", "pending"}:
            if state["instance_id"] != self.instance_id:
                phase = "generating" if queryable else "interrupted"
            else:
                phase = "generating" if phase == "running" else "queued"
        artifact_id = state.get("artifact_id")
        available = False
        if artifact_id:
            artifact = self.store.get_artifact(artifact_id)
            available = (artifact.type == ArtifactType.VIDEO
                         and artifact.lifecycle_state == ArtifactLifecycle.ACTIVE
                         and artifact.metadata.get("origin") == "real"
                         and not artifact.metadata.get("mock"))
        if phase == "succeeded" and not available:
            phase = "unavailable"
        error = run.error
        if phase == "unknown":
            error = waiting.get("message", "原视频请求待核对")
        return {"run_id": run.id, "request_id": state["request_id"], "status": run.status.value,
                "phase": phase, "can_query": queryable, "prompt": state["request"]["prompt"],
                "submitted_prompt": state["snapshot"].get("generation_prompt",
                                                         state["request"]["prompt"]),
                "scope": state["request"]["scope"], "snapshot": state["snapshot"],
                "artifact_id": artifact_id if available else None,
                "created_at": run.started_at.isoformat(), "error": error,
                "actual_fen": reservation.actual_fen if reservation else None}

    def view(self, owner):
        current = self.stories.view(owner)
        story = current["story"]
        reason = self.readiness()
        runs = self.store.list_runs(domain="comic", conversation_id=owner,
                                    workflow=VIDEO_WORKFLOW, limit=30)
        tasks = [self.project(run) for run in runs]
        confirmed = bool(current["confirmation"] and not current["missing_images"])
        return {"story_id": story["story_id"] if story else None,
                "story_version": story["version"] if story else 0,
                "content_sha256": story["content_sha256"] if story else None,
                "confirmed": confirmed, "manifest": current["manifest"] if confirmed else None,
                "story_context": self._story_context(story, current["manifest"])
                                 if confirmed else "",
                "provider": "xiaoyunque", "model_label": "小云雀 2.0",
                "credentials_configurable": (self.capability.provider.provider_id == "xiaoyunque"
                    and getattr(getattr(self.capability.provider, "settings", None),
                                "authentication", None) == "access_key"),
                "session_credentials_configured": bool(getattr(
                    self.capability.provider, "_credentials", None)),
                "ready": reason is None, "unavailable_reason": reason,
                "tasks": tasks}
