"""Real isolated stores and image bytes; visual judgements are explicit offline substitutes."""

import base64
import json
from pathlib import Path
from uuid import uuid4

import pytest
from test_comic_stories import _png, _select, _upload
from test_comic_stories import story_app as story_app_fixture
from test_comic_workspace import work

from kantoku.config import ToolError
from kantoku.config.runtime_paths import atomic_write_bytes
from kantoku.domains.comic.models import ImageContinuityChange
from kantoku.domains.comic.visual_continuity import (
    VisualContinuityStore,
    pending_changes,
    protected_elements,
    visual_references,
)
from kantoku.domains.comic.workspace import ComicWorkspaceService

story_app = story_app_fixture


def object_observation(target, attribute, value, *, entity=None, prior=None,
                       kind="subject", field="character_context.appearance",
                       visibility="visible", quote=None, satisfied=None, comparison="consistent"):
    return {"element_id": prior, "entity_id": entity, "entity_key": target,
        "target": target, "attribute": attribute, "kind": kind, "field": field,
        "observation": value, "visibility": visibility, "comparison": comparison,
        "authorization_quote": quote, "change_satisfied": satisfied}


def object_model(rows):
    return lambda _: json.dumps({"contract": "visual-elements-2", "elements": rows,
                                 "limitations": []})


@pytest.mark.parametrize("subject", ["人物", "机械主体", "商品"])
def test_objects_share_identity_across_prompt_columns_and_keep_invisible_facts(story_app, subject):
    app, store = story_app, VisualContinuityStore(story_app.comic_media)
    parent = work(app)
    first = source(app, parent, 30)
    initial = store.observe(parent["work_id"], first, model_call=object_model([
        object_observation(subject, "shape", "原始外形"),
        object_observation(subject, "color", "蓝色", field="style_context.color"),
        object_observation("杯子", "shape", "缺口杯", kind="prop",
                           field="world_context.environment")]))
    shape, color, cup = initial["elements"]
    assert shape["entity_id"] == color["entity_id"] != cup["entity_id"]
    quote = "把主体改成红色"
    changes = [{"entity_id": color["entity_id"], "attributes": ["color"],
                "target": subject, "operation": "change", "quote": quote}]
    second = source(app, parent, 70)
    rows = [object_observation(item["target"], item["attribute"], "红色" if item == color else "",
        entity=item["entity_id"], prior=item["element_id"], kind=item["kind"],
        field="character_context.clothing" if item == color else item["field"],
        visibility="visible" if item == color else "absent",
        quote=quote if item == color else None, satisfied=True if item == color else None)
        for item in initial["elements"]]
    back = store.observe(parent["work_id"], second, inherited=initial, changes=changes,
                         model_call=object_model(rows))
    assert back["elements"][0]["source"] == first
    assert back["elements"][1]["source"] == second
    assert back["elements"][2]["source"] == first
    # Reusing an earlier branch is unaffected by later adoption.
    assert store.require_state(parent["work_id"], initial)["elements"][1]["value"] == "蓝色"
    from kantoku.domains.comic.visual_continuity import shot_references
    refs = shot_references(back, {"required_elements": [shape["element_id"], color["element_id"]],
                                  "changes": []})
    assert [item["artifact_id"] for item in refs] == [first["image"]["artifact_id"],
                                                     second["image"]["artifact_id"]]


def test_new_object_permission_is_semantic_and_unknown_face_needs_first_design(story_app):
    from kantoku.domains.comic.models import ImageContinuityDecision
    from kantoku.domains.comic.visual_continuity import shot_visual_plan

    app, store = story_app, VisualContinuityStore(story_app.comic_media)
    parent = work(app)
    first = source(app, parent, 10)
    root = store.observe(parent["work_id"], first, model_call=object_model([
        object_observation("主角", "clothing", "灰衣", field="character_context.clothing")]))
    coat = root["elements"][0]
    quote = "主角转回来露脸，拿起新杯子"
    decision = ImageContinuityDecision(candidate_id="test", required_elements=[coat["element_id"]],
        visual_changes=[{"entity_id": coat["entity_id"], "target": "主角", "attributes": ["face"],
                         "quote": quote, "operation": "design"},
                        {"target": "杯子", "new_object_key": "cup", "attributes": ["shape"],
                         "quote": quote, "operation": "add"}])
    plan = shot_visual_plan(root, decision, quote)
    invalid = decision.model_copy(deep=True)
    invalid.visual_changes[0].operation = "change"
    with pytest.raises(ToolError, match="首次设计"):
        shot_visual_plan(root, invalid, quote)
    cup_id = plan["changes"][1]["entity_id"]
    second = source(app, parent, 90)
    observations = [
        object_observation("主角", "clothing", "灰衣", entity=coat["entity_id"],
                           prior=coat["element_id"], field=coat["field"]),
        object_observation("主角", "face", "首次可见的面容", entity=coat["entity_id"],
                           quote=quote, satisfied=True),
        object_observation("杯子", "shape", "白瓷杯", entity=cup_id, kind="prop",
                           field="shot_context.action", quote=quote, satisfied=True)]
    result = store.observe(parent["work_id"], second, inherited=root, changes=plan["changes"],
                           model_call=object_model(observations))
    assert not result["findings"] and not result["unresolved_changes"]
    assert len(result["elements"]) == 3
    assert all(item["attribute"] != "face" for item in root["elements"])
    assert result["elements"][2]["entity_id"] == cup_id
    # A later accidental new face must never become the new reference.
    observations[1].update(element_id=result["elements"][1]["element_id"],
        observation="意外换脸", authorization_quote=None, change_satisfied=None,
        comparison="changed")
    observations[2].update(element_id=result["elements"][2]["element_id"],
        authorization_quote=None, change_satisfied=None)
    third = source(app, parent, 100)
    drift = store.observe(parent["work_id"], third, inherited=result,
                          model_call=object_model(observations))
    assert drift["elements"][1] == result["elements"][1]
    assert drift["findings"][0]["attribute"] == "face"


