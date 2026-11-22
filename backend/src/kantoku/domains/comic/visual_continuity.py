"""Per-element visual evidence in existing Comic revisions and Core Artifacts.

This is a derived image observation, not another conversation memory or media store.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from kantoku.config import ToolError
from kantoku.config.observability import current_trace_id, public_error
from kantoku.core.runtime.models import ArtifactLifecycle, ArtifactRecord, ArtifactType, utc_now

from .director_provenance import value_hash
from .models import (
    ComicMediaMutation,
    ComicPromptContexts,
    ImageVersion,
    ImageVersionRef,
    VisualContinuityObservation,
)

CONTRACT = "visual-elements-1"
OBJECT_CONTRACT = "visual-elements-2"
ATTRIBUTE_LABELS = {"face": "面容", "hair": "发型", "clothing": "服装", "shape": "外形",
    "color": "颜色", "material": "材质", "position": "位置", "action": "动作",
    "presence": "是否入镜", "environment": "环境", "art_style": "画风", "identity": "身份",
    "appearance": "外观", "lighting": "光影", "composition": "构图"}


def continuity_changes(context: dict) -> list[dict]:
    return context.get("pending_changes", context.get("changes", []))


def visual_model_elements(state: dict) -> list[dict]:
    """The images are supplied once, not repeated as metadata for every property."""
    return [{**{key: item[key] for key in ("element_id", "target", "field", "value")},
             **{key: item[key] for key in ("entity_id", "attribute", "kind") if key in item},
             "source_artifact_id": item["source"]["image"]["artifact_id"]}
            for item in state.get("elements", [])]


def visual_model_references(references: dict, *, current_shot: bool = False) -> dict:
    continuity = references.get("continuity")
    if not continuity or not continuity.get("visual_state"):
        return references
    state = continuity["visual_state"]
    plan = continuity.get("visual_plan")
    if plan and current_shot:
        required = set(plan["required_elements"])
        state = {**state, "elements": [item for item in state.get("elements", [])
            if item["element_id"] in required or semantic_permission(item, plan["changes"])]}
    projected = dict(continuity)
    if plan and current_shot:
        projected["visual_plan"] = {key: value for key, value in plan.items()
                                    if key != "pending_changes"}
    return {**references, "continuity": {**projected, "visual_state": {
        "artifact_id": state["artifact_id"], "contract": state["contract"],
        "elements": visual_model_elements(state), "limitations": state["limitations"]}}}


def pending_changes(state: dict, previous, current) -> list[dict]:
    """Later requests replace the same target, while unrendered changes remain effective."""
    result = {}
    for change in [*previous, *current]:
        targets = change.get("element_ids")
        if targets is None:
            targets = [item["element_id"] for item in state.get("elements", [])
                       if item["field"] == change["field"]]
        for target in targets or [None]:
            result[change["field"], target] = {**change,
                **({"element_ids": [target]} if target else {})}
    if len(result) > 128:
        raise ToolError("待落实的视觉修改超过容量，请先生成并采用当前画面")
    return list(result.values())


def protected_elements(state: dict, changes=()) -> list[dict]:
    return [element for element in state.get("elements", []) if not any(
        (change.get("entity_id") == element.get("entity_id")
         and element.get("attribute") in change.get("attributes", []))
        or (change.get("field") == element["field"] and ("element_ids" not in change
            or element["element_id"] in change["element_ids"])) for change in changes)]


def visual_references(state: dict, changes=(), *, required_elements=None) -> list[dict]:
    """One exact file may establish several properties of several subjects."""
    references = {}
    for element in protected_elements(state, changes):
        if required_elements is not None and element["element_id"] not in required_elements:
            continue
        image = element["source"]["image"]
        key = (image["artifact_id"], image["sha256"])
        reference = references.setdefault(key, {
            "image": {name: image[name] for name in ("image_id", "version")},
            "artifact_id": image["artifact_id"], "source_project_id": image["project_id"],
            "sha256": image["sha256"], "filename": image["filename"],
            "roles": [], "targets": [], "element_ids": [],
        })
        if element.get("entity_id"):
            role = ("style" if element["kind"] == "style" else "composition" if element[
                "attribute"] in {"position", "action", "presence", "composition", "lighting"}
                else "identity")
        else:
            role = ("identity" if element["field"].startswith("character_context.") else
                    "style" if element["field"].startswith("style_context.") else "composition")
        if role not in reference["roles"]:
            reference["roles"].append(role)
        section, leaf = element["field"].split(".")
        label = (ATTRIBUTE_LABELS.get(element["attribute"], element["attribute"])
                 if element.get("entity_id") else
                 ComicPromptContexts.model_fields[section].annotation.labels[leaf])
        reference["targets"].append(element["target"] + " · " + label)
        reference["element_ids"].append(element["element_id"])
    return [{**item, "target": "；".join(item["targets"])} for item in references.values()]


def shot_visual_plan(state: dict, decision, request: str) -> dict:
    """Validate one shot's evidence and semantic permissions, without expanding by field."""
    if decision.required_elements is None or decision.visual_changes is None:
        raise ToolError("本轮缺少对象修改权限或入镜元素选择，原设定已保留")
    elements = {item["element_id"]: item for item in state.get("elements", [])}
    if (len(set(decision.required_elements)) != len(decision.required_elements)
            or any(key not in elements for key in decision.required_elements)):
        raise ToolError("本轮参考元素不属于所选创作分支")
    entities = {item["entity_id"] for item in elements.values()}
    new_objects, changes = {}, []
    for change in decision.visual_changes:
        if change.quote not in request:
            raise ToolError("对象修改缺少本轮用户原话依据")
        value = change.model_dump(exclude_none=True)
        if change.operation == "add":
            if change.entity_id is not None or not change.new_object_key:
                raise ToolError("新增对象须使用独立临时标识，不能替换已有对象")
            value["entity_id"] = new_objects.setdefault(
                change.new_object_key, "object-" + uuid4().hex)
        elif change.entity_id not in entities or change.new_object_key is not None:
            raise ToolError("对象修改身份不属于所选创作分支")
        known = {item["attribute"] for item in elements.values()
                 if item["entity_id"] == value["entity_id"]}
        if change.operation == "change" and set(change.attributes) - known - {
                "action", "pose", "position", "presence", "composition", "lighting", "camera"}:
            raise ToolError("未知外观属性须标为首次设计，不能冒充历史定稿")
        if change.operation == "design" and any(
                item["entity_id"] == value["entity_id"]
                and item["attribute"] in change.attributes for item in elements.values()):
            raise ToolError("首次设计不能覆盖已经确立的属性")
        changes.append(value)
    return {"contract": "visual-plan-2", "required_elements": decision.required_elements,
            "changes": changes}


