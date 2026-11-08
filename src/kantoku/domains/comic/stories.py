"""会话故事的不可变修订、人工确认与材料导出；复用媒体及 Core 记录。"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_STORED, ZipFile

from pydantic import ValidationError

from kantoku.config import ToolError
from kantoku.config.observability import current_run_id, current_trace_id, public_error
from kantoku.config.runtime_paths import (
    atomic_write_bytes,
    get_runtime_paths,
    validate_runtime_path,
)
from kantoku.core.conversations import InteractionMode
from kantoku.core.runtime.models import ArtifactLifecycle, ArtifactRecord, ArtifactType, utc_now

from .constraints import image_edit_constraint_basis
from .director_provenance import value_hash
from .models import (
    ComicAsset,
    ComicPromptArtifact,
    ComicStoryDraft,
    ComicStoryFinding,
    ComicStoryFindingAudits,
    ComicStoryReviewOutput,
    ComicStoryShot,
    CreativeConstraint,
    ImageVersion,
    ImageVersionRef,
)
from .prompts import prompt_constraints
from .story_structure import structural_findings

STORY_CONTRACT = "comic-story-14"
STORY_REPAIR_LIMIT = 6
STORY_SEMANTICS = (
    "这是由静态图片发展为视频的故事。图片及其生成来源描述起始画面，角色/物体身份、固定资产与"
    "明确持续生效的硬要求必须保持；起始姿态和机位不等于整段视频必须静止。"
    "image_sources的reference_role=initial_frame：constraints及原始要求是该起始画面的来源依据，"
    "scope、trust、source_verified只说明原要求的对象及可信来源，不自行证明它必须贯穿视频。"
    "持续性须根据source_quote、effective_requirement、固定资产及本轮要求的真实语义判断；"
    "原图片编译器的negative_prompt属于单张图片生成规避词，不能建立视频全程禁令。"
    "有合理时间顺序和连续性时，可加入后续动作、对白、表情、运镜与转场；"
    "不能仅因原图片提示词没写这些后续事件就判为错误，也不能把合理先后变化当作同时冲突。"
    "无明确精确数量要求时，近义的规模描述或文案不统一至多是优化建议。"
    "真正错误包括起始状态衔接缺失、同一时刻互斥要求、身份/硬约束冲突、时长无法容纳必需动作；"
    "必须结合本轮故事要求和对应图片来源判断，不得取消用户要求的合法剧情来迎合静态图描述。"
    "user_intent若存在，是本次整理前的用户框架，须保留其叙事目标、合法事件、角色及对白；"
    "有错的执行方式可调整，并补足前后动作衔接，时长、运镜和转场须协调，不要求逐字照抄。"
    "user_intent为空时，以当前draft及instruction为目标，不追溯旧稿撤销用户新编辑。"
    "user_intent中的执行方式是待整理的旧框架，不是当前稿事实或不可更改的硬要求；"
    "允许修正姿态、速度、武器衔接及机位执行方式，已纠正的旧矛盾不能继续判为当前错误。"
    "每张图的来源要求只在该图对应镜头的起始画面生效，不把相邻图的道具或机位倒灌到本镜。"
    "来源之间有差异时，可以用合理的先后动作交代衔接，不强行抹平各图事实；"
    "只有明确持续身份或禁令才跨镜保持，无法同时满足的明确要求应指出，不能反复换一种改法。"
    "景别和相机移动是不同维度，特写可跟随主体；先后切换机位不是同时冲突。"
    "节奏快慢、重复站立或建议缩短镜头通常是warning；只有可说明的物理/时序或硬要求冲突才是error。"
    "程度或局部效果须按当前文字的明确范围判断，不能把局部微弱效果推断为全局强烈效果，"
    "也不能仅因近义表达不逐字相同就判为来源冲突。"
    "已满足合理连续性、可执行性和本轮要求的方案可通过，不为给出建议而新增审美偏好、"
    "额外剧情或重复修正；所有发现仍须有当前稿的具体依据。"
    "单镜的时间顺序是图片起始状态→action→transition→下一镜图片起始状态；"
    "camera在这一过程中执行，duration_seconds须容纳本镜动作和转场。"
    "transition中已明确发生的取物、换持或移动是有效剧情，不要求action重复写一遍；"
    "上一镜取物后、下一镜已经持有该物是顺接，不能反称为缺少取物。"
    "持续约束和起始画面不得为统一称谓而改写。建议本身也须符合全部相关图片来源，"
    "不能建议把一图的固定道具改成另一图的道具；应说明物品放回何处、从何处取出及先后动作。"
    "对连续三个起始画面各自要求 A、B、A 的情形，分别安排 A→B 和 B→A 两次衔接，"
    "不能把后三镜的道具统一成 B；起始画面要求必须落在各自对应的镜头。"
    "shot_boundaries明确切镜前后的边界：两张起始图之间必需的换持、起身或移位，"
    "须在前镜action/transition内完成，不能放在已经具有新状态的后镜开头重演。"
    "不要按来源数组的顺序猜镜头归属，应核对applies_to_shots与shot_source_map；"
    "同图可以重复入镜，每次都须检查它自己的前后边界。"
)
_SUFFIXES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


def _key(image) -> tuple[str, int]:
    return (
        (image["image_id"], image["version"])
        if isinstance(image, dict)
        else (image.image_id, image.version)
    )


def _parse(raw, schema):
    try:
        if not isinstance(raw, str) or len(raw) > 160000:
            raise ValueError("capacity")
        return schema.model_validate_json(raw)
    except (ValueError, ValidationError):
        raise ToolError(
            "故事模型未返回有效的公开结构", detail="module=comic.story schema=invalid"
        ) from None


class ComicStoryService:
    """只表达故事业务；运行生命周期与具体模型调用由既有 Shell 注入。"""

    def __init__(self, media, workspace=None):
        self.media = media
        self.projects, self.runtime = media.projects, media.runtime
        self.workspace = workspace

    def story_id(self, conversation_id):
        work_id = self.workspace.work_for_story(conversation_id) if self.workspace else None
        return "comic-story-" + value_hash(work_id or conversation_id)[:32]

    def _require_image(self, ref, anchor, conversation_id, *, connection=None,
                       allow_related=True, snapshot=None):
        frozen = (ImageVersion.model_validate_json(json.dumps(snapshot, ensure_ascii=False))
                  if snapshot is not None else None)
        work_id = (
            self.workspace.work_for_story(conversation_id, connection=connection)
            if self.workspace
            else None
        )
        if work_id:
            if anchor != work_id:
                raise ToolError("故事所属作品不一致")
            return self.workspace.require_story_image(
                ref, work_id, connection=connection, snapshot=frozen)
        return self.media.require_image(
            ref, anchor, conversation_id, connection=connection, allow_related=allow_related,
            snapshot=frozen,
        )

    def _conversation(self, conversation_id):
        conversation = self.runtime.get_conversation(conversation_id)
        if (
            conversation.domain != "comic"
            or conversation.interaction_mode != InteractionMode.GUIDED
        ):
            raise ToolError(
                "故事必须属于当前 Comic 创作会话", detail="module=comic.story owner=mode"
            )

    def _anchor(self, connection, conversation_id):
        self._conversation(conversation_id)
        if self.workspace and (
            work_id := self.workspace.work_for_story(conversation_id, connection=connection)
        ):
            return work_id
        story_id = self.story_id(conversation_id)
        rows = connection.execute(
            "SELECT project_id,payload_json FROM comic_entity_versions "
            "WHERE entity_type='story_anchor' AND entity_id=? ORDER BY version DESC",
            (story_id,),
        ).fetchall()
        owners = {row["project_id"] for row in rows}
        if len(owners) > 1:
            raise ToolError(
                "故事绑定出现冲突，请查看运行记录", detail="module=comic.story anchor=conflict"
            )
        if not rows:
            return None
        payload = json.loads(rows[0]["payload_json"])
        if (
            payload.get("conversation_id") != conversation_id
            or payload.get("anchor_project_id") not in owners
        ):
            raise ToolError("故事归属记录不一致", detail="module=comic.story anchor=identity")
        self.media.require_owner(
            payload["anchor_project_id"], conversation_id, connection=connection
        )
        return payload["anchor_project_id"]

    def anchor_for(self, conversation_id, anchor_project_id=None):
        """只读解析，首次绑定必须由写事务完成。"""
        with self.projects._connect() as connection:
            anchor = self._anchor(connection, conversation_id)
            if anchor and anchor_project_id and anchor != anchor_project_id:
                raise ToolError("当前聊天的故事已绑定另一作品，请重新读取故事")
            if not anchor and anchor_project_id:
                self.projects.get(anchor_project_id)
                self.media.require_owner(anchor_project_id, conversation_id, connection=connection)
            return anchor or anchor_project_id

    def _current(self, connection, anchor, conversation_id):
        if not anchor:
            return 0, {}
        return self.media._latest(connection, anchor, "story", self.story_id(conversation_id))

    def _prepare(self, connection, request, *, bind=True):
        anchor = self._anchor(connection, request.conversation_id)
        if anchor and anchor != request.anchor_project_id:
            raise ToolError("故事所属作品已固定，请重新读取后保存")
        anchor = anchor or request.anchor_project_id
        self.media._write_guard(connection, anchor, request)
        version, story = self._current(connection, anchor, request.conversation_id)
        if version != request.expected_story_version:
            raise ToolError(
                "故事已在其他页面更新，请核对当前版本；生成候选仍保留在原运行记录",
                detail="module=comic.story cas=story",
            )
        if bind and not self._anchor(connection, request.conversation_id):
            self.projects._insert_version(
                connection,
                project_id=anchor,
                entity_type="story_anchor",
                entity_id=self.story_id(request.conversation_id),
                version=1,
                created_at=utc_now(),
                payload_json=json.dumps(
                    {"conversation_id": request.conversation_id, "anchor_project_id": anchor},
                    ensure_ascii=False,
                ),
            )
        return anchor, story

    def conversation_candidates(self, conversation_id):
        self._conversation(conversation_id)
        if (
            self.workspace
            and (result := self.workspace.candidates(conversation_id, self)) is not None
        ):
            return result
        with self.projects._connect() as connection:
            rows = connection.execute(
                "SELECT project_id,entity_id,payload_json FROM comic_entity_versions v "
                "WHERE entity_type='image_selection' AND version=(SELECT MAX(version) "
                "FROM comic_entity_versions WHERE project_id=v.project_id "
                "AND entity_type=v.entity_type AND entity_id=v.entity_id) AND ("
                "EXISTS (SELECT 1 FROM comic_entity_versions i WHERE i.project_id=v.project_id "
                "AND i.entity_type='image' AND i.entity_id=v.entity_id "
                "AND i.version=json_extract(v.payload_json,'$.selected_version') "
                "AND json_extract(i.payload_json,'$.conversation_id')=?) OR "
                "EXISTS (SELECT 1 FROM artifacts a WHERE a.id=v.entity_id "
                "AND a.conversation_id=? AND a.source='comic.external_image' "
                "AND json_extract(a.metadata_json,'$.project_id')=v.project_id "
                "AND json_extract(v.payload_json,'$.selected_version')=1)) "
                "ORDER BY created_at,entity_id",
                (conversation_id, conversation_id),
            ).fetchall()
            result, unavailable = [], []
            for row in rows:
                selection = json.loads(row["payload_json"])
                ref = ImageVersionRef(
                    image_id=row["entity_id"], version=selection["selected_version"]
                )
                try:
                    image = self.media.get(ref.image_id, version=ref.version,
                                           connection=connection, _legacy_history=True)
                except ToolError as error:
                    unavailable.append({**ref.model_dump(mode="json"),
                                        "reason": error.message})
                    continue
                if image.conversation_id != conversation_id:
                    continue
                self.media.require_owner(image.project_id, conversation_id, connection=connection)
                item = {
                    **self.media.image_view(image),
                    "selection": self.media.selection_view(image.project_id, selection,
                        conversation_id=conversation_id, connection=connection),
                    "source_project_title": self.projects.get(image.project_id).project.title,
                }
                if not item["available"]:
                    item["error"] = item["unavailable_reason"]
                result.append(item)
        return {"images": result, "unavailable_images": unavailable}

    def _images(self, draft, anchor, conversation_id, *, previous=None, connection=None):
        """新引用核对当前采用，既有固定引用只核对内容和归属。"""
        trusted = {_key(item): item for item in previous or []}
        result = {}
        for index, shot in enumerate(draft.shots, 1):
            identity = _key(shot.image)
            if identity in result:
                continue
            try:
                image = self._require_image(
                    shot.image, anchor, conversation_id, connection=connection, allow_related=True,
                    snapshot=trusted.get(identity),
                )
            except ToolError as error:
                raise ToolError(
                    f"镜头 {index} 的图片不可用：{error.message}",
                    detail="module=comic.story stage=images shot_id=" + shot.shot_id,
                ) from error
            frozen = image.model_dump(mode="json")
            if identity in trusted:
                # 快照已经过同一媒体校验；保留原序列化与指纹，不改写历史。
                frozen = trusted[identity]
            else:
                if connection is None:
                    with self.projects._connect() as opened:
                        selection = self.media._latest(
                            opened, image.project_id, "image_selection", image.image_id
                        )[1]
                else:
                    selection = self.media._latest(
                        connection, image.project_id, "image_selection", image.image_id
                    )[1]
                if selection.get("selected_version") != image.version:
                    raise ToolError(
                        "新入镜图片必须是当前聊天明确采用的版本",
                        detail="module=comic.story image=unselected",
                    )
            result[identity] = frozen
        return sorted(result.values(), key=_key)

    def _source_contexts(self, images, *, previous=None):
        """仅读取固定图片实际关联的不可变来源，不把当前作品理解当历史图事实。"""
        frozen = {_key(item["image"]): item for item in previous or []}
        result = []
        for image in images:
            if _key(image) in frozen:
                result.append(frozen[_key(image)])
                continue
            with self.projects._connect() as connection:
                imported = self.media._latest(
                    connection,
                    image["project_id"],
                    "legacy_image",
                    f"{image['image_id']}:{image['version']}",
                )[1]
            if imported:
                source = imported["source_context"]
                result.append(
                    {
                        **source,
                        "image": {"image_id": image["image_id"], "version": image["version"]},
                        "source_project_id": image["project_id"],
                        "image_artifact_id": image["artifact_id"],
                        "legacy_source": {
                            "image_id": imported["original"]["image_id"],
                            "version": imported["original"]["version"],
                            "source_project_id": imported["original"]["project_id"],
                        },
                    }
                )
                continue
            source = {
                "image": {"image_id": image["image_id"], "version": image["version"]},
                "source_project_id": image["project_id"],
                "image_artifact_id": image["artifact_id"],
                "instruction_artifact_id": image["instruction_artifact_id"],
                "source_kind": image["provenance"],
                "constraints": [],
                "negative_prompt": "",
            }
            if not image["instruction_artifact_id"]:
                result.append(source)
                continue
            artifact = self.runtime.get_artifact(image["instruction_artifact_id"])
            owner = artifact.conversation_id
            if not owner and artifact.run_id:
                owner = self.runtime.get_run(artifact.run_id).state.get("conversation_id")
            if (
                artifact.type != ArtifactType.PROMPT
                or artifact.status != "ready"
                or artifact.lifecycle_state != ArtifactLifecycle.ACTIVE
                or owner != image["conversation_id"]
                or artifact.metadata.get("project_id") != image["project_id"]
            ):
                raise ToolError("图片固定指令的归属或状态不合法")
            if artifact.metadata.get("image_operation") == "edit":
                self.media.require_edit_confirmation(artifact.id)
                payload = artifact.metadata.get("payload", {})
                if (
                    artifact.metadata.get("sha256") != value_hash(payload)
                    or payload.get("basis", {}).get("source_image") != image["parent"]
                ):
                    raise ToolError("图片修改来源的指纹或父版本不一致")
                scope = dict(payload["scope"])
                receipt = payload["constraint_receipt"]["review"]
                scope["constraint_resolution"] = {
                    **scope.get("constraint_resolution", {}),
                    "constraint_replacements": receipt.get("constraint_replacements", []),
                }
                constraints = [
                    CreativeConstraint.model_validate(item)
                    for item in [
                        *payload["basis"]["constraints"],
                        *payload.get("edit_constraints", []),
                    ]
                ]
                constraints = [
                    item
                    for item in constraints
                    if item.status == "active" and item.origin != "model_choice"
                ]
                source.update(
                    source_kind="confirmed_edit",
                    constraints=image_edit_constraint_basis(scope, constraints),
                    authorized_requirements=scope["authorized_requirements"],
                    negative_prompt=payload["output"].get("negative_prompt", ""),
                )
            else:
                metadata = artifact.metadata
                with self.projects._connect() as connection:
                    prompt = ComicPromptArtifact.model_validate_json(
                        self.projects._version_payload(
                            connection,
                            image["project_id"],
                            "prompt",
                            metadata["prompt_id"],
                            artifact.version,
                        )
                    )
                    if (
                        prompt.artifact_id != artifact.id
                        or prompt.positive_prompt != metadata.get("positive_prompt")
                        or prompt.negative_prompt != metadata.get("negative_prompt")
                    ):
                        raise ToolError("图片 Prompt 与固定修订不一致")
                    assets = []
                    refs = [*prompt.character_asset_versions, *prompt.scene_asset_versions]
                    if prompt.style_version:
                        refs.append(prompt.style_version)
                    for ref in refs:
                        assets.append(
                            ComicAsset.model_validate_json(
                                self.projects._version_payload(
                                    connection,
                                    image["project_id"],
                                    "asset",
                                    ref.asset_id,
                                    ref.version,
                                )
                            )
                        )
                snapshot = self.projects.get(image["project_id"], version=prompt.project_version)
                constraints = prompt_constraints(snapshot, assets)
                source.update(
                    source_kind="prompt_output",
                    constraints=[
                        item.model_dump(mode="json")
                        for item in constraints
                        if item.status == "active" and item.origin != "model_choice"
                    ],
                    negative_prompt=prompt.negative_prompt,
                    source_versions={
                        "prompt": prompt.version,
                        "creative_brief": prompt.creative_brief_version,
                    },
                )
                if snapshot.creative_brief.constraint_contract_version != 2:
                    source["user_requirements"] = snapshot.creative_brief.hard_constraints
                    source["asset_requirements"] = [
                        item for asset in assets for item in asset.fixed_constraints
                    ]
            result.append(source)
        return result

    @staticmethod
    def _content_hash(draft, fixed_images, source_contexts):
        return value_hash(
            {
                "draft": draft.model_dump(mode="json"),
                "fixed_images": fixed_images,
                "source_contexts": source_contexts,
            }
        )

    def _insert_story(
        self, connection, request, draft, fixed_images, source, run_id, source_contexts
    ):
        now = utc_now()
        artifact_id = "artifact-" + uuid4().hex
        story = {
            **draft.model_dump(mode="json"),
            "story_id": self.story_id(request.conversation_id),
            "conversation_id": request.conversation_id,
            "anchor_project_id": request.anchor_project_id,
            "version": request.expected_story_version + 1,
            "content_sha256": self._content_hash(draft, fixed_images, source_contexts),
            "fixed_images": fixed_images,
            "source_contexts": source_contexts,
            "source": source,
            "run_id": run_id,
            "artifact_id": artifact_id,
            "created_at": now.isoformat(),
            "schema_version": 1,
        }
        self.runtime.insert_artifact(
            connection,
            ArtifactRecord(
                id=artifact_id,
                type=ArtifactType.DOCUMENT,
                run_id=run_id,
                conversation_id=request.conversation_id,
                node_id="story.save",
                source="comic.story",
                status="ready",
                created_at=now,
                version=story["version"],
                metadata={
                    "project_id": request.anchor_project_id,
                    "story_id": story["story_id"],
                    "sha256": story["content_sha256"],
                    "payload": story,
                    "request_id": request.request_id,
                    "trace_id": current_trace_id(),
                },
            ),
        )
        self.projects._insert_version(
            connection,
            project_id=request.anchor_project_id,
            entity_type="story",
            entity_id=story["story_id"],
            version=story["version"],
            payload_json=json.dumps(story, ensure_ascii=False),
            created_at=now,
        )
        return story

    @staticmethod
    def _draft(story):
        return ComicStoryDraft.model_validate(
            {key: story[key] for key in ("title", "summary", "instruction", "shots")}
        )

    FIELD_LABELS = {
        "title": "故事标题", "summary": "故事概述", "instruction": "本轮故事要求",
        "action": "动作", "camera": "运镜", "duration_seconds": "时长",
        "transition": "转场", "image": "图片",
    }

    # 兼容已有服务和测试调用；规则本身集中在 story_structure.py。
    _structural_findings = staticmethod(structural_findings)

    @staticmethod
    def _structural_review(findings):
        return {"status": "needs_revision", "findings": findings}

    def _review_current(self, connection, story, model):
        """内容没变就沿用原校验结论：同一份初稿点两次校验，结论不能自相矛盾。

        换了校验模型则重新核验，避免旧模型的结论盖住新口径。
        """
        payload = self.media._latest(
            connection, story["anchor_project_id"], "story_review",
            f"{story['story_id']}:{story['version']}",
        )[1]
        return bool(
            payload
            and payload.get("content_sha256") == story["content_sha256"]
            and payload.get("contract") == STORY_CONTRACT
            and payload.get("model") == model
        )

    @staticmethod
    def _review_allows_confirmation(story, review):
        """主动校验或同一生成运行的内部复核均可授权当前内容；恢复候选须主动校验。"""
        return bool(
            review
            and review.get("contract") == STORY_CONTRACT
            and review.get("content_sha256") == story.get("content_sha256")
            and review.get("story_version") == story.get("version")
            and review["status"] != "needs_revision"
            and not any(item["severity"] == "error" for item in review["findings"])
            and (review.get("validation_requested") or (
                story.get("source") == "model_generated"
                and story.get("run_id")
                and review.get("run_id") == story["run_id"]
            ))
        )

    @staticmethod
    def _complete(draft):
        findings = ComicStoryService._structural_findings(draft)
        if findings:
            raise ToolError("；".join(item["message"] for item in findings))

    def _manifest(self, story):
        images, filenames = [], {}
        for image in story["fixed_images"]:
            content = (image["sha256"], image["mime_type"])
            if content not in filenames:
                filenames[content] = (
                    f"images/image-{len(filenames) + 1:03d}-{image['sha256'][:8]}"
                    f"{_SUFFIXES[image['mime_type']]}"
                )
            images.append(
                {
                    "image_id": image["image_id"],
                    "version": image["version"],
                    "artifact_id": image["artifact_id"],
                    "source_project_id": image["project_id"],
                    "sha256": image["sha256"],
                    "mime_type": image["mime_type"],
                    "filename": filenames[content],
                }
            )
        by_ref = {_key(image): image for image in images}
        shots = [
            {
                "shot_id": shot["shot_id"],
                "sequence_number": index,
                "image_id": shot["image"]["image_id"],
                "image_version": shot["image"]["version"],
                "filename": by_ref[_key(shot["image"])]["filename"],
                **{
                    field: shot[field]
                    for field in ("action", "camera", "duration_seconds", "transition")
                },
            }
            for index, shot in enumerate(story["shots"], 1)
        ]
        return {
            "story_id": story["story_id"],
            "version": story["version"],
            "content_sha256": story["content_sha256"],
            "images": images,
            "shots": shots,
        }

    @staticmethod
    def _text(story, manifest):
        shots = manifest["shots"]
        timed = bool(shots) and all(isinstance(shot["duration_seconds"], (float, int))
                                   and shot["duration_seconds"] > 0 for shot in shots)
        total = f"{sum(shot['duration_seconds'] for shot in shots):g} 秒" if timed else "待完善"
        lines = [
            "完整视频生成提示词",
            story["title"] or "故事视频",
            f"故事版本：v{story['version']}",
            f"制作目标：将以下 {len(shots)} 个分镜与对应参考图制作成一条完整、连贯的视频，"
            f"目标总时长 {total}。请交付一条成片，按时间轴完整表现剧情与收尾。",
            "",
            "全片叙事主线",
            story["summary"],
            "",
            "全片制作要求",
            story["instruction"],
            "",
            "跨镜连续性",
            "保持已确认的人物、服装、道具和美术风格；每镜从对应参考图的起始状态展开。"
            "将前镜的动作结束、人物位置、视线、运动方向及道具状态接入后镜，"
            "不同状态之间用明确动作或转场交代，不产生无叙事依据的道具、姿态或位置跳变。"
            "运镜跟随剧情节奏，保留动作完成过程；对白、字幕和声音按用户要求与剧情同步，"
            "跨镜自然延续，末镜按指定收尾结束。",
            "",
            "参考图对应（按分镜首次使用顺序上传）",
        ]
        references = {}
        for shot in shots:
            references.setdefault(shot["filename"], None)
        lines.extend(f"参考图 {index}：{filename}"
                     for index, filename in enumerate(references, 1))
        lines += ["", "连续分镜时间轴"]
        elapsed = 0.0
        for shot in shots:
            duration = shot["duration_seconds"]
            timing = (f"{elapsed:g}–{elapsed + duration:g} 秒" if timed
                      else f"目标 {duration if duration is not None else '未填写'} 秒")
            lines += [
                "",
                f"镜头 {shot['sequence_number']}｜{timing}｜{shot['filename']}",
                f"画面与动作：{shot['action']}",
                f"镜头运动：{shot['camera']}",
                f"本镜结束与衔接：{shot['transition']}",
            ]
            if timed:
                elapsed += duration
        return "\n".join(lines) + "\n"

    def _export_record(self, artifact_id, *, connection=None):
        if connection is None:
            artifact = self.runtime.get_artifact(artifact_id)
        else:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE id=?", (artifact_id,)
            ).fetchone()
            if row is None:
                raise ToolError("找不到素材包产物")
            artifact = self.runtime._artifact(row)
        path = validate_runtime_path(Path(artifact.location or ""))
        if (
            artifact.type != ArtifactType.DOCUMENT
            or artifact.status != "ready"
            or artifact.lifecycle_state != ArtifactLifecycle.ACTIVE
            or artifact.source != "comic.story.export"
            or not artifact.location
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != artifact.metadata.get("sha256")
        ):
            raise ToolError("素材包已失效、缺失或内容已改变，请核对原导出")
        return {
            "artifact_id": artifact.id,
            "filename": path.name,
            "content_url": f"/api/artifacts/{artifact.id}/content",
            "sha256": artifact.metadata["sha256"],
            "story_version": artifact.metadata["story_version"],
            "available": True,
        }

    def view(self, conversation_id, *, version=None, connection=None):
        if connection is None:
            with self.projects._connect() as opened:
                return self.view(conversation_id, version=version, connection=opened)
        anchor = self._anchor(connection, conversation_id)
        current_version, story = self._current(connection, anchor, conversation_id)
        if version is not None:
            if not anchor:
                raise ToolError("找不到指定故事版本")
            story = json.loads(
                self.projects._version_payload(
                    connection, anchor, "story", self.story_id(conversation_id), version
                )
            )
        project_version = self.projects.get(anchor).project.current_version if anchor else None
        result = {
            "story": story or None,
            "review": None,
            "confirmation": None,
            "can_confirm": False,
            "export_text": "",
            "manifest": None,
            "missing_images": [],
            "exports": [],
            "project_version": project_version,
            "story_version": story.get("version", 0),
            "current_story_version": current_version,
            "anchor_project_id": anchor,
        }
        if not story:
            return result
        identity = f"{story['story_id']}:{story['version']}"
        for kind in ("review", "confirmation"):
            payload = self.media._latest(connection, anchor, "story_" + kind, identity)[1]
            if payload.get("content_sha256") == story["content_sha256"]:
                result[kind] = payload
        if result["confirmation"] and result["confirmation"].get("review_sha256") != value_hash(
            result["review"]
        ):
            result["confirmation"] = None
        result["manifest"] = self._manifest(story)
        result["export_text"] = self._text(story, result["manifest"])
        for image in story["fixed_images"]:
            try:
                self._require_image(
                    ImageVersionRef(image_id=image["image_id"], version=image["version"]),
                    anchor,
                    conversation_id,
                    connection=connection,
                    allow_related=True,
                    snapshot=image,
                )
            except ToolError as error:
                affected = [shot for shot in story["shots"] if _key(shot["image"]) == _key(image)]
                result["missing_images"].extend(
                    {
                        "shot_id": shot["shot_id"],
                        "reason": str(error),
                        "source_project_id": image["project_id"],
                        "image_id": image["image_id"],
                        "version": image["version"],
                        "artifact_id": image["artifact_id"],
                        "error": str(error),
                    }
                    for shot in affected
                )
        rows = connection.execute(
            "SELECT payload_json FROM comic_entity_versions "
            "WHERE project_id=? AND entity_type='story_export' AND entity_id=? "
            "ORDER BY version DESC",
            (anchor, identity),
        ).fetchall()
        for row in rows:
            saved = json.loads(row["payload_json"])
            try:
                result["exports"].append(
                    self._export_record(saved["artifact_id"], connection=connection)
                )
            except ToolError as error:
                result["exports"].append({**saved, "available": False, "error": str(error)})
        result["can_confirm"] = not result["missing_images"] and self._review_allows_confirmation(
            story, result["review"])
        return result

    def versions(self, conversation_id):
        with self.projects._connect() as connection:
            anchor = self._anchor(connection, conversation_id)
            rows = (
                connection.execute(
                    "SELECT payload_json FROM comic_entity_versions "
                    "WHERE project_id=? AND entity_type='story' AND entity_id=? "
                    "ORDER BY version DESC",
                    (anchor, self.story_id(conversation_id)),
                ).fetchall()
                if anchor
                else []
            )
        return {"versions": [json.loads(row["payload_json"]) for row in rows]}

    def _finish(self, connection, request, operation, *, run_id=None, **extra):
        result = {**self.view(request.conversation_id, connection=connection), **extra}
        if run_id:
            result["run_id"] = run_id
        return self.media._finish(
            connection, request.anchor_project_id, operation, request, result, advance_project=False
        )

    def save(self, request, *, run_id=None, imported_images=None):
        with self.projects._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if saved := self.media.request_result(
                request.anchor_project_id, "story.save", request, connection=connection
            ):
                return saved
            anchor, previous = self._prepare(connection, request)
            if imported_images is not None:
                for image in imported_images:
                    mapping = self.media._latest(
                        connection,
                        image["project_id"],
                        "legacy_image",
                        f"{image['image_id']}:{image['version']}",
                    )[1]
                    if not mapping or mapping["original"]["sha256"] != image["sha256"]:
                        raise ToolError("历史故事引用必须具有已核验的导入映射")
            images = self._images(
                request.draft,
                anchor,
                request.conversation_id,
                previous=previous.get("fixed_images") or imported_images,
                connection=connection,
            )
            sources = self._source_contexts(images, previous=previous.get("source_contexts"))
            if self._content_hash(request.draft, images, sources) != previous.get("content_sha256"):
                self._insert_story(
                    connection, request, request.draft, images, "human_edit", run_id, sources
                )
            return self._finish(connection, request, "story.save", run_id=run_id)

    def _candidate(self, run_id, payload):
        run = self.runtime.get_run(run_id)
        self.runtime.update_run(
            run_id,
            status=run.status,
            state={**run.state, "story_candidate": payload},
            current_node=run.current_node,
            error=run.error,
        )

    def _execute(self, request, operation, run_id, action):
        if saved := self.media.claim_request(
            request.anchor_project_id, operation, request, run_id=run_id
        ):
            return saved
        try:
            return action()
        except Exception as error:
            try:
                self.media.fail_request(request.anchor_project_id, request, error)
            except Exception as persistence_error:
                public_error(
                    persistence_error,
                    component="comic.story.failure_record",
                    project_id=request.anchor_project_id,
                    request_id=request.request_id,
                    run_id=run_id,
                )
            raise

    @staticmethod
    def _evidence_quote(value, evidence):
        """只接受同字段原文或确定性等价表示，返回实际字段中的引用。"""
        fragment = evidence.strip()
        if not fragment:
            return None
        if isinstance(value, ImageVersionRef):
            if fragment == value.image_id:
                return fragment
            try:
                ref = ImageVersionRef.model_validate_json(fragment)
            except (ValueError, ValidationError):
                return None
            return json.dumps(value.model_dump(), ensure_ascii=False) if ref == value else None
        if isinstance(value, (int, float)):
            number = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(?:s|秒)?",
                                  fragment)
            return str(value) if number and float(number[1]) == value else None
        if not isinstance(value, str):
            return None
        if fragment in value:
            return fragment
        # 换行/连续空格属于排版；保留词边界，不删字或做语义模糊匹配。
        pattern = r"\s+".join(re.escape(part) for part in re.split(r"\s+", fragment))
        match = re.search(pattern, value)
        return match[0] if match else None

    @staticmethod
    def _review_fields(draft):
        """字段引用绑定位置及当前值，避免模型复述长文、混用修复前的证据。"""
        fields = {}
        for field in ("title", "summary", "instruction"):
            fields[f"story.{field}:{value_hash(getattr(draft, field))[:16]}"] = {
                "shot_id": None, "field": field}
        for index, shot in enumerate(draft.shots, 1):
            for field in ("action", "camera", "duration_seconds", "transition", "image"):
                value = getattr(shot, field)
                if isinstance(value, ImageVersionRef):
                    value = value.model_dump(mode="json")
                fields[f"shot.{index}.{field}:{value_hash([shot.shot_id, value])[:16]}"] = {
                    "shot_id": shot.shot_id, "field": field}
        return fields

    def _review_findings(self, draft, output):
        shots = {shot.shot_id: (index, shot) for index, shot in enumerate(draft.shots, 1)}
        fields = self._review_fields(draft)
        checked, issues = [], []
        for index, finding in enumerate(output.findings, 1):
            coordinate = fields.get(finding.evidence_id)
            # 当前值绑定的引用已经唯一定位镜头；允许模型省略冗余 shot_id，不猜测显式错误身份。
            if coordinate and finding.shot_id is None and coordinate["field"] == finding.field:
                finding.shot_id = coordinate["shot_id"]
            position = "故事"
            if finding.shot_id:
                entry = shots.get(finding.shot_id)
                if entry is None or finding.field not in {
                    "action", "camera", "duration_seconds", "transition", "image",
                }:
                    issues.append({"finding": index, "reason": "invalid_shot_or_field",
                                   "message": f"第 {index} 条故事审核引用了不存在的镜头或字段"})
                    continue
                position, shot = f"镜头 {entry[0]}", entry[1]
                value = getattr(shot, finding.field)
            elif finding.field in {"title", "summary", "instruction"}:
                value = getattr(draft, finding.field)
            else:
                issues.append({"finding": index, "reason": "missing_shot_id",
                               "message": f"第 {index} 条故事审核缺少对应镜头身份"})
                continue
            if finding.evidence_id is not None:
                if fields.get(finding.evidence_id) != {
                    "shot_id": finding.shot_id, "field": finding.field,
                }:
                    expected = next(ref for ref, target in fields.items() if target == {
                        "shot_id": finding.shot_id, "field": finding.field})
                    issues.append({"finding": index, "field": finding.field,
                                   "reason": "invalid_field_reference",
                                   "expected_field_reference": expected,
                                   "message": f"{position}的审核引用不属于当前字段，请重新校验"})
                    continue
                if not finding.evidence:
                    finding.evidence = (
                        value.model_dump_json()
                        if isinstance(value, ImageVersionRef) else str(value)
                    )[:4000]
            quote = self._evidence_quote(value, finding.evidence)
            if quote is None:
                issues.append({"finding": index, "field": finding.field,
                               "reason": "evidence_mismatch",
                               "message": f"{position} · {self.FIELD_LABELS[finding.field]}："
                               "故事审核证据与当前字段不一致，请重新校验"})
                continue
            finding.evidence = quote
            if any(ref not in fields for ref in finding.related_fields):
                issues.append({"finding": index, "reason": "invalid_related_field",
                               "message": f"第 {index} 条审核的关联字段不属于当前故事"})
                continue
            if finding.timing_plan and finding.code != "duration_conflict":
                budget_shot = shots[finding.shot_id][1] if finding.shot_id in shots else None
                phases = {field for field in ("action", "transition")
                          if budget_shot and getattr(budget_shot, field).strip()}
                budget = sum(beat.seconds for beat in finding.timing_plan)
                if (not budget_shot or not phases or budget <= 0
                        or {beat.field for beat in finding.timing_plan} != phases
                        or any(self._evidence_quote(
                            getattr(budget_shot, beat.field), beat.evidence) is None
                            for beat in finding.timing_plan)
                        or (finding.field == "duration_seconds" and finding.severity == "error"
                            and budget_shot.duration_seconds is not None
                            and budget <= budget_shot.duration_seconds + 0.001)):
                    # 保留问题本身，但不把没有充分依据的节拍表交给改稿模型。
                    finding.timing_plan = []
            if finding.code == "duration_conflict" and (
                finding.severity == "error" or finding.timing_plan
            ):
                # 语义时长判断必须给出当前动作和转场的完整预算，不能凭动作数量逐轮加秒。
                shot = shots[finding.shot_id][1] if finding.shot_id in shots else None
                required = {field for field in ("action", "transition")
                            if shot and getattr(shot, field).strip()}
                # 只纠正可以在本镜另一个阶段唯一找到的逐字引用；绝不接纳虚构或其他镜头内容。
                for beat in finding.timing_plan:
                    if (shot and self._evidence_quote(
                            getattr(shot, beat.field), beat.evidence) is None):
                        matches = [phase for phase in required if self._evidence_quote(
                            getattr(shot, phase), beat.evidence) is not None]
                        if len(matches) == 1:
                            beat.field = matches[0]
                budget = sum(beat.seconds for beat in finding.timing_plan)
                if (not shot or not finding.timing_plan or budget <= 0
                    or {beat.field for beat in finding.timing_plan} != required
                    or any(self._evidence_quote(getattr(shot, beat.field), beat.evidence) is None
                           for beat in finding.timing_plan)
                    or (finding.severity == "error" and shot.duration_seconds is not None
                        and budget <= shot.duration_seconds + 0.001)):
                    issues.append({"finding": index, "reason": "invalid_timing_plan",
                        "message": f"{position}的时长判断缺少当前动作与转场的有效节拍预算",
                        "required_phases": sorted(required),
                        "provided_phases": sorted({beat.field for beat in finding.timing_plan}),
                        "budget_seconds": budget,
                        "quote_mismatch_beats": [index for index, beat in enumerate(
                            finding.timing_plan) if shot and self._evidence_quote(
                                getattr(shot, beat.field), beat.evidence) is None],
                        "minimum_error_budget_exclusive": shot.duration_seconds if shot else None})
                    continue
            if not finding.suggestion.strip():
                finding.suggestion = f"请修改{self.FIELD_LABELS[finding.field]}：{finding.message}"
            checked.append(finding)
        if output.status != "pass" and not output.findings:
            issues.append({"reason": "missing_findings",
                           "message": "故事校验未给出问题位置和修改建议，请重新校验"})
        return checked, issues

    @staticmethod
    def _review_response(raw):
        """只暴露安全结构坐标；一个坏字段不能抹掉同一响应内已合法的其他发现。"""
        issue = {"reason": "invalid_public_structure", "message": "故事模型未返回有效的公开结构"}
        try:
            if not isinstance(raw, str) or len(raw) > 160000:
                raise ValueError("response_capacity")
            text = raw.strip()
            fenced = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", text)
            payload = json.loads(fenced.group(1) if fenced else text)
        except (ValueError, TypeError):
            return None, [{**issue, "schema_errors": [{"loc": [], "type": "json_invalid"}]}]
        try:
            return ComicStoryReviewOutput.model_validate(payload), []
        except ValidationError as error:
            allowed = {"findings", "status", "seconds", *ComicStoryFinding.model_fields}
            diagnostics = [{"loc": [part if isinstance(part, int) or part in allowed
                                     else "unknown_field" for part in item["loc"]],
                            "type": item["type"]} for item in error.errors(
                                include_input=False, include_context=False, include_url=False)[:20]]
        # 仅把完整符合领域 Schema 的单条发现交给后续引文和时长核验；非法字段值不进入纠正。
        findings = []
        if isinstance(payload, dict) and isinstance(payload.get("findings"), list):
            for item in payload["findings"][:100]:
                try:
                    findings.append(ComicStoryFinding.model_validate(item))
                except ValidationError:
                    continue
        return ComicStoryReviewOutput(status="needs_revision", findings=findings), [
            {**issue, "schema_errors": diagnostics}]

    @staticmethod
    def _story_sources(source_contexts):
        """视频只接收有依据的起始帧来源；单图编译负向词不是用户的持续要求。"""
        return [{**{key: value for key, value in source.items() if key != "negative_prompt"},
                 "reference_role": "initial_frame"} for source in source_contexts]

    @staticmethod
    def _shot_source_map(draft, source_contexts):
        """来源按固定图片身份关联，编号指向同一请求的 image_sources，不复制或混用来源正文。"""
        indices = {_key(source["image"]): index for index, source in enumerate(source_contexts)}
        return {shot.shot_id: {"image": shot.image.model_dump(mode="json"),
                    "image_source_index": indices.get(_key(shot.image)),
                    "previous_shot_id": draft.shots[index - 1].shot_id if index else None,
                    "next_shot_id": (draft.shots[index + 1].shot_id
                                     if index + 1 < len(draft.shots) else None)}
                for index, shot in enumerate(draft.shots)} if draft else {}

    def _story_context(self, draft, source_contexts, *, images=None):
        """模型输入按镜头首次使用排序，显式列出切镜边界；不改变持久化快照与内容指纹。"""
        positions, uses = {}, {}
        for index, shot in enumerate(draft.shots if draft else []):
            positions.setdefault(_key(shot.image), index)
            uses.setdefault(_key(shot.image), []).append(shot.shot_id)
        ordered = sorted(source_contexts, key=lambda source: positions.get(
            _key(source["image"]), len(draft.shots) if draft else 0))
        sources = self._story_sources(ordered)
        for source in sources:
            source["applies_to_shots"] = uses.get(_key(source["image"]), [])
        mapping = self._shot_source_map(draft, ordered)
        fields = self._review_fields(draft) if draft else {}
        references = {(value["shot_id"], value["field"]): ref for ref, value in fields.items()}
        boundaries = []
        for previous, following in zip(draft.shots, draft.shots[1:], strict=False) if draft else []:
            boundaries.append({
                "from_shot_id": previous.shot_id, "to_shot_id": following.shot_id,
                "from_source_index": mapping[previous.shot_id]["image_source_index"],
                "to_source_index": mapping[following.shot_id]["image_source_index"],
                "handoff_field": references[(previous.shot_id, "transition")],
                "before_cut_fields": [references[(previous.shot_id, field)] for field in (
                    "action", "camera", "transition", "duration_seconds")],
                "after_cut_start_field": references[(following.shot_id, "action")],
            })
        result = {"image_sources": sources, "shot_source_map": mapping,
                  "shot_boundaries": boundaries}
        if images is not None:
            result["images"] = [{key: image[key] for key in (
                "image_id", "version", "filename", "provenance")}
                for image in sorted(images, key=lambda item: positions.get(
                    _key(item), len(draft.shots) if draft else 0))]
        return result

    def _audit_finding_quotes(self, draft, output, model_call, source_contexts):
        """一次核对旧引文及跨图衔接；反证与建议更正必须绑定当前字段和真实来源。"""
        fields = self._review_fields(draft)
        shots = {shot.shot_id: shot for shot in draft.shots}
        positions = {shot.shot_id: index for index, shot in enumerate(draft.shots)}
        source_fields = {}
        for index, source in enumerate(source_contexts):
            requirements = [item.get("effective_requirement", item.get("requirement", ""))
                            for item in source.get("constraints", [])
                            if item.get("status", "active") == "active"
                            and item.get("origin") != "model_choice"]
            for key in ("user_requirements", "asset_requirements"):
                requirements.extend(source.get(key, []))
            requirements.extend(item.get("requirement", "") for item in
                                source.get("authorized_requirements", []) if isinstance(item, dict))
            for text in dict.fromkeys(item for item in requirements if isinstance(item, str)):
                if text.strip():
                    source_fields[f"source.{index}:{value_hash(text)[:16]}"] = {
                        "image": source["image"], "quote": text}

        def field_value(ref):
            coordinate = fields[ref]
            return getattr(shots.get(coordinate["shot_id"], draft), coordinate["field"])

        suspects, references, quotes, temporal, source_refs, source_images = {}, {}, {}, {}, {}, {}
        for index, finding in enumerate(output.findings, 1):
            primary = next(ref for ref, coordinate in fields.items() if coordinate == {
                "shot_id": finding.shot_id, "field": finding.field})
            refs = {primary, *finding.related_fields}
            fragments = re.findall(r'“([^”]+)”|"([^"\n]+)"', finding.message)
            absent = [left or right for left, right in fragments
                      if not any(self._evidence_quote(field_value(ref), left or right)
                                 for ref in refs)]
            if finding.field == "duration_seconds":
                # 只把声称当前可用时长的数字列为待核对原文；建议或目标时长不作为现稿事实。
                patterns = [
                    r"(?<![\d.])(\d+(?:\.\d+)?\s*秒)(?=\s*(?:内|无法|不足|难以|不够|偏短|过短))",
                    r"(?:当前(?:时长)?(?:为|是|仅)?|时长(?:为|是|仅)?|仅(?:有)?|只有|需在|要在)"
                    r"\s*(\d+(?:\.\d+)?\s*秒)",
                ]
                for pattern in patterns:
                    for claim in re.findall(pattern, finding.message):
                        if (claim not in absent and self._evidence_quote(
                                field_value(primary), claim) is None):
                            absent.append(claim)
            related_shots = {fields[ref]["shot_id"] for ref in refs} - {None}
            if (finding.code in {"continuity", "transition_conflict"}
                    and finding.shot_id in shots and len(related_shots) < 2):
                neighbor = positions[finding.shot_id] + (1 if finding.field == "transition" else -1)
                if 0 <= neighbor < len(draft.shots):
                    related_shots.add(draft.shots[neighbor].shot_id)
            images = {_key(shots[shot_id].image) for shot_id in related_shots}
            bound_sources = {ref for ref, source in source_fields.items()
                             if _key(source["image"]) in images}
            cross_image = (finding.code in {"continuity", "transition_conflict", "other"}
                           and finding.field in {"action", "transition"}
                           and len(images) > 1 and bool(bound_sources))
            if absent or cross_image:
                # 当前字段正文已在 draft 中，主字段编号另列于 audit_requirements。
                suspects[index] = finding.model_dump(mode="json", exclude={"evidence"})
                references[index], quotes[index] = refs, absent
                if cross_image:
                    temporal[index] = {ref for ref, coordinate in fields.items()
                        if coordinate["shot_id"] in related_shots and coordinate["field"] in {
                            "action", "camera", "transition", "duration_seconds"}}
                    source_refs[index] = bound_sources
                    source_images[index] = images
        if not suspects:
            return
        requirements = {}
        for index in suspects:
            requirements[index] = {
                "primary_field_id": next(ref for ref, coordinate in fields.items()
                    if coordinate == {"shot_id": output.findings[index - 1].shot_id,
                                      "field": output.findings[index - 1].field}),
                "absent_current_quotes": quotes[index],
                "current_field_ids": sorted(temporal.get(index, references[index])),
                "source_fields_by_image": [{"image": source_context["image"], "field_ids": [
                    ref for ref in sorted(source_refs.get(index, set()))
                    if _key(source_fields[ref]["image"]) == _key(source_context["image"])]}
                    for source_context in source_contexts
                    if _key(source_context["image"]) in source_images.get(index, set())],
            }
        bound_sources = {ref: source_fields[ref] for ref in
                         sorted(set().union(*source_refs.values()))}
        source_lookup = {(_key(item["image"]), item["quote"]): ref
                         for ref, item in bound_sources.items()}
        context = self._story_context(draft, source_contexts)
        for source in context["image_sources"]:
            image_key = _key(source["image"])
            for section in ("constraints", "authorized_requirements"):
                if section not in source:
                    continue
                compact_items = []
                for raw_item in source[section]:
                    if not isinstance(raw_item, dict):
                        compact_items.append(raw_item)
                        continue
                    item = dict(raw_item)
                    for field in ("effective_requirement", "requirement", "source_quote"):
                        value = item.get(field)
                        reference = (source_lookup.get((image_key, value))
                                     if isinstance(value, str) else None)
                        if reference:
                            item.pop(field)
                            item[field + "_ref"] = reference
                    compact_items.append(item)
                source[section] = compact_items
            for section in ("user_requirements", "asset_requirements"):
                if section in source:
                    source[section] = [
                        {"source_field_ref": source_lookup[(image_key, item)]}
                        if isinstance(item, str) and (image_key, item) in source_lookup else item
                        for item in source[section]]
        payload = {"stage": "story_finding_audit", "draft": draft.model_dump(mode="json"),
                   "review_fields": fields, "findings": suspects,
                   "source_fields": bound_sources,
                   "continuity_fields": {index: sorted(refs) for index, refs in temporal.items()},
                   "audit_requirements": requirements,
                   **context}
        # 传输失败或结果未知直接停止，不借契约纠正重发付费请求。
        raw = model_call([
            {"role": "system", "content":
             "核对这些审核发现是否把旧文字错误地声称为当前字段原文。当前draft是唯一现稿。"
             "逐条返回checks：默认retain。只有整条发现仅建立在已不存在的当前原文上，"
             "并能从对应当前字段引用反证时，才可stale_current_quote。"
             "用户要求、图片来源、建议改写和语义概括不必逐字存在于现稿，不能据此撤销真实冲突；"
             "同时含真实问题或无法确定时保留。不得仅因希望故事通过而撤销。"
             "撤销须给field_id、原message内claimed_quote及现字段current_quote。只返回JSON："
             "absent_current_quotes可能包含审核对当前镜头时长的错误数字；"
             "建议的目标秒数不是当前时长，其他独立问题存在或无法确认时须保留。"
             "跨图衔接还须按现稿完整时间顺序核对action和transition，不能漏看已写明的取物/换持。"
             "只有整条衔接问题已由现稿实际动作完整满足且符合固定来源时，才可already_satisfied；"
             "用supporting_quotes逐字引用当前主字段、相关transition及每个相关图片的source_fields依据，"
             "也可以把supporting_quotes的值写成与键完全相同的当前字段或来源编号，"
             "由系统提取该编号绑定的完整原文；不能使用旧编号或无关编号。"
             "至少两个不同当前字段，不能凭空宣称通过。真实冲突、硬要求冲突、不确定均retain。"
             "若冲突真实但原suggestion会破坏固定图片或重复无效修改，retain并给corrected_suggestion，"
             "同样须用上述当前字段和来源原文证明应如何衔接，不能只改掉建议而放行。"
             "非continuity_fields所列发现只允许原旧引文核对。不得改写剧情或私有推理。"
             "audit_requirements按发现给出主字段、允许的现稿字段及每张图的合法来源编号。"
             "image_sources中以_ref或source_field_ref结尾的来源内容，原文在source_fields对应编号中；"
             "它与原字段等价，仍须结合保留的来源范围和用户要求判断。"
             "衔接反证须引用主字段、至少一个transition和所列每张图的来源；编号不能跨发现借用。"
             "单条无法核实时retain，不能省略它或为补齐证据编造引文。"
             + STORY_SEMANTICS
             + json.dumps(ComicStoryFindingAudits.model_json_schema(), ensure_ascii=False)},
            {"role": "user", "content": json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"))},
        ])
        removed, amendments = set(), {}
        try:
            audit = _parse(raw, ComicStoryFindingAudits)
        except ToolError as error:
            public_error(error, component="comic.story.finding_audit", run_id=current_run_id())
            return  # 结构不合法时不能使用任何反证。
        accepted, rejected = [], []
        for index in suspects:
            checks = [check for check in audit.checks if check.finding == index]
            try:
                if len(checks) != 1:
                    raise ToolError("故事审核反证缺少对应发现或重复")
                check = checks[0]
                if check.verdict == "retain" and not check.corrected_suggestion:
                    accepted.append(check.model_dump(mode="json"))
                    continue
                if check.verdict == "stale_current_quote":
                    if (check.field_id not in references[check.finding]
                        or check.claimed_quote not in quotes[check.finding]
                        or not self._evidence_quote(
                            field_value(check.field_id), check.current_quote)):
                        raise ToolError("故事审核反证与当前字段不一致")
                    removed.add(check.finding)
                    accepted.append(check.model_dump(mode="json"))
                    continue
                allowed = temporal.get(check.finding, set())
                sources = source_refs.get(check.finding, set())
                supporting = dict(check.supporting_quotes)
                for ref, text in supporting.items():
                    if text == ref and ref in (allowed | sources):
                        value = source_fields[ref]["quote"] if ref in sources else field_value(ref)
                        supporting[ref] = str(value)[:4000] if value is not None else ""
                supplied = set(supporting)
                current = supplied & allowed
                finding = output.findings[check.finding - 1]
                primary = next((ref for ref in allowed if fields[ref] == {
                    "shot_id": finding.shot_id, "field": finding.field}), None)
                required_images = source_images.get(check.finding, set())
                quoted_images = {_key(source_fields[ref]["image"]) for ref in supplied & sources}
                if (not allowed or primary not in current or len(current) < 2
                        or not any(fields[ref]["field"] == "transition" for ref in current)
                        or not required_images or quoted_images != required_images
                        or supplied - (allowed | sources)
                        or any(not self._evidence_quote(
                            source_fields[ref]["quote"] if ref in sources else field_value(ref),
                            text)
                            for ref, text in supporting.items())):
                    raise ToolError("故事衔接反证缺少当前动作、转场或固定图片来源依据")
                check.supporting_quotes = supporting
                if check.verdict == "already_satisfied":
                    removed.add(check.finding)
                else:
                    amendments[check.finding] = check.corrected_suggestion
                accepted.append(check.model_dump(mode="json"))
            except ToolError as error:
                # 每条反证独立核验；失败只保留该条原问题，不丢掉其他已严格核验的结果。
                rejected.append(index)
                public_error(error, component="comic.story.finding_audit", finding=index,
                             run_id=current_run_id())
        if run_id := current_run_id():
            run = self.runtime.get_run(run_id)
            receipt = {"draft_sha256": value_hash(draft.model_dump(mode="json")),
                       "findings": suspects, "checks": accepted, "retained_invalid": rejected}
            self.runtime.update_run(run_id, status=run.status,
                state={**run.state, "story_review_audits": [
                    *run.state.get("story_review_audits", []), receipt][-4:]},
                current_node=run.current_node, error=run.error)
        for index, suggestion in amendments.items():
            output.findings[index - 1].suggestion = suggestion
        output.findings = [finding for index, finding in enumerate(output.findings, 1)
                           if index not in removed]

    def _review_output(self, draft, images, model_call, source_contexts, *, user_intent=None):
        fields = self._review_fields(draft)
        schema = ComicStoryReviewOutput.model_json_schema()
        finding_schema = schema["$defs"]["ComicStoryFinding"]
        finding_schema["properties"]["evidence_id"] = {"type": "string", "enum": list(fields)}
        finding_schema["required"].append("evidence_id")
        finding_schema["properties"]["related_fields"]["items"] = {
            "type": "string", "enum": list(fields)}
        messages = [
            {
                "role": "system",
                "content": "独立核验公开故事中的动作、运镜、时长、衔接与用户需求的一致性。"
                "图片身份仅为固定来源，不证明不同图片中的人物是同一角色；没有视觉证据时不要假装看过图片。"
                "image_sources是各图片起始画面的来源依据，仅适用于使用对应图片的镜头。"
                "shot_source_map按shot_id给出对应图片及image_sources的零起始编号；null代表无来源文字。"
                "scoped_replacement以effective_requirement为准，不能恢复被替代的旧目标；"
                "保留其他对象和未变属性，不把授权范围扩为整张图。"
                + STORY_SEMANTICS +
                "只返回给定Schema的公开JSON；finding从review_fields选择当前字段的evidence_id，"
                "shot_id及field与所选条目一致，finding.evidence留空，由系统从该字段提取原文。"
                "引用编号包含当前值指纹，修改后的稿件必须使用本次编号，不能沿用修复前的编号。"
                "field必须是引文所属的当前字段，不得引用用户要求、其他字段或修复前的旧稿来充当该字段原文。"
                "时长、图片和文字都用对应字段编号，不需复述数值、图片对象或长文本。"
                "并给出code（missing_title/missing_summary/missing_shots/missing_action/"
                "missing_camera/missing_transition/missing_duration/"
                "duration_conflict/camera_conflict/transition_conflict/image_conflict/"
                "requirement_conflict/continuity/other）与suggestion（一句可执行建议：改哪个字段、怎么改）。"
                "存在真实冲突为error/needs_revision，艺术建议为warning，未发现问题为pass/findings=[]。"
                "跨镜头衔接/物件一致性问题必须用related_fields列出需协同修改的其他当前字段编号，"
                "不要只给一镜修改建议却要求另一镜保持矛盾内容；无关联字段时填空列表。"
                "先检查全部镜头，再按shot_source_map的前后关系逐一检查边界，一次列全具体问题；"
                "不能只检查第一处武器差异，等修复后才开始检查其余镜头的运镜或时长。"
                "时长不足须说明完整动作节拍的时间需求，并提出一次可完成的总时长或精简方案，"
                "不能仅以动作多就反复建议再加一秒。"
                "code=duration_conflict且severity=error必须给timing_plan：按实际顺序引用本镜当前action和transition"
                "中的具体原文及各节拍seconds，二者均须覆盖；同一动作不重复计时，并行动作按较长者"
                "安排。运镜、环境和静态状态通常与动作同时发生，不应另作串行节拍。"
                "error的总需求秒数必须确实超过当前duration_seconds；warning可不提供预算，"
                "但它仍是必须处理的优化建议。其他问题timing_plan为空。"
                "注意finding.evidence与timing_plan[].evidence是不同字段：只有finding.evidence留空；"
                "每个timing_plan[].evidence必须从该镜头相应action或transition逐字摘录，不得留空、"
                "概括或引用其他镜头。即使transition只是切镜，也引用它并给seconds=0，不可漏掉该阶段。"
                "message只写一句简短问题，suggestion只写一句可执行修改，避免复述整段故事或长篇论证；"
                "finding.evidence保持空字符串，依据由系统提取；不要将依据长文重复到message或suggestion。"
                "不重新生成、不自动修复、不输出私有推理。Schema："
                + json.dumps(schema, ensure_ascii=False),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "draft": draft.model_dump(mode="json"),
                        "review_fields": fields,
                        **self._story_context(draft, source_contexts, images=images),
                        "user_intent": user_intent.model_dump(mode="json") if user_intent else None,
                    },
                    ensure_ascii=False, separators=(",", ":"),
                ),
            },
        ]
        verified, issues = {}, []
        for attempt in range(2):
            inputs = messages
            if attempt:
                inputs = [*messages, {"role": "user", "content": (
                    "上次公开审核结构或引文无效，只纠正一次。依据当前原稿重新返回完整公开审核JSON，"
                    "不得重写故事或输出私有推理。保留下列已核验发现；其余发现从当前review_fields"
                    "选择evidence_id，finding.evidence留空；timing_plan[].evidence仍必须逐字引用"
                    "本镜当前action/transition，覆盖required_phases，修正quote_mismatch_beats；"
                    "有expected_field_reference时使用该字段编号。"
                    "error预算总数须大于minimum_error_budget_exclusive，不能重复计算并行动作。"
                    + json.dumps({"issues": issues, "verified_findings": [
                        item.model_dump(mode="json") for item in verified.values()]},
                        ensure_ascii=False))}]
            # 仅成功返回后的契约错误可纠正；传输失败或结果未知直接退出，不重发模型请求。
            raw = model_call(inputs)
            output, structure_issues = self._review_response(raw)
            if output is not None:
                checked, issues = self._review_findings(draft, output)
                issues = [*structure_issues, *issues]
                for finding in checked:
                    key = (finding.shot_id, finding.field, finding.code,
                           finding.evidence, finding.severity)
                    verified.setdefault(key, finding)
                if not issues and len(verified) <= 100:
                    output.findings = list(verified.values())
                    self._audit_finding_quotes(draft, output, model_call, source_contexts)
                    if any(item.severity == "error" for item in output.findings):
                        output.status = "needs_revision"
                    elif output.findings:
                        output.status = "warning"
                    else:
                        output.status = "pass"
                    return output.model_dump(mode="json")
                if not issues:
                    issues = [{"reason": "finding_capacity",
                               "message": "故事审核发现过多，请缩小校验范围"}]
            else:
                issues = structure_issues
            error = ToolError(issues[0]["message"], detail="module=comic.story.review "
                f"reason={issues[0]['reason']} attempt={attempt + 1} "
                f"diagnostics={json.dumps(issues[0], ensure_ascii=False)}")
            public_error(error, component="comic.story.review_contract", stage="review",
                         run_id=current_run_id())
            if attempt:
                raise error

    def _insert_review(self, connection, story, output, *, model, run_id,
                       validation_requested=False):
        identity = f"{story['story_id']}:{story['version']}"
        version, _ = self.media._latest(
            connection, story["anchor_project_id"], "story_review", identity
        )
        payload = {
            **output,
            "content_sha256": story["content_sha256"],
            "story_version": story["version"],
            "model": model,
            "run_id": run_id,
            "created_at": utc_now().isoformat(),
            "contract": STORY_CONTRACT,
            "validation_requested": validation_requested,
        }
        self.projects._insert_version(
            connection,
            project_id=story["anchor_project_id"],
            entity_type="story_review",
            entity_id=identity,
            version=version + 1,
            payload_json=json.dumps(payload, ensure_ascii=False),
            created_at=utc_now(),
        )
        return payload

    @staticmethod
    def _merge_user_draft(draft, skeleton):
        """固定用户镜头身份及图片，采纳协调后的制作字段，缺值才沿用原框架。"""
        if skeleton is None:
            return draft
        merged = draft.model_copy(deep=True)
        if skeleton.title.strip():
            merged.title = skeleton.title
        if skeleton.summary.strip():
            merged.summary = skeleton.summary
        if not skeleton.shots:
            return merged
        pool = list(draft.shots)
        shots = []
        user_ids = {shot.shot_id for shot in skeleton.shots}
        image_counts = {}
        for shot in skeleton.shots:
            image_counts[_key(shot.image)] = image_counts.get(_key(shot.image), 0) + 1
        unmatched_images = set()
        for user in skeleton.shots:
            index = next((position for position, item in enumerate(pool)
                          if item.shot_id == user.shot_id
                          and _key(item.image) == _key(user.image)), None)
            if index is None and image_counts[_key(user.image)] == 1:
                # 兼容模型未沿用 ID 的唯一同图结果，不能借用另一用户镜头或其他图片的内容。
                matches = [position for position, item in enumerate(pool)
                           if _key(item.image) == _key(user.image)
                           and item.shot_id not in user_ids]
                index = matches[0] if len(matches) == 1 else None
            model_shot = pool.pop(index) if index is not None else None
            if model_shot is None:
                unmatched_images.add(_key(user.image))
            shots.append(ComicStoryShot(
                shot_id=(user.shot_id or (model_shot.shot_id if model_shot
                                          else f"shot-{len(shots) + 1}")),
                image=user.image,
                action=(model_shot.action if model_shot and model_shot.action.strip()
                        else user.action),
                camera=(model_shot.camera if model_shot and model_shot.camera.strip()
                        else user.camera),
                duration_seconds=(model_shot.duration_seconds if model_shot
                                  and model_shot.duration_seconds is not None
                                  else user.duration_seconds),
                transition=(model_shot.transition if model_shot and model_shot.transition.strip()
                            else user.transition),
            ))
        used = {shot.shot_id for shot in shots}
        for extra in pool:
            if extra.shot_id in user_ids or _key(extra.image) in unmatched_images:
                continue
            if extra.shot_id in used:
                extra = extra.model_copy(update={"shot_id": f"shot-{uuid4().hex}"})
            used.add(extra.shot_id)
            shots.append(extra)
        merged.shots = shots
        return merged

    def _vision_payload(self, images, payload):
        content = [{"type": "text", "text": json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"))}]
        mapping = payload.get("shot_source_map", {})
        sequence = list(mapping)
        def shot_numbers(image):
            return [index for index, shot_id in enumerate(sequence, 1)
                    if _key(mapping[shot_id]["image"]) == _key(image)]
        for image in sorted(images, key=lambda item: min(shot_numbers(item), default=101)):
            artifact = self.runtime.get_artifact(image["artifact_id"])
            data = validate_runtime_path(Path(artifact.location)).read_bytes()
            if hashlib.sha256(data).hexdigest() != image["sha256"]:
                raise ToolError("图片内容在故事生成前发生变化")
            content.append({"type": "text",
                            "text": f"图片来源 {image['image_id']} v{image['version']}；"
                                    f"对应镜头序号 {shot_numbers(image)}"})
            content.append({"type": "image_url", "image_url": {
                "url": f"data:{image['mime_type']};base64,"
                + base64.b64encode(data).decode("ascii")}})
        return content

    @staticmethod
    def _repair_scope(draft, findings, *, include_warnings=False):
        """以错误定位镜头；执行字段协同修正，真实交界冲突可联动下一镜。"""
        fields = {"action", "camera", "duration_seconds", "transition"}
        scope = {"shots": {}, "story": [], "create_shots": False}
        positions = {shot.shot_id: index for index, shot in enumerate(draft.shots)}
        references = ComicStoryService._review_fields(draft)
        for item in findings:
            if item.get("severity") != "error" and not (
                include_warnings and item.get("severity") == "warning"
            ):
                continue
            shot_id, field = item.get("shot_id"), item["field"]
            if shot_id in positions and field in fields:
                scope["shots"][shot_id] = sorted(fields)
                if field == "action" and item["code"] in {"continuity", "transition_conflict"}:
                    preceding = positions[shot_id] - 1
                    linked = {references[ref]["shot_id"] for ref in item.get("related_fields", [])
                              if ref in references and references[ref]["shot_id"]}
                    if preceding >= 0 and (not linked or draft.shots[preceding].shot_id in linked):
                        scope["shots"][draft.shots[preceding].shot_id] = sorted(fields)
                if field == "transition" and item["code"] in {"continuity", "transition_conflict"}:
                    following = positions[shot_id] + 1
                    if following < len(draft.shots):
                        scope["shots"][draft.shots[following].shot_id] = sorted(fields)
                if field == "action" and item["code"] in {"image_conflict", "other"}:
                    preceding = positions[shot_id] - 1
                    if preceding >= 0:
                        previous_id = draft.shots[preceding].shot_id
                        linked_images = {references[ref]["shot_id"] for ref in
                            item.get("related_fields", []) if ref in references
                            and references[ref]["field"] == "image"}
                        if previous_id in linked_images:
                            authorized = set(scope["shots"].get(previous_id, []))
                            scope["shots"][previous_id] = sorted(
                                authorized | {"transition", "duration_seconds"})
            elif not shot_id and field in {"title", "summary"}:
                scope["story"].append(field)
            elif not draft.shots and item["code"] == "missing_shots":
                scope["create_shots"] = True
            if item.get("code") in {
                "continuity", "transition_conflict", "requirement_conflict", "image_conflict",
                "other",
            }:
                for reference in item.get("related_fields", []):
                    related = references.get(reference, {})
                    if related.get("shot_id") in positions and related.get("field") in fields:
                        scope["shots"][related["shot_id"]] = sorted(fields)
        return scope

    def _minimum_durations(self, draft, findings):
        """把当前稿已核验的完整节拍预算变成修稿下界，防止写稿模型再次缩短。"""
        result = {}
        for item in findings:
            if item.get("code") != "duration_conflict" or not item.get("timing_plan"):
                continue
            try:
                output = ComicStoryReviewOutput.model_validate({
                    "status": "needs_revision", "findings": [item]})
            except ValidationError:
                continue
            checked, issues = self._review_findings(draft, output)
            if issues or not checked or not checked[0].shot_id:
                continue
            budget = round(sum(beat.seconds for beat in checked[0].timing_plan), 6)
            # 超出单镜合法容量必须继续精简或重新安排，不能制造一个越界完成稿。
            if 0 < budget <= 60:
                shot_id = checked[0].shot_id
                result[shot_id] = max(result.get(shot_id, 0), budget)
        return result

    def _repair(self, draft, findings, images, source_contexts, vision_model_call, instruction,
                *, user_intent=None, repair_strategy="targeted", repair_history=(),
                include_warnings=False):
        """在出错镜头内协调制作字段，输出仍由独立审核决定是否可交付。"""
        if not findings:
            return None
        payload = {
            "instruction": instruction,
            "current_draft": draft.model_dump(mode="json"),
            "user_intent": user_intent.model_dump(mode="json") if user_intent else None,
            # 字段指纹/关联已投影为 repair_scope 和边界，不在问题中再次展开。
            "issues": [{key: value for key, value in item.items()
                        if key not in {"evidence", "evidence_id", "related_fields"}}
                       for item in findings],
            "repair_scope": self._repair_scope(draft, findings, include_warnings=include_warnings),
            "minimum_durations": self._minimum_durations(draft, findings),
            "repair_strategy": repair_strategy,
            # 当前问题含完整执行预算；历史只给位置/类别，旧预算与旧建议不再重复进入现稿请求。
            "repair_history": [{"draft_sha256": value_hash(attempt.get("draft")),
                "review": {"status": attempt.get("review", {}).get("status"), "findings": [
                    {key: value for key, value in item.items()
                     if key in {"shot_id", "field", "code", "severity"}}
                    for item in attempt.get("review", {}).get("findings", [])]}}
                for attempt in list(repair_history)[-3:]],
            **self._story_context(draft, source_contexts, images=images),
        }
        messages = [
            {"role": "system", "content": "按 issues 及 repair_scope 修正 current_draft。"
             "错误镜头的动作、运镜、时长与转场应一起协调；不能只改一个字段而留下相互矛盾的运动。"
             "已有用户填写内容也应纠正错误的执行方式，但必须保留原本合法剧情和对白。"
             "repair_history仅为已尝试的旧问题索引，不能当作current_draft事实；避免撤销有效修正。"
             "AI整理须处理repair_scope授权内的错误和可执行优化建议，不能只回一份修改建议。"
             "reconcile时先统筹所有出错镜头的起始状态、动作结束状态和下一镜起始状态再修正。"
             "各图来源的起始人物、兵器、姿态必须保留；不同镜头的刀剑不同时，不能把一图的"
             "兵器直接改成另一图的兵器，应在允许的动作或转场内交代收剑、换刀等实际变化。"
             "所需动作须能在时长内完成，不能靠含糊措辞假装冲突已解决。"
             "逐个核对修正镜头的前后边界，把取放位置、换持步骤写成实际动作；"
             "不能仅写已换持或武器换为另一件而省略过程，也不能重复执行已握持物品的取出动作。"
             "issues中的建议仍须服从固定来源与现稿事实，不可盲目照改；"
             "先安排全部动作节拍再确定足够的时长，不要按每轮增加一秒试探审核。"
             "issues的timing_plan是本轮审核对现稿动作和转场的逐段预算；"
             "据此一次协调完整时长，若精简动作也必须保留合法剧情并重新核算，不机械累加秒数。"
             "minimum_durations是现稿动作和转场未变时已核验的保守下界；"
             "若改写动作或转场，须根据改稿重新安排完整节拍，不能机械沿用旧预算。"
             "改稿仍须独立复核，不能把增加时长当作其他衔接和来源问题已经解决。"
             "只修改repair_scope授权的字段，保留其余文字、shot_id、镜头顺序与图片；不新增无关内容。"
             "仅create_shots=true时可从所给images补建完整镜头。"
             + STORY_SEMANTICS +
             "每个镜头必须写清动作、运镜、时长与转场。只返回完整 ComicStoryDraft JSON。Schema："
             + json.dumps(ComicStoryDraft.model_json_schema(), ensure_ascii=False)},
            {"role": "user", "content": self._vision_payload(images, payload)},
        ]
        raw = vision_model_call(messages)
        try:
            return _parse(raw, ComicStoryDraft)
        except ToolError as error:
            public_error(error, component="comic.story.repair", stage="repair",
                         run_id=current_run_id())
            return None

    def _repair_once(self, draft, findings, images, source_contexts, vision_model_call,
                     instruction, allowed, *, user_intent=None, repair_strategy="targeted",
                     repair_history=(), include_warnings=False):
        """一次有界修复：修完仍缺字段或引用未选图片就退回原稿，不连环重试。"""
        repaired = self._repair(draft, findings, images, source_contexts,
                                vision_model_call, instruction, user_intent=user_intent,
                                repair_strategy=repair_strategy, repair_history=repair_history,
                                include_warnings=include_warnings)
        if repaired is None:
            return draft
        # 模型返回完整 JSON，只采纳授权范围，图片、镜头身份与顺序不接受模型改写。
        restricted = draft.model_copy(deep=True)
        repaired_shots = {shot.shot_id: shot for shot in repaired.shots}
        scope = self._repair_scope(draft, findings, include_warnings=include_warnings)
        if scope["create_shots"]:
            restricted.shots = repaired.shots
        for field in scope["story"]:
            setattr(restricted, field, getattr(repaired, field))
        for shot_id, fields in scope["shots"].items():
            replacement = repaired_shots.get(shot_id)
            original = next((shot for shot in restricted.shots if shot.shot_id == shot_id), None)
            if original and replacement and _key(original.image) == _key(replacement.image):
                for field in fields:
                    setattr(original, field, getattr(replacement, field))
        minimum_durations = self._minimum_durations(draft, findings)
        original_shots = {shot.shot_id: shot for shot in draft.shots}
        for shot in restricted.shots:
            minimum = minimum_durations.get(shot.shot_id)
            original = original_shots.get(shot.shot_id)
            if (minimum is not None and original and shot.action == original.action
                    and shot.transition == original.transition
                    and "duration_seconds" in scope["shots"].get(shot.shot_id, [])):
                shot.duration_seconds = max(shot.duration_seconds or 0, minimum)
        repaired = restricted
        repaired.instruction = instruction
        if any(_key(shot.image) not in allowed for shot in repaired.shots):
            return draft
        return draft if repaired == draft else repaired

    def generate(self, request, *, vision_model_call, model_call, model, vision_model, run_id):
        if saved := self.media.request_result(request.anchor_project_id, "story.generate", request):
            return saved
        continuation = None
        skeleton = request.draft
        history = []
        if request.candidate_run_id:
            original, continuation = self._load_candidate(request)
            source_request = continuation["request"]
            if (original.status.value != "waiting" or not continuation.get("review")
                    or original.state.get("provider_submission_unknown")
                    or source_request["expected_story_version"] != request.expected_story_version
                    or source_request["instruction"] != request.instruction
                    or {_key(ref) for ref in request.images}
                    != {_key(ref) for ref in continuation["fixed_images"]}):
                raise ToolError("候选与当前故事或整理要求不一致，请基于当前故事重新整理")
            skeleton = (ComicStoryDraft.model_validate(source_request["draft"])
                        if source_request.get("draft") else None)
            history = original.state.get("story_attempts", [])[-STORY_REPAIR_LIMIT:]
        with self.projects._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            anchor, previous = self._prepare(connection, request)
            probe = ComicStoryDraft(
                shots=[
                    {"shot_id": f"source-{index}", "image": ref.model_dump()}
                    for index, ref in enumerate(request.images)
                ]
            )
            images = self._images(probe, anchor, request.conversation_id,
                                  previous=previous.get("fixed_images"), connection=connection)
        source_contexts = self._source_contexts(images)
        allowed = {_key(image) for image in images}
        if skeleton and any(_key(shot.image) not in allowed for shot in skeleton.shots):
            raise ToolError("已填写镜头的图片必须包含在本次整理范围内，请保留这些图片后再整理")

        def frozen_for(draft):
            frozen = [image for image in images
                      if _key(image) in {_key(shot.image) for shot in draft.shots}]
            return frozen, [item for item in source_contexts
                            if _key(item["image"]) in {_key(image) for image in frozen}]

        def retain(draft, frozen, frozen_sources, output=None):
            candidate = {
                "conversation_id": request.conversation_id,
                "anchor_project_id": anchor,
                "request": request.model_dump(mode="json"),
                "draft": draft.model_dump(mode="json"),
                "fixed_images": frozen,
                "source_contexts": frozen_sources,
                "content_sha256": self._content_hash(draft, frozen, frozen_sources),
                "vision_model": vision_model,
                "created_at": utc_now().isoformat(),
            }
            if continuation:
                candidate["source_candidate_run_id"] = request.candidate_run_id
                # 延续原始用户意图，不能把带错候选升格为用户要求。
                candidate["request"] = {**candidate["request"],
                                        "draft": continuation["request"].get("draft")}
            if output is not None:
                candidate.update(review=output, review_model=model, review_contract=STORY_CONTRACT)
            self._candidate(run_id, candidate)

        def record_attempt(draft, output):
            run = self.runtime.get_run(run_id)
            attempts = [*run.state.get("story_attempts", []), {
                "draft": draft.model_dump(mode="json"), "review": output,
                "review_contract": STORY_CONTRACT,
            }]
            self.runtime.update_run(run_id, status=run.status,
                state={**run.state, "story_attempts": attempts[-(STORY_REPAIR_LIMIT + 1):]},
                current_node=run.current_node, error=run.error)

        def review_candidate(draft, frozen, frozen_sources):
            try:
                return self._review_output(draft, frozen, model_call, frozen_sources,
                                           user_intent=skeleton)
            except ToolError as error:
                raise ToolError("初稿审核未完成，候选已保留，可恢复后重新校验。"
                                + error.message, detail=error.detail) from error

        def action():
            payload = {
                "instruction": request.instruction,
                "current_draft": skeleton.model_dump(mode="json") if skeleton else None,
                "user_intent": skeleton.model_dump(mode="json") if skeleton else None,
                **self._story_context(skeleton, source_contexts, images=images),
            }
            messages = [
                {
                    "role": "system",
                    "content": "依据用户明确选中的图片和故事需求编排可编辑视频制作故事。"
                    "current_draft 是待整理的标题、概述与镜头框架：保留有效剧情与对白并补全空缺，"
                    "主动协调动作、运镜、时长与转场，纠正错误执行方式，不能照抄有矛盾的原稿。"
                    "框架与instruction共同表达用户意图；仅两者均无内容时才由你自行构思。"
                    "可以单图成段或多图连续，每镜头只能引用输入的image_id及version，"
                    "镜数不超过100；已有镜头须沿用shot_id及对应图片，不得遗漏或重排。"
                    "不把不同人像强行当同一角色，不虚构可见图片身份；动作与运镜不能冲突。"
                    "summary应概括全片的因果、发展与收尾；逐镜交代动作结束状态如何接入下一镜，"
                    "主动协调节奏与时长，不把明显可改的执行问题留给用户当优化建议。"
                    "image_sources只约束对应图片，不将某张图的规则套在其他图；"
                    "scoped_replacement以有效新要求为准，不恢复旧目标，保留其他对象和未变属性。"
                    + STORY_SEMANTICS +
                    "只返回公开ComicStoryDraft JSON，完整填写动作、运镜、合理时长及衔接，"
                    "instruction保持用户原文；shot_id独立且不重复，不输出私有推理。Schema："
                    + json.dumps(ComicStoryDraft.model_json_schema(), ensure_ascii=False),
                },
                {"role": "user", "content": (
                    self._vision_payload(images, payload) if not continuation else [])},
            ]
            draft = (ComicStoryDraft.model_validate(continuation["draft"]) if continuation
                     else self._merge_user_draft(
                         _parse(vision_model_call(messages), ComicStoryDraft), skeleton))
            draft.instruction = request.instruction
            if any(_key(shot.image) not in allowed for shot in draft.shots):
                raise ToolError("故事模型引用了未明确选中的图片版本")
            frozen, frozen_sources = frozen_for(draft)
            if continuation and continuation["content_sha256"] != self._content_hash(
                draft, frozen, frozen_sources,
            ):
                raise ToolError("候选图片或来源依据已变化，请基于当前故事重新整理")
            retain(draft, frozen, frozen_sources)
            repairs_used, reconcile_next = 0, False
            pending_review = (continuation["review"] if continuation
                              and continuation.get("review_contract") == STORY_CONTRACT else None)
            while True:
                structural = self._structural_findings(draft)
                # 同契约的原候选已有真实错误，先修再复核，不能重按按钮重抽一次通过。
                output = (pending_review if pending_review is not None
                          else self._structural_review(structural) if structural
                          else review_candidate(draft, frozen, frozen_sources))
                pending_review = None
                record_attempt(draft, output)
                retain(draft, frozen, frozen_sources, output)
                history.append({"draft": draft.model_dump(mode="json"), "review": output,
                                "review_contract": STORY_CONTRACT})
                if output["status"] == "pass" and not output["findings"]:
                    break
                while repairs_used < STORY_REPAIR_LIMIT:
                    strategy = ("reconcile" if reconcile_next or repairs_used >= 2 else "targeted")
                    repairs_used += 1
                    fixed = self._repair_once(draft, output["findings"], images, source_contexts,
                        vision_model_call, request.instruction, allowed, user_intent=skeleton,
                        repair_strategy=strategy, repair_history=history[-3:],
                        include_warnings=True)
                    if any(item.get("review_contract") == STORY_CONTRACT
                           and item["draft"] == fixed.model_dump(mode="json") for item in history):
                        fixed = draft  # 已审核的旧稿不能靠重复抽取结论获得通过。
                    if fixed is not draft:
                        break
                    # 无变化不重新抽审核结论；只允许换统筹策略再修一次。
                    if strategy == "reconcile":
                        break
                    reconcile_next = True
                else:
                    break
                if fixed is draft:
                    break
                draft = fixed
                frozen, frozen_sources = frozen_for(draft)
            if output["status"] != "pass" or output["findings"]:
                with self.projects._connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    self._prepare(connection, request)
                    return self._finish(connection, request, "story.generate", run_id=run_id,
                        generation_status="needs_revision", candidate_run_id=run_id,
                        candidate_draft=draft.model_dump(mode="json"), candidate_review=output)
            self._complete(draft)
            with self.projects._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._prepare(connection, request)
                self._images(
                    draft, anchor, request.conversation_id, previous=frozen, connection=connection
                )
                story = self._insert_story(
                    connection, request, draft, frozen, "model_generated", run_id, frozen_sources
                )
                self._insert_review(connection, story, output, model=model, run_id=run_id)
                return self._finish(connection, request, "story.generate", run_id=run_id,
                                    generation_status="ready",
                                    candidate_run_id=request.candidate_run_id)

        return self._execute(request, "story.generate", run_id, action)

    def review(self, request, *, model_call, model, run_id):
        if saved := self.media.request_result(request.anchor_project_id, "story.review", request):
            return saved
        with self.projects._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            anchor, story = self._prepare(connection, request)
            if not story:
                raise ToolError("请先保存故事再核验")
            draft = self._draft(story)
            images = self._images(
                draft,
                anchor,
                request.conversation_id,
                previous=story["fixed_images"],
                connection=connection,
            )

        def action():
            # 先做确定性检查：缺字段这类问题不必问模型，坐标与改法都更准；
            # 内容没变则沿用上次结论，避免同一份初稿两次校验结论不一致。
            with self.projects._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._prepare(connection, request)
                structural = self._structural_findings(draft)
                if structural:
                    self._insert_review(connection, story, self._structural_review(structural),
                                        model="local_structural", run_id=run_id,
                                        validation_requested=True)
                    return self._finish(connection, request, "story.review", run_id=run_id)
                if self._review_current(connection, story, model):
                    review = self.view(request.conversation_id, connection=connection)["review"]
                    if not review.get("validation_requested"):
                        self._insert_review(connection, story, review, model=model, run_id=run_id,
                                            validation_requested=True)
                    return self._finish(connection, request, "story.review", run_id=run_id,
                                        review_reused=True)
            output = self._review_output(draft, images, model_call, story["source_contexts"])
            with self.projects._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._prepare(connection, request)
                self._images(
                    draft, anchor, request.conversation_id, previous=images, connection=connection
                )
                self._insert_review(connection, story, output, model=model, run_id=run_id,
                                    validation_requested=True)
                return self._finish(connection, request, "story.review", run_id=run_id)

        return self._execute(request, "story.review", run_id, action)

    def confirm(self, request, *, run_id=None):
        with self.projects._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if saved := self.media.request_result(
                request.anchor_project_id, "story.confirm", request, connection=connection
            ):
                return saved
            anchor, story = self._prepare(connection, request)
            if not story:
                raise ToolError("请先保存故事再确认")
            draft = self._draft(story)
            # 先看校验结论：有未通过的问题时说清"先校验"，比只说某个字段没填更有用。
            view = self.view(request.conversation_id, connection=connection)
            review = view["review"]
            if not self._review_allows_confirmation(story, review):
                raise ToolError("当前故事尚未通过校验，请先校验或修改后重新校验")
            self._complete(draft)
            self._images(
                draft,
                anchor,
                request.conversation_id,
                previous=story["fixed_images"],
                connection=connection,
            )
            if review["status"] == "warning" and not request.accept_warnings:
                raise ToolError("当前故事有核验建议，请明确接受建议后再确认")
            identity = f"{story['story_id']}:{story['version']}"
            version, _ = self.media._latest(connection, anchor, "story_confirmation", identity)
            payload = {
                "story_version": story["version"],
                "content_sha256": story["content_sha256"],
                "confirmed": True,
                "accept_warnings": request.accept_warnings,
                "review_sha256": value_hash(review),
                "run_id": run_id,
                "created_at": utc_now().isoformat(),
            }
            self.projects._insert_version(
                connection,
                project_id=anchor,
                entity_type="story_confirmation",
                entity_id=identity,
                version=version + 1,
                payload_json=json.dumps(payload),
                created_at=utc_now(),
            )
            return self._finish(connection, request, "story.confirm", run_id=run_id)

    def restore(self, request, *, run_id=None):
        with self.projects._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if saved := self.media.request_result(
                request.anchor_project_id, "story.restore", request, connection=connection
            ):
                return saved
            anchor, _ = self._prepare(connection, request)
            historical = json.loads(
                self.projects._version_payload(
                    connection,
                    anchor,
                    "story",
                    self.story_id(request.conversation_id),
                    request.version,
                )
            )
            draft = self._draft(historical)
            images = self._images(
                draft,
                anchor,
                request.conversation_id,
                previous=historical["fixed_images"],
                connection=connection,
            )
            self._insert_story(
                connection,
                request,
                draft,
                images,
                "history_restore",
                run_id,
                historical["source_contexts"],
            )
            return self._finish(connection, request, "story.restore", run_id=run_id)

    def _load_candidate(self, request):
        original = self.runtime.get_run(request.candidate_run_id)
        candidate = original.state.get("story_candidate")
        if (
            original.domain != "comic"
            or original.workflow != "comic.story.generate"
            or not candidate
            or original.state.get("conversation_id") != request.conversation_id
            or candidate.get("conversation_id") != request.conversation_id
            or candidate.get("anchor_project_id") != request.anchor_project_id
        ):
            raise ToolError("生成候选不属于当前聊天故事")
        draft = ComicStoryDraft.model_validate(candidate["draft"])
        if candidate.get("content_sha256") != self._content_hash(
            draft, candidate["fixed_images"], candidate["source_contexts"]
        ):
            raise ToolError("生成候选来源指纹不一致")
        return original, candidate

    def adopt_candidate(self, request, *, run_id=None):
        original, candidate = self._load_candidate(request)
        draft = ComicStoryDraft.model_validate(candidate["draft"])
        review = candidate.get("review")
        if (candidate.get("review_contract") != STORY_CONTRACT or not review
                or review["status"] != "pass" or review["findings"]):
            raise ToolError("候选尚未通过当前检查，请继续 AI 整理后再使用")
        self._complete(draft)
        with self.projects._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if saved := self.media.request_result(
                request.anchor_project_id, "story.adopt-candidate", request, connection=connection
            ):
                return saved
            anchor, _ = self._prepare(connection, request)
            images = self._images(
                draft,
                anchor,
                request.conversation_id,
                previous=candidate["fixed_images"],
                connection=connection,
            )
            story = self._insert_story(
                connection,
                request,
                draft,
                images,
                "candidate_recovery",
                run_id,
                candidate["source_contexts"],
            )
            if candidate.get("review") and candidate.get("review_contract") == STORY_CONTRACT:
                self._insert_review(
                    connection,
                    story,
                    candidate["review"],
                    model=candidate["review_model"],
                    run_id=original.id,
                )
            return self._finish(
                connection,
                request,
                "story.adopt-candidate",
                run_id=run_id,
                candidate_run_id=original.id,
            )

    def export_bundle(self, request, *, run_id=None, output_dir=None):
        operation = "story.exports"
        if saved := self.media.request_result(request.anchor_project_id, operation, request):
            self._export_record(saved["export_artifact_id"])
            return saved
        with self.projects._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            anchor, story = self._prepare(connection, request)
            if not story:
                raise ToolError("请先完成故事再导出")
            current = self.view(request.conversation_id, connection=connection)
            self._complete(self._draft(story))
            if not current["confirmation"] or current["confirmation"][
                "review_sha256"
            ] != value_hash(current["review"]):
                raise ToolError("请确认当前故事与核验结果后再导出")
            if current["missing_images"]:
                raise ToolError(
                    "故事中有缺失或已改变的图片，素材包未导出",
                    detail="module=comic.story export=images",
                )
        stream, files = io.BytesIO(), {}
        for image in current["manifest"]["images"]:
            ref = ImageVersionRef(image_id=image["image_id"], version=image["version"])
            frozen = next(item for item in story["fixed_images"] if _key(item) == _key(ref))
            self._require_image(ref, anchor, request.conversation_id, allow_related=True,
                                snapshot=frozen)
            artifact = self.runtime.get_artifact(image["artifact_id"])
            content = validate_runtime_path(Path(artifact.location)).read_bytes()
            if hashlib.sha256(content).hexdigest() != image["sha256"]:
                raise ToolError("图片在导出过程中发生变化，已停止导出")
            files.setdefault(image["filename"], content)
        with ZipFile(stream, "w", compression=ZIP_STORED) as archive:
            archive.writestr("story.md", current["export_text"].encode("utf-8"))
            archive.writestr(
                "manifest.json",
                json.dumps(current["manifest"], ensure_ascii=False, indent=2).encode("utf-8"),
            )
            for filename, content in files.items():
                archive.writestr(filename, content)
        content = stream.getvalue()
        manager = get_runtime_paths()
        path = validate_runtime_path(
            Path(output_dir) if output_dir else manager.artifacts / "comic-stories"
        ) / (f"story-v{story['version']}-{uuid4().hex[:12]}.zip")
        committed = False
        try:
            atomic_write_bytes(
                path, content, file_type="export", owner="comic.story", replace=False
            )
            with self.projects._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if saved := self.media.request_result(
                    anchor, operation, request, connection=connection
                ):
                    self._export_record(saved["export_artifact_id"])
                    return saved
                self._prepare(connection, request)
                refreshed = self.view(request.conversation_id, connection=connection)
                if (
                    refreshed["confirmation"] != current["confirmation"]
                    or refreshed["review"] != current["review"]
                ):
                    raise ToolError("故事确认或核验已改变，请核对后重新导出")
                self._images(
                    self._draft(story),
                    anchor,
                    request.conversation_id,
                    previous=story["fixed_images"],
                    connection=connection,
                )
                artifact_id, now = "artifact-" + uuid4().hex, utc_now()
                self.runtime.insert_artifact(
                    connection,
                    ArtifactRecord(
                        id=artifact_id,
                        type=ArtifactType.DOCUMENT,
                        run_id=run_id,
                        conversation_id=request.conversation_id,
                        node_id=operation,
                        source="comic.story.export",
                        status="ready",
                        created_at=now,
                        location=str(path),
                        metadata={
                            "project_id": anchor,
                            "story_id": story["story_id"],
                            "story_version": story["version"],
                            "content_sha256": story["content_sha256"],
                            "sha256": hashlib.sha256(content).hexdigest(),
                            "origin": "real",
                            "filename": path.name,
                            "mime_type": "application/zip",
                            "request_id": request.request_id,
                            "trace_id": current_trace_id(),
                            "manifest": current["manifest"],
                        },
                    ),
                )
                identity = f"{story['story_id']}:{story['version']}"
                version, _ = self.media._latest(connection, anchor, "story_export", identity)
                self.projects._insert_version(
                    connection,
                    project_id=anchor,
                    entity_type="story_export",
                    entity_id=identity,
                    version=version + 1,
                    payload_json=json.dumps(
                        {
                            "artifact_id": artifact_id,
                            "filename": path.name,
                            "story_version": story["version"],
                        }
                    ),
                    created_at=now,
                )
                result = self._finish(
                    connection, request, operation, run_id=run_id, export_artifact_id=artifact_id
                )
            committed = True
            return result
        finally:
            if not committed:
                manager.remove_file(path, file_type="export", if_registered=True)