def test_irrelevant_history_does_not_grow_current_references_or_execution_text():
    from kantoku.domains.comic.models import ComicPromptDraft
    from kantoku.domains.comic.prompts import execution_prompt
    from kantoku.domains.comic.visual_continuity import shot_references

    image = {"image_id": "image-a", "version": 1, "artifact_id": "artifact-a",
             "project_id": "branch-a", "sha256": "a" * 64, "filename": "a.png"}
    item = {"element_id": "face", "entity_id": "person", "attribute": "face", "kind": "subject",
            "target": "角色", "field": "character_context.appearance", "value": "原面容",
            "source": {"image": image}}
    state = {"elements": [item]}
    plan = {"required_elements": ["face"], "changes": []}
    draft = ComicPromptDraft(director_summary="本轮", positive_prompt="角色抬头", context_sources={
        "image_references": {"continuity": {"visual_state": state, "visual_plan": plan}}})
    text, references = execution_prompt(draft), shot_references(state, plan)
    state["elements"].extend({**item, "element_id": f"unrelated-{index}",
        "entity_id": f"object-{index}", "value": "无关历史", "source": {"image": {
            **image, "artifact_id": f"old-{index}"}}} for index in range(100))
    assert execution_prompt(draft) == text
    assert shot_references(state, plan) == references


def test_deferred_offscreen_change_keeps_its_record_without_sending_its_reference(story_app):
    app, store = story_app, VisualContinuityStore(story_app.comic_media)
    parent = work(app)
    first = source(app, parent, 30)
    root = store.observe(parent["work_id"], first, model_call=object_model([
        object_observation("人物", "face", "原面容"),
        object_observation("杯子", "color", "蓝色", kind="prop", field="style_context.color")]))
    face, cup = root["elements"]
    red = {"entity_id": cup["entity_id"], "target": "杯子", "attributes": ["color"],
           "operation": "change", "quote": "杯子改红色"}
    second = source(app, parent, 60)
    changed = store.observe(parent["work_id"], second, inherited=root, changes=[red],
        required_elements=[cup["element_id"]], model_call=object_model([
            object_observation("杯子", "color", "红色", kind="prop", field="style_context.color",
                entity=cup["entity_id"], prior=cup["element_id"], quote=red["quote"],
                satisfied=True)]))
    deferred = {**red, "quote": "下次杯子改白色"}
    third = source(app, parent, 90)

    def observe_face(messages):
        parts = messages[1]["content"]
        pixels = [base64.b64decode(item["image_url"]["url"].split(",", 1)[1])
                  for item in parts if item["type"] == "image_url"]
        assert pixels == [_png(90), _png(30)]
        assert json.loads(parts[0]["text"])["authorized_changes"] == []
        return object_model([object_observation("人物", "face", "原面容",
            entity=face["entity_id"], prior=face["element_id"])])(messages)

    result = store.observe(parent["work_id"], third, inherited=changed,
        required_elements=[face["element_id"]], deferred_changes=[deferred],
        model_call=observe_face)
    assert result["unresolved_changes"] == [deferred]
    assert result["elements"][1]["source"] == second