def semantic_permission(element: dict, changes) -> dict | None:
    return next((change for change in changes if change.get("entity_id") == element.get(
        "entity_id") and element.get("attribute") in change.get("attributes", [])), None)


def shot_references(state: dict, plan: dict) -> list[dict]:
    required = set(plan["required_elements"])
    # A reference for identity is not also permission to reuse an obsolete action/layout.
    chosen = {**state, "elements": [item for item in state.get("elements", [])
        if item["element_id"] in required and not semantic_permission(item, plan["changes"])]}
    return visual_references(chosen, required_elements=required)


class VisualContinuityStore:
    """Observe an adopted image once; preserve unmodified or invisible evidence."""

    def __init__(self, media):
        self.media = media
        self.projects, self.runtime = media.projects, media.runtime

    def require_source(self, work_id, source):
        image = source["image"]
        with self.projects._connect() as connection:
            task = self.media._latest(connection, image["project_id"], "workspace_task",
                                      image["project_id"])[1]
            row = connection.execute(
                "SELECT payload_json FROM comic_entity_versions WHERE project_id=? "
                "AND entity_type='image_selection' AND entity_id=? AND version=?",
                (image["project_id"], image["image_id"], source["selection_version"]),
            ).fetchone()
        if (task.get("work_id") != work_id or task.get("conversation_id") != image[
                "conversation_id"] or not row or json.loads(row[0]).get(
                    "selected_version") != image["version"]):
            raise ToolError("视觉元素来源不属于当前作品，或没有对应的采用记录")
        current = self.media.require_image(ImageVersionRef(
            image_id=image["image_id"], version=image["version"]), image["project_id"],
            image["conversation_id"], snapshot=ImageVersion.model_validate_json(json.dumps(image)))
        if current.artifact_id != image["artifact_id"] or current.sha256 != image["sha256"]:
            raise ToolError("视觉元素的参考图内容已变化")
        return current

    def cached(self, work_id, source):
        with self.projects._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM comic_entity_versions WHERE project_id=? "
                "AND entity_type='visual_continuity' "
                "AND json_extract(payload_json,'$.source.image.artifact_id')=? "
                "AND json_extract(payload_json,'$.source.selection_version')=? "
                "ORDER BY created_at DESC LIMIT 1", (source["image"]["project_id"],
                    source["image"]["artifact_id"], source["selection_version"])).fetchone()
        if row:
            state = json.loads(row[0])
            if state["source"] != source:
                return None
            self.require_state(work_id, state)
            return state
        return None

    def require_state(self, work_id, state):
        artifact = self.runtime.get_artifact(state["artifact_id"])
        payload = artifact.metadata.get("payload", {})
        if (artifact.source != "comic.visual_continuity" or artifact.type != ArtifactType.JSON
                or artifact.status != "ready"
                or artifact.lifecycle_state != ArtifactLifecycle.ACTIVE
                or payload.get("work_id") != work_id
                or payload.get("contract") not in {CONTRACT, OBJECT_CONTRACT}
                or not payload or any(state.get(key) != value for key, value in payload.items())):
            raise ToolError("视觉元素状态的身份或版本不一致")
        sources = [payload["source"], *[item["source"] for item in payload["elements"]]]
        seen = set()
        for source in sources:
            key = (source["image"]["artifact_id"], source["image"]["sha256"])
            if key not in seen:
                self.require_source(work_id, source)
                seen.add(key)
        return payload

    def observe(self, work_id, source, *, inherited=None, changes=(), model_call, run_id=None,
                required_elements=None, deferred_changes=()):
        image = self.require_source(work_id, source)
        previous = ({**self.require_state(work_id, inherited),
                     "artifact_id": inherited["artifact_id"]} if inherited else None)
        with self.projects._connect() as connection:
            choice = json.loads(self.projects._version_payload(connection, image.project_id,
                "image_selection", image.image_id, source["selection_version"]))
        extra = choice.get("accepted_visual_changes", [])
        if extra:
            if not previous or previous["contract"] != OBJECT_CONTRACT:
                raise ToolError("额外采用范围缺少对象属性依据")
            overridden = {(item["entity_id"], attribute) for item in extra
                          for attribute in item["attributes"]}
            changes = [*[{**item, "attributes": attributes} for item in changes
                if (attributes := [attribute for attribute in item.get("attributes", [])
                    if (item["entity_id"], attribute) not in overridden])], *extra]
        identity = value_hash({"source": source, "inherited": inherited, "changes": changes,
                               "contract": OBJECT_CONTRACT, "required": required_elements,
                               "deferred": deferred_changes})
        with self.projects._connect() as connection:
            saved = self.media._latest(connection, image.project_id,
                                       "visual_continuity", identity)[1]
        if saved:
            self.require_state(work_id, saved)
            return saved
        request = ComicMediaMutation(
            request_id="visual-" + identity, conversation_id=image.conversation_id,
            expected_project_version=self.projects.get(image.project_id).project.current_version)
        operation = "image.visual_continuity"
        if saved := self.media.claim_request(image.project_id, operation, request, run_id=run_id):
            self.require_state(work_id, saved)
            return saved
        try:
            old = {item["element_id"]: item for item in (previous or {}).get("elements", [])}
            scope = old if required_elements is None else {
                key: item for key, item in old.items() if key in required_elements
                or semantic_permission(item, changes)}
            context = {"stage": "visual_continuity",
                       "requested_contract": OBJECT_CONTRACT,
                       "established_elements": visual_model_elements(
                           {"elements": list(scope.values())}),
                       "authorized_changes": list(changes),
                       "description_status": "image_observations_not_user_facts"}
            images = [source["image"]]
            for item in scope.values():
                reference = item["source"]["image"]
                if not any(value["artifact_id"] == reference["artifact_id"] for value in images):
                    images.append(reference)
            if len(images) > 8:
                raise ToolError("元素参考超过八张，请明确本轮需要延续的画面，未省略任何参考图")
            parts = [{"type": "text", "text": json.dumps(context, ensure_ascii=False)}]
            for reference in images:
                artifact = self.runtime.get_artifact(reference["artifact_id"])
                data = Path(artifact.location).read_bytes()
                if hashlib.sha256(data).hexdigest() != reference["sha256"]:
                    raise ToolError("视觉元素的参考图文件已变化")
                parts.extend([
                    {"type": "text", "text": json.dumps({"image": reference["artifact_id"]})},
                    {"type": "image_url", "image_url": {"url":
                        f"data:{reference['mime_type']};base64,"
                        + base64.b64encode(data).decode()}},
                ])
            raw = model_call([{"role": "system", "content": (
                "观察第一张已采用图片；其余图片是逐元素标明的旧参考，不是本轮目标图。"
                "按具体人物/道具/环境对象分别记录可见属性，不把多人合成一个角色。"
                "新输出使用 contract=visual-elements-2。每项填写 entity_id、attribute、kind；"
                "同一对象跨栏目使用同一 entity_id；已有身份不能因改名或换栏目改变。"
                "首次对象 entity_id=null，并为同一对象的全部属性使用同一个 entity_key，"
                "entity_key 只是本响应内分组，不以对象名字作永久身份。"
                "attribute 使用语义属性标识，如 face/hair/clothing/shape/color/material/"
                "position/action/presence/environment/art_style；field 仅是兼容展示栏目。"
                "authorized_changes 若包含 entity_id/attributes，其权限只由对象及属性决定，"
                "不由 field 决定；add 是明确新增对象，design 是此前未知属性的首次设计。"
                "target 应明确具体对象与属性；同一对象的外形、位置、是否入镜分别记录，"
                "面容与发型分别记录，不能把所有道具及位置包成一项环境。"
                "记录身份、面容、服装、道具、环境、风格、构图、光影等，field 使用 Schema 路径。"
                "每个 established_elements 的 element_id 必须且只能返回一次，保留 target/field；"
                "看不到脸、背面、遮挡或暂时出画用 occluded/absent/uncertain，不猜测。"
                "新发现属性 element_id=null；未知细节不能从 Prompt 或旧文字推断为像素事实。"
                "与旧证据的实际差异写 comparison=changed，即使画面已采用也不能隐藏差异。"
                "只有 authorized_changes 的原话确实允许改变该具体对象及属性时，逐字填写"
                "authorization_quote；element_ids 非空时只能作用于列出的元素。"
                "change_satisfied 只有可见结果确实满足这条原话才填 true；例如要求红衣却画成"
                "蓝衣应为 false，遮挡看不到应为 null，不能只因允许改服装就把任意新衣视为成功。"
                "动作或遮挡变化不允许换脸、换衣或增删道具。未授权的差异不得借采用更新设定。"
                "只返回公开观察 JSON，不输出私有推理："
                + json.dumps(VisualContinuityObservation.model_json_schema(), ensure_ascii=False)
            )}, {"role": "user", "content": parts}])
            try:
                output = VisualContinuityObservation.model_validate_json(raw)
            except (ValueError, ValidationError):
                raise ToolError("视觉元素观察未返回有效结构，原设定已保留") from None
            if output.contract == OBJECT_CONTRACT:
                return self._observe_objects(work_id, source, previous, changes, output,
                    identity=identity, request=request, operation=operation, run_id=run_id,
                    required_elements=list(scope), deferred_changes=deferred_changes)
            if self.runtime.get_artifact(image.artifact_id).metadata.get("generation_task_id"):
                raise ToolError("固定任务的视觉观察须使用对象属性契约，原设定已保留")
            if previous and previous["contract"] == OBJECT_CONTRACT:
                raise ToolError("对象观察不能降级为旧字段契约，原设定已保留")
            seen, keys, merged, findings = set(), set(), dict(old), []
            applied, applied_quotes = set(), set()
            for observation in output.elements:
                key = (observation.target, observation.field)
                if key in keys or (observation.element_id and observation.element_id in seen):
                    raise ToolError("视觉元素观察包含重复对象或属性，原设定已保留")
                keys.add(key)
                prior = old.get(observation.element_id)
                if observation.element_id:
                    if prior is None or (prior["target"], prior["field"]) != key:
                        raise ToolError("视觉元素观察改写了原对象身份，原设定已保留")
                    seen.add(observation.element_id)
                permitted = any(change["field"] == observation.field
                    and change["quote"] == observation.authorization_quote
                    and ("element_ids" not in change
                        or observation.element_id in change["element_ids"]
                        or (observation.element_id is None and not change["element_ids"]))
                    for change in changes)
                if observation.authorization_quote is not None and not permitted:
                    raise ToolError("视觉元素修改没有对应的用户授权，原设定已保留")
                if observation.visibility != "visible" or not observation.observation.strip():
                    continue
                if permitted and observation.change_satisfied is not True:
                    findings.append({"target": observation.target, "field": observation.field,
                        "observed": observation.observation, "advisory": True,
                        "reason": "本次修改尚未被图像证实", "source": source})
                    continue
                if prior and not permitted:
                    if observation.comparison == "changed":
                        findings.append({"element_id": prior["element_id"], "target": prior[
                            "target"], "field": prior["field"], "expected": prior["value"],
                            "observed": observation.observation, "source": source,
                            "advisory": True})
                    continue
                if not prior and any((item["target"], item["field"]) == key
                                     for item in old.values()):
                    raise ToolError("已有视觉元素不能通过新身份覆盖")
                if (old and not prior and not permitted and observation.target not in {
                        item["target"] for item in old.values()}):
                    findings.append({"target": observation.target, "field": observation.field,
                                     "observed": observation.observation, "advisory": True,
                                     "reason": "未授权新增对象"})
                    continue
                identifier = observation.element_id or "element-" + uuid4().hex
                if permitted:
                    applied.add(identifier)
                    applied_quotes.add((observation.field, observation.authorization_quote))
                merged[identifier] = {"element_id": identifier, "target": observation.target,
                    "field": observation.field, "value": observation.observation,
                    "source": source, "origin": "image_observation",
                    "authorization_quote": observation.authorization_quote}
            if seen != set(old):
                raise ToolError("视觉观察遗漏了已确定元素，原设定已保留")
            unresolved = []
            for change in changes:
                targets = change.get("element_ids", [key for key, item in old.items()
                                                      if item["field"] == change["field"]])
                remaining = [key for key in targets if key not in applied]
                if remaining or (not targets and (change["field"], change["quote"])
                                 not in applied_quotes):
                    unresolved.append({**change, **({"element_ids": remaining} if targets else {})})
            payload = {"contract": CONTRACT, "work_id": work_id, "source": source,
                       "elements": list(merged.values()), "findings": findings,
                       "observations": [item.model_dump(mode="json") for item in output.elements],
                       "unresolved_changes": unresolved,
                       "limitations": output.limitations,
                       "parent_artifact_id": inherited["artifact_id"] if inherited else None}
            return self._save_observation(work_id, source, inherited, payload,
                identity=identity, request=request, operation=operation, run_id=run_id)
        except Exception as error:
            try:
                self.media.fail_request(image.project_id, request, error)
            except Exception as persistence_error:
                public_error(persistence_error, component="comic.visual_continuity.failure_record",
                             project_id=image.project_id, request_id=request.request_id)
            public_error(error, component="comic.visual_continuity", project_id=image.project_id,
                         request_id=request.request_id, run_id=run_id)
            raise

    def _save_observation(self, work_id, source, inherited, payload, *, identity, request,
                          operation, run_id):
        self.require_source(work_id, source)
        if inherited:
            self.require_state(work_id, inherited)
        image = source["image"]
        with self.projects._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.media._write_guard(connection, image["project_id"], request)
            artifact = ArtifactRecord(id="artifact-" + uuid4().hex, type=ArtifactType.JSON,
                run_id=run_id, conversation_id=image["conversation_id"],
                node_id=operation, source="comic.visual_continuity", status="ready",
                created_at=utc_now(), metadata={"internal": True, "payload": payload,
                    "project_id": image["project_id"], "trace_id": current_trace_id()})
            self.runtime.insert_artifact(connection, artifact)
            result = {"artifact_id": artifact.id, **payload,
                      "project_version": request.expected_project_version}
            self.projects._insert_version(connection, project_id=image["project_id"],
                entity_type="visual_continuity", entity_id=identity, version=1,
                payload_json=json.dumps(result, ensure_ascii=False), created_at=utc_now())
            return self.media._finish(connection, image["project_id"], operation, request,
                                      result, advance_project=False)

    def _observe_objects(self, work_id, source, previous, changes, output, *, identity,
                         request, operation, run_id, required_elements=None, deferred_changes=()):
        old = {item["element_id"]: item for item in (previous or {}).get("elements", [])}
        known_entities = {item.get("entity_id") for item in old.values()} - {None}
        entity_kinds = {item["entity_id"]: item["kind"] for item in old.values()
                        if item.get("entity_id")}
        merged, findings, groups, seen, applied = dict(old), [], {}, set(), set()
        keys = {(item.get("entity_id"), item.get("attribute")): key
                for key, item in old.items() if item.get("entity_id")}
        returned = set()
        for observation in output.elements:
            prior = old.get(observation.element_id)
            if observation.element_id and (not prior or observation.element_id in seen):
                raise ToolError("视觉观察包含未知或重复属性身份，原设定已保留")
            if not observation.attribute or not observation.kind:
                raise ToolError("对象观察缺少语义属性或对象类型，原设定已保留")
            entity = observation.entity_id
            if prior and prior.get("entity_id"):
                if (entity != prior["entity_id"] or observation.attribute != prior["attribute"]
                        or observation.kind != prior["kind"]):
                    raise ToolError("对象观察改写了既有身份或属性，原设定已保留")
            elif entity is None:
                if not observation.entity_key:
                    raise ToolError("首次对象缺少属性分组，原设定已保留")
                entity = groups.setdefault(observation.entity_key, "object-" + uuid4().hex)
            elif entity not in known_entities and not any(
                    item.get("operation") == "add" and item.get("entity_id") == entity
                    for item in changes):
                raise ToolError("对象观察引用了其他分支或未知身份")
            key = (entity, observation.attribute)
            if entity_kinds.setdefault(entity, observation.kind) != observation.kind:
                raise ToolError("同一对象不能被改成另一种对象类型")
            if key in returned or (key in keys and keys[key] != observation.element_id):
                raise ToolError("同一对象属性不能通过新身份重复建立")
            returned.add(key)
            if prior:
                seen.add(observation.element_id)
            element = {"entity_id": entity, "attribute": observation.attribute}
            permission = semantic_permission(element, changes)
            if prior and previous and previous["contract"] == CONTRACT and permission is None:
                legacy = next((item for item in changes if item.get("field") == prior["field"]
                    and ("element_ids" not in item or prior["element_id"] in item["element_ids"])
                    and item["quote"] == observation.authorization_quote), None)
                if legacy:
                    permission = {**element, "attributes": [observation.attribute],
                                  "quote": legacy["quote"]}
            if (observation.authorization_quote is not None and (permission is None
                    or permission["quote"] != observation.authorization_quote)):
                raise ToolError("对象属性修改没有对应授权，原设定已保留")
            if prior and not prior.get("entity_id"):
                # Upgrade identity metadata only; hidden facts retain their original pixels.
                merged[observation.element_id] = {**prior, **element, "kind": observation.kind}
            if observation.visibility != "visible" or not observation.observation.strip():
                continue
            permitted = permission is not None and observation.authorization_quote == permission[
                "quote"] and observation.change_satisfied is True
            if previous and not permitted:
                if not prior or observation.comparison == "changed" or permission:
                    findings.append({**element, "target": observation.target,
                        "field": observation.field, "expected": prior["value"] if prior else None,
                        "observed": observation.observation, "advisory": True,
                        "reason": "修改尚未证实或未经授权", "source": source})
                continue
            identifier = observation.element_id or "element-" + uuid4().hex
            merged[identifier] = {**element, "element_id": identifier,
                "target": observation.target, "field": observation.field, "kind": observation.kind,
                "value": observation.observation, "source": source,
                "origin": "image_observation",
                "authorization_quote": observation.authorization_quote}
            if permitted:
                applied.add(key)
        expected = set(old) if required_elements is None else set(required_elements)
        if not expected.issubset(seen):
            raise ToolError("视觉观察遗漏了本轮必须核对的属性，原设定已保留")
        normalized = [item for item in changes if "attributes" in item]
        for change in changes:
            if "field" not in change:
                continue
            normalized.extend({"entity_id": item["entity_id"], "target": item["target"],
                "attributes": [item["attribute"]], "quote": change["quote"], "operation": "change"}
                for key, item in merged.items() if key in old and item.get("entity_id")
                and old[key]["field"] == change["field"]
                and ("element_ids" not in change or key in change["element_ids"]))
        unresolved = [{**change, "attributes": remaining} for change in normalized
            if (remaining := [attribute for attribute in change.get("attributes", [])
                              if (change["entity_id"], attribute) not in applied])]
        payload = {"contract": OBJECT_CONTRACT, "work_id": work_id, "source": source,
            "elements": list(merged.values()), "findings": findings,
            "observations": [item.model_dump(mode="json") for item in output.elements],
            "unresolved_changes": [*unresolved, *deferred_changes],
            "limitations": output.limitations,
            "legacy_unresolved_changes": [item for item in changes if "field" in item],
            "parent_artifact_id": previous["artifact_id"] if previous else None}
        return self._save_observation(work_id, source, previous, payload, identity=identity,
            request=request, operation=operation, run_id=run_id)