def test_legacy_visual_facts_upgrade_identity_without_overwriting_hidden_originals(story_app):
    app, store = story_app, VisualContinuityStore(story_app.comic_media)
    parent = work(app)
    first = source(app, parent, 15)
    root = store.observe(parent["work_id"], first, model_call=model([
        observed("人物", "character_context.appearance", "原面容"),
        observed("人物", "character_context.clothing", "棕衣")], []))
    face, coat = root["elements"]
    second = source(app, parent, 75)
    quote = "人物换红衣并背过身"
    upgraded = store.observe(parent["work_id"], second, inherited=root, changes=[{
        "field": coat["field"], "element_ids": [coat["element_id"]], "quote": quote}],
        model_call=object_model([
            object_observation("人物", "face", "", prior=face["element_id"], visibility="occluded"),
            object_observation("人物", "clothing", "红衣", prior=coat["element_id"],
                field=coat["field"], quote=quote, satisfied=True)]))
    assert upgraded["contract"] == "visual-elements-2"
    assert upgraded["elements"][0]["source"] == first
    assert upgraded["elements"][1]["source"] == second
    assert upgraded["elements"][0]["entity_id"] == upgraded["elements"][1]["entity_id"]
    assert root["contract"] == "visual-elements-1" and "entity_id" not in face


def _target_elements():
    return {key: {"element_id": key, "target": target, "field": field, "value": value}
        for key, target, field, value in [
            ("coat-a", "甲", "character_context.clothing", "棕衣"),
            ("coat-b", "乙", "character_context.clothing", "白衣"),
            ("face-a", "甲", "character_context.appearance", "原面容")]}


def _locate(changes, model_call, *, request="甲换红衣，甲的脸颊增加伤疤", budget=18000):
    ComicWorkspaceService._visual_change_targets(request, changes, _target_elements(),
        model_call=model_call, budget=budget)


def test_new_visual_properties_and_explicit_empty_targets_do_not_need_another_model():
    request = ("这个镜头是主角拿出剑刚好格挡住一只箭的特写 旁边几只箭的落点也要交代清楚 "
               "这个整个画风要严峻 主角表情要变得凶恶但是整张脸不用完全暴露在镜头留点阴影")
    changes = [ImageContinuityChange(field=field, quote=request) for field in (
        "shot_context.action", "shot_context.pose", "shot_context.lighting_changes")]
    changes.append(ImageContinuityChange(field="character_context.clothing", quote=request,
                                        element_ids=[]))
    _locate(changes, lambda _: pytest.fail("No existing attribute needs semantic targeting"),
            request=request)
    assert [change.element_ids for change in changes] == [[], [], [], []]


@pytest.mark.parametrize("output", [
    {"element_ids": [["coat-a"]]},
    {"element_ids": ["coat-a"]},
    {"element_ids": [["coat-a"]], "question": None},
])
def test_single_visual_target_has_an_unambiguous_wire_format(output):
    changes = [ImageContinuityChange(field="character_context.clothing", quote="甲换红衣")]
    calls = []

    def locate(messages):
        calls.append(messages)
        assert "coat-a" in messages[0]["content"]
        assert "face-a" not in messages[0]["content"]
        return json.dumps(output)

    _locate(changes, locate)
    assert changes[0].element_ids == ["coat-a"]
    assert len(calls) == 1


@pytest.mark.parametrize("invalid", ["json", "root", "count", "unknown", "field", "private"])
def test_visual_target_structure_is_corrected_once_with_only_validated_fragments(invalid):
    changes = [ImageContinuityChange(field="character_context.clothing", quote="甲换红衣"),
               ImageContinuityChange(field="character_context.appearance",
                                     quote="甲的脸颊增加伤疤")]
    first = {"element_ids": [["coat-a"], ["face-a"]]}
    if invalid == "count":
        first["element_ids"].pop()
    if invalid in {"unknown", "field"}:
        first["element_ids"][1] = ["unknown-private-id" if invalid == "unknown" else "coat-b"]
    if invalid == "private":
        first["private_reasoning"] = "PRIVATE_RESPONSE_MUST_NOT_LEAVE"
    raw = "PRIVATE_RESPONSE_MUST_NOT_LEAVE" if invalid == "json" else (
        json.dumps([first]) if invalid == "root" else json.dumps(first))
    calls = []

    def locate(messages):
        calls.append(messages)
        body = json.loads(messages[1]["content"])
        assert [change.element_ids for change in changes] == [[], []]
        if len(calls) == 1:
            return raw
        assert len(calls) == 2
        assert "repair" in body
        assert "PRIVATE_RESPONSE_MUST_NOT_LEAVE" not in json.dumps(messages)
        assert "unknown-private-id" not in json.dumps(messages)
        if invalid in {"unknown", "field", "private"}:
            assert body["repair"]["fixed"]["0"] == ["coat-a"]
        return json.dumps({"element_ids": [["coat-a"], ["face-a"]]})

    _locate(changes, locate)
    assert len(calls) == 2
    assert [change.element_ids for change in changes] == [["coat-a"], ["face-a"]]


@pytest.mark.parametrize("failure", ["persistent", "expand", "transport", "ambiguity"])
def test_visual_target_failure_keeps_changes_atomic_and_does_not_retry_transport(failure):
    changes = [ImageContinuityChange(field="character_context.clothing", quote="甲换红衣"),
               ImageContinuityChange(field="character_context.appearance",
                                     quote="甲的脸颊增加伤疤")]
    original = [item.model_dump() for item in changes]
    calls = []

    def locate(messages):
        calls.append(messages)
        if failure == "transport":
            raise ToolError("Offline connection failed; outcome unknown")
        if failure == "ambiguity":
            return json.dumps({"question": "需要修改哪位人物？"})
        if len(calls) == 1 or failure == "persistent":
            return json.dumps({"element_ids": [["coat-a"], ["unknown-private-id"]]})
        return json.dumps({"element_ids": [["coat-a", "coat-b"], ["face-a"]]})

    with pytest.raises(ToolError):
        _locate(changes, locate)
    assert [item.model_dump() for item in changes] == original
    assert len(calls) == (1 if failure in {"transport", "ambiguity"} else 2)


def test_visual_target_correction_checks_capacity_before_another_call(monkeypatch):
    from kantoku.domains.comic import workspace

    changes = [ImageContinuityChange(field="character_context.clothing", quote="甲换红衣")]
    calls = []
    monkeypatch.setattr(workspace, "token_size", lambda messages: 18001
        if "repair" in json.loads(messages[1]["content"]) else 1)
    with pytest.raises(ToolError, match="容量"):
        _locate(changes, lambda messages: calls.append(messages) or "invalid")
    assert len(calls) == 1 and changes[0].element_ids == []


@pytest.mark.parametrize("ids", [["coat-a"], ["face-a"], ["unknown"], ["coat-a", "coat-a"]])
def test_existing_explicit_targets_are_validated_before_any_target_model(ids):
    changes = [ImageContinuityChange(field="character_context.clothing", quote="甲换红衣",
                                     element_ids=ids)]
    def invoke(_):
        pytest.fail("Already supplied identities must not cause another call")
    if ids == ["coat-a"]:
        _locate(changes, invoke)
    else:
        with pytest.raises(ToolError, match="未知元素或其他属性"):
            _locate(changes, invoke)
    assert changes[0].element_ids == ids


def test_correction_cannot_drop_a_valid_identity_inside_an_invalid_group():
    changes = [ImageContinuityChange(field="character_context.clothing", quote="甲换红衣")]
    calls = []

    def locate(messages):
        calls.append(messages)
        return json.dumps({"element_ids": [["coat-a", "unknown"]] if len(calls) == 1
                           else [["coat-b"]]})

    with pytest.raises(ToolError, match="改写了已核验范围"):
        _locate(changes, locate)
    assert len(calls) == 2 and changes[0].element_ids == []


def source(app, parent, color):
    task = app.comic_workspace.create_task(parent["work_id"], {
        "request_id": uuid4().hex, "expected_version": app.comic_workspace.get(
            parent["work_id"])["version"], "title": "Offline visual evidence"},
        conversation_id=parent["conversation_id"])
    image = _upload(app, task["project_id"], parent["conversation_id"], content=_png(color))
    choice = _select(app, parent["conversation_id"], image)
    return {"image": app.comic_media.get(image["image_id"]).model_dump(mode="json"),
            "selection_version": choice["selection_version"]}


def observed(target, field, value, *, prior=None, visible="visible", comparison="consistent",
             quote=None):
    return {"element_id": prior, "target": target, "field": field,
            "observation": value, "visibility": visible, "comparison": comparison,
            "authorization_quote": quote, "change_satisfied": True if quote else None}


def model(elements, calls, *, image_bytes=None):
    def invoke(messages):
        parts = messages[1]["content"]
        calls.append(json.loads(parts[0]["text"]))
        pixels = [base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
                  for part in parts if part["type"] == "image_url"]
        if image_bytes is not None:
            assert pixels == image_bytes
        return json.dumps({"elements": elements, "limitations": ["Offline visual substitute"]})
    return invoke


def test_face_back_face_and_targeted_outfit_keep_distinct_original_evidence(story_app):
    app, calls = story_app, []
    parent = work(app)
    first = source(app, parent, 10)
    store = VisualContinuityStore(app.comic_media)
    root = store.observe(parent["work_id"], first, model_call=model([
        observed("甲", "character_context.appearance", "甲的原面容"),
        observed("甲", "character_context.clothing", "棕衣"),
        observed("乙", "character_context.appearance", "乙的原面容"),
        observed("乙", "character_context.clothing", "白衣"),
        observed("杯子", "world_context.environment", "带缺口的蓝杯"),
        observed("房间", "world_context.environment", "圆窗和木桌"),
        observed("画面", "style_context.art_style", "水墨"),
    ], calls, image_bytes=[_png(10)]))
    ids = {(item["target"], item["field"]): item["element_id"] for item in root["elements"]}
    quote = "甲转过身来，甲的衣服换成红色"
    second = source(app, parent, 80)
    changes = [{"field": "character_context.clothing", "quote": quote,
                "element_ids": [ids["甲", "character_context.clothing"]]}]
    rows = []
    for item in root["elements"]:
        clothing = item["target"] == "甲" and item["field"].endswith("clothing")
        rows.append(observed(item["target"], item["field"], "红衣" if clothing else item["value"],
            prior=item["element_id"], quote=quote if clothing else None,
            visible="occluded" if item["field"].endswith("appearance") else
                    "absent" if item["target"] == "杯子" else "visible",
            comparison="changed" if clothing else "consistent"))
    back = store.observe(parent["work_id"], second, inherited=root, changes=changes,
                         model_call=model(rows, calls, image_bytes=[_png(80), _png(10)]))
    values = {item["element_id"]: item for item in back["elements"]}
    assert values[ids["甲", "character_context.appearance"]]["source"] == first
    assert values[ids["甲", "character_context.clothing"]]["source"] == second
    assert values[ids["乙", "character_context.clothing"]]["source"] == first
    assert values[ids["杯子", "world_context.environment"]]["value"] == "带缺口的蓝杯"
    assert next(item for item in back["observations"] if item["element_id"] == ids[
        "甲", "character_context.appearance"])["visibility"] == "occluded"
    third = source(app, parent, 140)
    rows = [observed(item["target"], item["field"],
        "模型画错的脸" if item["element_id"] == ids["甲", "character_context.appearance"]
        else item["value"], prior=item["element_id"], comparison="changed"
        if item["element_id"] == ids["甲", "character_context.appearance"] else "consistent")
        for item in back["elements"]]
    front = store.observe(parent["work_id"], third, inherited=back,
        model_call=model(rows, calls, image_bytes=[_png(140), _png(10), _png(80)]))
    assert len(front["findings"]) == 1
    assert front["findings"][0]["expected"] == "甲的原面容"
    assert front["elements"] == back["elements"]
    restarted = VisualContinuityStore(app.comic_media)
    assert restarted.observe(parent["work_id"], third, inherited=back,
        model_call=lambda _: pytest.fail("Replay must not call a model")) == front
    references = visual_references(front)
    assert {item["artifact_id"] for item in references} == {
        first["image"]["artifact_id"], second["image"]["artifact_id"]}
    assert len(calls) == 3


@pytest.mark.parametrize("invalid", ["omitted", "other_identity", "forged_quote", "private"])
def test_invalid_visual_observation_keeps_original_and_never_resubmits(story_app, invalid):
    app, calls = story_app, []
    parent = work(app)
    first = source(app, parent, 1)
    store = VisualContinuityStore(app.comic_media)
    root = store.observe(parent["work_id"], first, model_call=model([
        observed("角色", "character_context.appearance", "原脸")], calls))
    second = source(app, parent, 2)
    old = root["elements"][0]
    row = observed(old["target"], old["field"], "新脸", prior=old["element_id"])
    if invalid == "other_identity":
        row["element_id"] = "invented-identity"
    if invalid == "forged_quote":
        row["authorization_quote"] = "并不存在的用户授权"
    if invalid == "private":
        row["private_reasoning"] = "must never be persisted"
    output = [] if invalid == "omitted" else [row]
    for _attempt in range(2):
        with pytest.raises(ToolError):
            store.observe(parent["work_id"], second, inherited=root,
                          model_call=model(output, calls))
    assert len(calls) == 2  # initial establishment + exactly one failed observation
    assert store.require_state(parent["work_id"], root)["elements"] == root["elements"]


def test_visual_state_rejects_cross_work_tampering_and_changed_bytes(story_app):
    app, calls = story_app, []
    parent = work(app)
    first = source(app, parent, 1)
    store = VisualContinuityStore(app.comic_media)
    state = store.observe(parent["work_id"], first, model_call=model([
        observed("角色", "character_context.appearance", "原脸")], calls))
    with pytest.raises(ToolError):
        store.require_state(work(app)["work_id"], state)
    forged = {**state, "elements": []}
    with pytest.raises(ToolError):
        store.require_state(parent["work_id"], forged)
    artifact = app.runtime_store.get_artifact(first["image"]["artifact_id"])
    atomic_write_bytes(Path(artifact.location), _png(3), owner="offline.test")
    with pytest.raises(ToolError):
        store.require_state(parent["work_id"], state)


def test_pending_changes_keep_unrendered_updates_without_releasing_other_subjects():
    state = {"elements": [{"element_id": key, "field": field, "target": target}
        for key, field, target in [("coat-a", "character_context.clothing", "甲"),
                                  ("coat-b", "character_context.clothing", "乙"),
                                  ("face-a", "character_context.appearance", "甲")]]}
    red = {"field": "character_context.clothing", "quote": "甲换红衣", "element_ids": ["coat-a"]}
    blue = {**red, "quote": "甲改穿蓝衣"}
    pending = pending_changes(state, [red], [{"field": "shot_context.pose", "quote": "转回来",
                                             "element_ids": []}])
    assert red in pending
    assert {item["element_id"] for item in protected_elements(state, pending)} == {
        "coat-b", "face-a"}
    pending = pending_changes(state, pending, [blue])
    assert blue in pending and red not in pending
    new_person = {"field": "character_context.appearance", "quote": "加入丙", "element_ids": []}
    assert {item["element_id"] for item in protected_elements(state, [new_person])} == {
        "coat-a", "coat-b", "face-a"}


def test_authorized_outfit_change_that_did_not_match_request_stays_pending(story_app):
    app, calls = story_app, []
    parent = work(app)
    first, second = source(app, parent, 1), source(app, parent, 2)
    store = VisualContinuityStore(app.comic_media)
    original = store.observe(parent["work_id"], first, model_call=model([
        observed("甲", "character_context.clothing", "棕色衣服")], calls))
    identifier = original["elements"][0]["element_id"]
    change = {"field": "character_context.clothing", "quote": "甲改穿红衣",
              "element_ids": [identifier]}
    wrong = observed("甲", change["field"], "蓝色衣服", prior=identifier,
                     quote=change["quote"], comparison="changed")
    wrong["change_satisfied"] = False
    result = store.observe(parent["work_id"], second, inherited=original, changes=[change],
                           model_call=model([wrong], calls))
    assert result["elements"] == original["elements"]
    assert result["unresolved_changes"] == [change]
    assert result["findings"][0]["reason"] == "本次修改尚未被图像证实"


@pytest.mark.parametrize("target_format_error,object_contract", [
    (False, False), (True, False), (False, True)])
def test_three_real_work_turns_export_face_from_first_and_outfit_from_second(
    story_app, monkeypatch, target_format_error, object_contract,
):
    from test_comic_director_quality import _responses
    from test_comic_prompt_production import _sections

    from kantoku.shells import web_studio

    app, parent = story_app, work(story_app)
    state = {"sources": [], "text": "", "changes": [], "task_id": None, "round": 0}
    observed_inputs, target_calls = [], []
    monkeypatch.setattr(web_studio, "normalize_constraints", lambda *_a, **_kw: ([], {}))
    monkeypatch.setattr(app, "_conversation_context_model", lambda _: json.dumps({
        "action": "create", "relation": "continue" if state["sources"] else "new",
        "goal": state["text"], "sources": state["sources"]}))
    monkeypatch.setattr(app.comic_director, "stage_executor",
                        lambda skill, *_a: _responses()[skill])
    monkeypatch.setattr(app, "_comic_director_model", lambda _: json.dumps({
        "public_summary": "Offline validation", "confidence": .9,
        "findings": [], "suggested_patches": []}))
    monkeypatch.setattr(app, "_comic_storyboard_model", lambda _: json.dumps({
        "title": "Offline frame", "description": "Offline", "shots": [{
            "purpose": "继续当前画面", "subject": "同一人物", "action": state["text"],
            "environment": "木桌和蓝杯", "shot_size": "中景", "camera_angle": "平视"}]}))

    def text_model(messages):
        data = json.loads(messages[1]["content"])
        if data.get("stage") == "visual_shot_plan":
            entity = next(item["entity_id"] for item in data["elements"]
                          if item["attribute"] == "clothing")
            return json.dumps({"required_elements": [item["element_id"] for item in data[
                "elements"]], "visual_changes": [{"entity_id": entity, "target": "人物",
                    "attributes": ["clothing"], "operation": "change", "quote": state["text"]}]
                    if state["round"] == 2 else []})
        if data.get("stage") == "confirmed_image_continuity":
            return json.dumps({"candidate_id": next(key for key, candidate in data[
                "candidates"].items() if candidate["source_task_id"] == state["task_id"]),
                "changes": [{"field": field, "quote": state["text"]}
                            for field in state["changes"]]})
        if data.get("stage") == "visual_change_targets":
            target_calls.append(data)
            output = {"element_ids": [[item["element_id"] for item in data["elements"]
                if item["field"] == change["field"]] for change in data["changes"]]}
            if target_format_error and len(target_calls) == 1:
                output["private_reasoning"] = "PRIVATE_RESPONSE_MUST_NOT_LEAVE"
            assert "PRIVATE_RESPONSE_MUST_NOT_LEAVE" not in json.dumps(messages)
            return json.dumps(output)
        if "constraints" in data and "sections" in data:
            return json.dumps({"complete": True, "scope_valid": True, "findings": []})
        sections = _sections()
        sections["character_context"].update(appearance="同一原面容", clothing=(
            "棕色衣服" if state["round"] == 1 else "红色衣服"))
        sections["shot_context"]["action"] = state["text"]
        return json.dumps(sections)

    def vision_model(messages):
        context = json.loads(messages[1]["content"][0]["text"])
        observed_inputs.append(context)
        if not context["established_elements"]:
            rows = [observed("人物", "character_context.appearance", "同一原面容"),
                    observed("人物", "character_context.clothing", "棕色衣服"),
                    observed("蓝杯", "world_context.environment", "木桌上的蓝色杯子")]
        else:
            rows = [observed(item["target"], item["field"], "红色衣服" if item[
                "field"].endswith("clothing") else item["value"], prior=item["element_id"],
                visible="occluded" if item["field"].endswith("appearance") else "visible",
                comparison="changed" if item["field"].endswith("clothing") else "consistent",
                quote=context["authorized_changes"][0]["quote"]
                    if item["field"].endswith("clothing") else None)
                for item in context["established_elements"]]
        if object_contract:
            old = {item["element_id"]: item for item in context["established_elements"]}
            rows = [{**row, "entity_key": row["target"],
                "entity_id": old.get(row["element_id"], {}).get("entity_id"),
                "attribute": {"character_context.appearance": "face",
                    "character_context.clothing": "clothing"}.get(row["field"], "shape"),
                "kind": "subject" if row["target"] == "人物" else "prop"} for row in rows]
        return json.dumps({"elements": rows, "limitations": [],
            **({"contract": "visual-elements-2"} if object_contract else {})})

    monkeypatch.setattr(app, "_comic_constraint_model", text_model)
    monkeypatch.setattr(app, "_comic_image_vision_model", vision_model)
    adopted, prompts = [], []
    for number, text in enumerate(["人物穿棕衣，站在木桌和蓝杯旁",
        "同一人物转过身，衣服换成红色", "同一人物转回来，拿起原来的蓝杯"], 1):
        state.update(round=number, text=text, changes=[] if number == 1 else
                     ["character_context.clothing", "shot_context.pose"] if number == 2 else
                     ["shot_context.pose", "shot_context.action"])
        turn_request = {"request_id": uuid4().hex,
            "expected_project_version": app.comic_workspace.get(parent["work_id"])["version"],
            "conversation_id": parent["conversation_id"], "creation_mode": "professional",
            "independent_plan": True, "task": text}
        result = app.comic_work_turn(parent["work_id"], turn_request)
        call_counts = len(observed_inputs), len(target_calls)
        assert app.comic_work_turn(parent["work_id"], turn_request) == result
        assert (len(observed_inputs), len(target_calls)) == call_counts
        pid, spec = result["task_id"], result["director_spec"]
        app.confirm_comic_director(pid, {"version": spec["version"],
            "expected_project_version": app.comic_projects.get(pid).project.current_version})
        board = app.create_comic_storyboard(pid, {"generate": True,
            "expected_project_version": app.comic_projects.get(pid).project.current_version})
        shot = board["shots"][0]
        prompt = app.compile_comic_prompt(shot["shot_id"], {"complete_prompt": True,
            "image_mode": "external", "conversation_id": parent["conversation_id"],
            "expected_shot_version": shot["version"], "expected_project_version":
                app.comic_projects.get(pid).project.current_version})
        prompts.append(prompt)
        if number < 3:
            uploaded = app.import_comic_external_image(shot["shot_id"], {
                "expected_project_version": app.comic_projects.get(pid).project.current_version,
                "expected_shot_version": shot["version"],
                "expected_prompt_version": prompt["version"],
                "conversation_id": parent["conversation_id"], "filename": f"offline-{number}.png",
                "data_url": "data:image/png;base64," + base64.b64encode(_png(number)).decode()})
            image = app.comic_media.get(uploaded["artifact_id"]).model_dump(mode="json")
            _select(app, parent["conversation_id"], image)
            adopted.append(image)
        state.update(task_id=pid, sources=[{"kind": "run", "id": result["run_id"],
            "version": spec["version"], "fields": []}])
    final = prompts[-1]
    assert len(observed_inputs) == 2
    assert len(target_calls) == (0 if object_contract else 2 if target_format_error else 1)
    refs = final["reference_attachments"]
    assert {item["artifact_id"] for item in refs} == {item["artifact_id"] for item in adopted}
    state_snapshot = final["context_sources"]["image_references"]["continuity"]["visual_state"]
    by_field = {item["field"]: item for item in state_snapshot["elements"]}
    assert by_field["character_context.appearance"]["source"]["image"] == adopted[0]
    assert by_field["character_context.clothing"]["source"]["image"] == adopted[1]
    exported = app.export_comic_codex_task(final["shot_id"], {"request_id": "three-turn-export",
        "conversation_id": parent["conversation_id"], "expected_prompt_version": final["version"],
        "expected_shot_version": final["shot_version"], "expected_project_version":
            app.comic_projects.get(final["project_id"]).project.current_version})
    manifest = json.loads(Path(exported["task_path"]).read_text(encoding="utf-8"))
    assert {Path(item["path"]).read_bytes() for item in manifest["references"]} == {
        _png(1), _png(2)}
    assert "同一原面容" in manifest["prompt"] and "红色衣服" in manifest["prompt"]
    uploaded = app.import_comic_external_image(final["shot_id"], {
        "expected_project_version": app.comic_projects.get(
            final["project_id"]).project.current_version,
        "expected_shot_version": final["shot_version"],
        "expected_prompt_version": final["version"], "conversation_id": parent["conversation_id"],
        "filename": "offline-third.png",
        "data_url": "data:image/png;base64," + base64.b64encode(_png(3)).decode()})
    target = app.comic_media.get(uploaded["artifact_id"])
    comparisons = []

    def compare(messages):
        parts = messages[1]["content"]
        basis = json.loads(parts[0]["text"])
        assert len(basis["references"]["established_elements"]) == 3
        pixels = [base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
                  for part in parts if part["type"] == "image_url"]
        assert pixels == [_png(3), _png(1), _png(2)]
        comparisons.append(basis)
        return json.dumps({"summary": "Offline drift observation", "findings": [{
            "finding_id": "face-drift", "observation": "与原面容不一致",
            "suggested_change": "按第一张图恢复面容", "confidence": .9,
            "uncertainty": "Offline substitute"}], "protected_features": [], "limitations": []})

    monkeypatch.setattr(app, "_comic_image_vision_model", compare)
    report = app.comic_image_operation(final["project_id"], "analysis", {
        "request_id": "compare-returned-third", "conversation_id": parent["conversation_id"],
        "expected_project_version": app.comic_projects.get(
            final["project_id"]).project.current_version,
        "image": {"image_id": target.image_id, "version": target.version}, "task": state["text"]})
    assert len(comparisons) == 1
    assert report["advisory"] is True
    assert report["output"]["findings"][0]["finding_id"] == "face-drift"
    assert app.comic_projects.get_director(final["project_id"]).reference_context[
        "continuity"]["visual_state"] == state_snapshot
    if object_contract:
        face = next(item for item in state_snapshot["elements"] if item["attribute"] == "face")
        accepted_quote = "明确接受主角在当前图中的新面容"
        with pytest.raises(ToolError, match="具体对象"):
            app.select_comic_image(target.image_id, {
                "request_id": "foreign-extra-face", "conversation_id": parent["conversation_id"],
                "expected_project_version": app.comic_projects.get(
                    final["project_id"]).project.current_version,
                "version": target.version, "expected_selection_version": 0,
                "accepted_visual_changes": [{"entity_id": "other-work-object", "target": "人物",
                    "attributes": ["face"], "operation": "change", "quote": accepted_quote}]})
        selection = app.select_comic_image(target.image_id, {
            "request_id": "explicit-extra-face", "conversation_id": parent["conversation_id"],
            "expected_project_version": app.comic_projects.get(
                final["project_id"]).project.current_version,
            "version": target.version, "expected_selection_version": 0,
            "accepted_visual_changes": [{"entity_id": face["entity_id"], "target": "人物",
                "attributes": ["face"], "operation": "change", "quote": accepted_quote}]})
        rows = [object_observation(item["target"], item["attribute"],
            "已明确接受的新面容" if item["attribute"] == "face" else item["value"],
            entity=item["entity_id"], prior=item["element_id"],
            kind=item["kind"], field=item["field"],
            quote=accepted_quote if item["attribute"] == "face" else None,
            satisfied=True if item["attribute"] == "face" else None)
            for item in state_snapshot["elements"]]
        accepted = app.comic_workspace.visual.observe(parent["work_id"], {
            "image": target.model_dump(mode="json"),
            "selection_version": selection["selection_version"]},
            inherited=state_snapshot, model_call=object_model(rows))
        assert next(item for item in accepted["elements"] if item["attribute"] == "face")[
            "source"]["image"]["artifact_id"] == target.artifact_id
        assert next(item for item in accepted["elements"] if item["attribute"] == "clothing")[
            "source"]["image"] == adopted[1]
