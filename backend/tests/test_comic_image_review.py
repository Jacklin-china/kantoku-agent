"""少量契约回归：实际媒体/HTTP/编译输入，模型全部为明确离线替身。"""

import base64
import hashlib
import json
import struct
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageOps
from pydantic import ValidationError

from kantoku.config import LLMError, ToolError, get_settings
from kantoku.core import llm
from kantoku.core.runtime.models import ArtifactLifecycle, ArtifactType
from kantoku.domains.comic.director_provenance import value_hash
from kantoku.domains.comic.models import ImageEditScope, ImageVersionRef
from kantoku.shells import web_studio
from kantoku.tools.image_gen import LocalFakeImageProvider


def png(color=0):
    def chunk(name, data):
        return (struct.pack(">I", len(data)) + name + data
                + struct.pack(">I", zlib.crc32(name + data)))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes([0, color, 0, 0]))) + chunk(b"IEND", b""))


@pytest.fixture
def review_app(monkeypatch, tmp_path):
    settings = get_settings().model_copy(deep=True)
    settings.storage.sqlite_path = tmp_path / "review.db"
    settings.image.output_dir = tmp_path / "images"
    monkeypatch.setattr(web_studio, "get_settings", lambda: settings)
    # Match the production topology: FileCatalog and domain writes share one DB.
    from kantoku.core.runtime import storage
    monkeypatch.setattr(storage, "get_settings", lambda: settings)
    monkeypatch.setattr(web_studio, "_provider", lambda: LocalFakeImageProvider(
        tmp_path / "fake", model_id="offline-test", actual_fen=0))
    def forbidden(*_args, **_kwargs):
        pytest.fail("离线验收禁止任何未替换的付费调用")
    monkeypatch.setattr(web_studio, "vision_chat", forbidden)
    monkeypatch.setattr(web_studio, "chat", forbidden)
    application = web_studio.StudioApplication()
    monkeypatch.setattr(application, "_comic_image_vision_model", forbidden)
    monkeypatch.setattr(application, "_comic_constraint_model", forbidden)
    try:
        yield application
    finally:
        application.runner.close()


def setup_image(app, *, constraints=None):
    cid = app.create_conversation({"interaction_mode": "guided", "domain": "comic"})["id"]
    pid = app.create_comic_project({"title": "QA 素材", "brief": {
        "original_request": "制作独立画面", "hard_constraints": constraints or []}})["project"][
            "project_id"]
    image = upload(app, pid, cid)
    return pid, cid, {"image_id": image["image_id"], "version": 1}


def common(app, pid, cid, request_id):
    return {"conversation_id": cid, "request_id": request_id,
            "expected_project_version": app.comic_projects.get(pid).project.current_version}


def upload(app, pid, cid, *, color=0, **fields):
    return app.import_comic_image(pid, {**common(app, pid, cid, f"upload-{color}"),
        "filename": "offline-fixture.png", "data_url": "data:image/png;base64,"
        + base64.b64encode(png(color)).decode(), **fields})


def observation(topic="主体"):
    return {"summary": f"离线观察 {topic}", "findings": [
        {"finding_id": "selected", "observation": f"{topic}边缘模糊",
         "suggested_change": f"调整{topic}边缘", "confidence": 0.7,
         "uncertainty": "小尺寸限制", "region": [0.1, 0.1, 0.8, 0.8]},
        {"finding_id": "unselected", "observation": "未选观察",
         "suggested_change": "换成绿色天空", "confidence": 0.5}],
        "protected_features": [f"保留{topic}轮廓"], "limitations": ["离线替身不证明视觉质量"]}


@pytest.mark.parametrize("orientation", range(1, 9))
def test_detail_crop_uses_display_coordinates_and_records_original_mapping(orientation):
    from kantoku.capabilities.image import crop_vision_image

    with Image.new("RGB", (6, 4)) as original, BytesIO() as buffer:
        original.putdata([(x * 30, y * 50, 10) for y in range(4) for x in range(6)])
        exif = Image.Exif()
        exif[274] = orientation
        original.save(buffer, format="PNG", exif=exif)
        raw = buffer.getvalue()
    crop = crop_vision_image(raw, "image/png", [.25, 0., .75, 1.])
    with Image.open(BytesIO(raw)) as source, ImageOps.exif_transpose(source) as display:
        box = crop.metadata["display_pixel_box"]
        with display.crop(tuple(box)) as expected, Image.open(BytesIO(crop.data)) as actual:
            assert actual.size == expected.size and actual.tobytes() == expected.tobytes()
            a, b, c, d, e, f = crop.metadata["display_to_source"]
            for y in range(actual.height):
                for x in range(actual.width):
                    dx, dy = box[0] + x + .5, box[1] + y + .5
                    assert actual.getpixel((x, y)) == source.getpixel((
                        int(a * dx + b * dy + c), int(d * dx + e * dy + f)))
    assert crop.metadata["source_sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(crop.metadata["display_to_source"]) == 6
    assert crop.metadata["orientation"] == orientation and not crop.metadata["upscaled"]


@pytest.mark.parametrize("mode,format,mime", [
    ("CMYK", "JPEG", "image/jpeg"), ("RGBA", "PNG", "image/png"),
])
def test_detail_crop_accepts_print_colors_and_keeps_transparency(mode, format, mime):
    from kantoku.capabilities.image import crop_vision_image

    with Image.new(mode, (10, 8), (20, 30, 40, 50)) as source, BytesIO() as buffer:
        source.save(buffer, format=format)
        crop = crop_vision_image(buffer.getvalue(), mime, [0., 0., .1, .125])
    with Image.open(BytesIO(crop.data)) as result:
        assert result.size == (1, 1) and result.mode == ("RGBA" if mode == "RGBA" else "RGB")
        if mode == "RGBA":
            assert result.getpixel((0, 0))[3] == 50


def test_analysis_has_one_batched_detail_review_and_conservative_suggestions(
    review_app, monkeypatch,
):
    app = review_app
    pid, cid, image = setup_image(app)
    calls = []
    output = observation()
    output["findings"][0].update(category="clear_deviation", evidence=[], risk_level="low")
    output["findings"][1].update(category="optional_improvement")
    output["recommendation"] = "consider_edit"
    output["detail_regions"] = [{"bounds": [.1, .1, .9, .9], "reason": "关键细部不确定"}]
    def vision(messages):
        calls.append(messages)
        return json.dumps(output)
    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    result = app.comic_image_operation(pid, "analysis", {
        **common(app, pid, cid, "detail-analysis"), "image": image, "task": "检查原图"})
    assert len(calls) == 2
    assert len([part for part in calls[1][1]["content"] if part["type"] == "image_url"]) == 2
    assert result["analysis_policy"] == "evidence-review-2"
    assert result["output"]["recommendation"] == "retain"
    assert result["output"]["findings"][0]["category"] == "uncertain"
    assert result["output"]["findings"][1]["category"] == "optional_improvement"
    assert result["output"]["detail_regions"] == []
    assert result["detail_review"]["crops"][0]["source_sha256"] == app.comic_media.get(
        image["image_id"]).sha256
    assert len(app.comic_media.versions(image["image_id"])) == 1


@pytest.mark.parametrize("outcome", [
    "accept", "reject", "stale", "tampered", "override", "overlap", "merge",
])
def test_unified_edit_plan_binds_scope_confirmation_and_resolves_manual_priority(
    review_app, monkeypatch, outcome,
):
    app = review_app
    pid, cid, image = setup_image(app)
    report, _, _ = analyze(app, pid, cid, image, monkeypatch)
    plan_calls = []
    bounds = ([.05, .05, .95, .95] if outcome in {"accept", "reject", "stale", "tampered", "merge"}
              else [.2, .2, .7, .7])
    def vision(messages):
        payload = json.loads(messages[1]["content"][0]["text"])
        if payload.get("stage") == "constraint_extraction_review":
            return app._comic_constraint_model([messages[0],
                {"role": "user", "content": json.dumps(payload)}])
        assert payload["stage"] == "image_edit_plan"
        plan_calls.append(payload)
        return json.dumps({"items": [
            {"item_id": "ai", "operation": "modify", "requirement_ids": ["finding:selected"],
             "object_id": "subject",
             "target": "主体", "attribute": "color" if outcome in {"override", "merge"} else "edge",
             "desired_state": "红色" if outcome == "merge" else
                "蓝色" if outcome == "override" else "清晰边缘",
             "edit_bounds": bounds, "scope_reason": "需连接定位框外的部分",
             "within_scope_alternative": "原框内只能修整局部"},
            {"item_id": "manual", "operation": "modify", "requirement_ids": ["user-instruction"],
             "object_id": "subject",
             "target": "主体", "attribute": "color", "desired_state": "红色",
             "edit_bounds": [.2, .2, .7, .7]}], "questions": []})
    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    result, request, seen = edit(app, pid, cid, image, report, monkeypatch,
        request_fields={"instruction": "把主体改为红色", "edit_scope": {
            "mode": "regions", "region_semantics": "locator", "regions": [{
                "region_id": "locate", "bounds": [.1, .1, .8, .8],
                "finding_id": "selected", "instruction": ""}],
            "instruction_target": {"mode": "selected_regions"}, "whole_finding_ids": []}})
    assert len(plan_calls) == 1
    if outcome in {"accept", "reject", "stale", "tampered"}:
        assert result["edit_status"] == "needs_scope_confirmation"
        assert "artifact_id" not in result
        run = app.runtime_store.get_run(result["run_id"])
        assert run.status.value == "waiting"
        # Replaying the completed business response must not call the plan model again.
        assert app.comic_image_operation(pid, "edit", request)["run_id"] == result["run_id"]
        confirm = {**request, **common(app, pid, cid, "scope-decision"),
            "plan_confirmation": {"run_id": result["run_id"],
                "fingerprint": result["plan_fingerprint"],
                "accepted_item_ids": ["ai"] if outcome != "reject" else []}}
        if outcome == "stale":
            confirm["instruction"] = "把另一个物体改为红色"
        if outcome == "tampered":
            changed = {**run.state}
            changed["image_edit_plan"]["plan"]["proposal"]["items"][0][
                "edit_bounds"] = [0., 0., 1., 1.]
            app.runtime_store.update_run(run.id, status=run.status,
                current_node=run.current_node, state=changed)
        result = app.comic_image_operation(pid, "edit", confirm)
        assert len(plan_calls) == 1
        if outcome in {"reject", "stale", "tampered"}:
            assert result["edit_status"] == ("needs_clarification" if outcome == "reject"
                                              else "review_unavailable")
            assert "artifact_id" not in result
            return
    assert result["edit_status"] == "ready"
    scope = result["scope"]
    assert scope["locator_scope"]["regions"][0]["bounds"] == [.1, .1, .8, .8]
    assert "proposal" not in scope["unified_plan"]
    if outcome == "override":
        assert scope["selected_findings"] == []
        assert scope["unified_plan"]["handling"][0]["action"] == "manual_override"
        assert {item["requirement_id"] for item in scope["authorized_requirements"]} == {
            "user-instruction"}
    elif outcome == "merge":
        assert len(scope["unified_plan"]["items"]) == 1
        assert scope["unified_plan"]["items"][0]["edit_bounds"] == [.2, .2, .7, .7]
        assert scope["unified_plan"]["handling"][0]["action"] == "merged"
        assert len(scope["unified_plan"]["items"][0]["requirement_ids"]) == 2
    else:
        assert len(scope["unified_plan"]["items"]) == 2
        assert scope["edit_scope"]["regions"][0]["bounds"] == bounds
    compiler = next(item for item in seen if isinstance(item, dict) and item.get("mode") == "edit")
    assert len(compiler["authorized_requirements"]) == (1 if outcome == "override" else 2)


@pytest.mark.parametrize("locator", [[.01, .02, .99, .99], [0., 0., 1., 1.]])
def test_global_edit_confirms_once_and_preservation_never_expands_the_edit(
    review_app, monkeypatch, locator,
):
    app = review_app
    pid, cid, image = setup_image(app)
    report, _, _ = analyze(app, pid, cid, image, monkeypatch)
    plans = []
    def vision(messages):
        payload = json.loads(messages[1]["content"][0]["text"])
        if payload.get("stage") == "constraint_extraction_review":
            return app._comic_constraint_model([messages[0],
                {"role": "user", "content": json.dumps(payload)}])
        plans.append(payload)
        return json.dumps({"items": [
            {"item_id": "lighting", "requirement_ids": ["region:manual"],
             "object_id": "scene", "target": "整图亮度", "attribute": "lighting",
             "desired_state": "降低亮度", "operation": "modify", "edit_bounds": None},
            {"item_id": "structure", "requirement_ids": ["region:manual"],
             "object_id": "subject", "target": "主体结构", "attribute": "structure",
             "desired_state": "保持原状", "operation": "preserve",
             "edit_bounds": [0., 0., .5, .5]},
            {"item_id": "color", "requirement_ids": ["region:manual"],
             "object_id": "subject", "target": "主体颜色", "attribute": "color",
             "desired_state": "保留颜色", "operation": "preserve", "edit_bounds": None}]})
    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    result, request, compiled = edit(app, pid, cid, image, report, monkeypatch,
        request_fields={"selected_finding_ids": [], "edit_scope": {"mode": "regions",
            "region_semantics": "locator", "regions": [{"region_id": "manual",
                "bounds": locator, "instruction": "整图暗一点，主体结构和颜色保持不变"}]}})
    if locator[0] != 0.:
        assert result["edit_status"] == "needs_scope_confirmation"
        assert result["edit_plan"]["questions"] == []
        assert [item["item_id"] for item in result["edit_plan"]["scope_proposals"]] == ["lighting"]
        assert result["edit_plan"]["scope_proposals"][0]["bounds"] == [0., 0., 1., 1.]
        assert compiled == [] and "artifact_id" not in result
        confirm = {**request, **common(app, pid, cid, "accept-global"),
            "plan_confirmation": {"run_id": result["run_id"],
                "fingerprint": result["plan_fingerprint"], "accepted_item_ids": ["lighting"]}}
        result = app.comic_image_operation(pid, "edit", confirm)
        assert app.comic_image_operation(pid, "edit", confirm) == result
    assert result["edit_status"] == "ready" and len(plans) == 1
    assert result["scope"]["edit_scope"]["mode"] == "whole"
    assert result["scope"]["edit_scope"]["regions"] == []
    assert result["scope"]["authorized_requirements"][0]["target"] == {
        "mode": "whole", "region_ids": []}
    assert {item["operation"] for item in result["scope"]["unified_plan"]["items"]} == {
        "modify", "preserve"}
    assert app.runtime_store.get_artifact(result["artifact_id"]).type == ArtifactType.PROMPT
    assert result["correction"] == {"used": False, "stage": None}


def test_preservation_alone_creates_no_edit_regions_or_scope_proposals(review_app, monkeypatch):
    app = review_app
    pid, cid, image = setup_image(app)
    report, _, _ = analyze(app, pid, cid, image, monkeypatch)
    def vision(messages):
        payload = json.loads(messages[1]["content"][0]["text"])
        if payload.get("stage") == "constraint_extraction_review":
            return app._comic_constraint_model([messages[0],
                {"role": "user", "content": json.dumps(payload)}])
        return json.dumps({"items": [{"item_id": "preserve", "requirement_ids": ["region:manual"],
            "object_id": "subject", "target": "主体", "attribute": "shape",
            "desired_state": "保持原状", "operation": "preserve", "edit_bounds": None}]})
    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    result, _, _ = edit(app, pid, cid, image, report, monkeypatch, request_fields={
        "selected_finding_ids": [], "edit_scope": {"mode": "regions",
            "region_semantics": "locator", "regions": [{"region_id": "manual",
                "bounds": [.1, .1, .8, .8], "instruction": "主体保持原状"}]}})
    assert result["edit_status"] == "ready"
    assert result["scope"]["unified_plan"]["scope_proposals"] == []
    assert result["scope"]["edit_scope"]["regions"] == []
    assert result["scope"]["authorized_requirements"][0]["operation"] == "preserve"
    assert result["scope"]["authorized_requirements"][0]["target"]["region_ids"] == ["manual"]


def test_mutually_exclusive_manual_goals_wait_without_discarding_other_requirements(
    review_app, monkeypatch,
):
    app = review_app
    pid, cid, image = setup_image(app)
    calls = []
    def vision(messages):
        calls.append(messages)
        return json.dumps({"items": [
            {"item_id": "red", "operation": "modify", "requirement_ids": ["region:manual"],
             "object_id": "coat",
             "target": "外套", "attribute": "color", "desired_state": "红色", "edit_bounds": None},
            {"item_id": "blue", "operation": "modify", "requirement_ids": ["user-instruction"],
             "object_id": "coat",
             "target": "外套", "attribute": "color", "desired_state": "蓝色",
             "edit_bounds": None}]})
    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    result = app.comic_image_operation(pid, "edit", {
        **common(app, pid, cid, "manual-conflict"), "image": image, "task": "检查外套",
        "instruction": "外套必须是蓝色", "selected_finding_ids": [],
        "edit_scope": {"mode": "mixed", "region_semantics": "locator", "regions": [{
            "region_id": "manual", "bounds": [.1, .1, .8, .8], "instruction": "外套改为红色"}],
            "instruction_target": {"mode": "whole"}, "whole_finding_ids": []}})
    assert result["edit_status"] == "needs_clarification" and len(calls) == 1
    assert "artifact_id" not in result
    assert result["edit_plan"]["questions"][0]["requirement_ids"] == [
        "region:manual", "user-instruction"]
    assert len(app.runtime_store.get_run(result["run_id"]).state["media_input"][
        "edit_scope"]["regions"]) == 1


def analyze(app, pid, cid, image, monkeypatch, task="检查主体", output=None, request_id="analyze"):
    seen = []
    def model(messages):
        context = json.loads(messages[-1]["content"][0]["text"])
        if context.get("stage") == "constraint_extraction_review":
            return app._comic_constraint_model([messages[0],
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)}])
        seen.append(messages)
        return json.dumps(output if output is not None else observation(), ensure_ascii=False)
    monkeypatch.setattr(app, "_comic_image_vision_model", model)
    request = {**common(app, pid, cid, request_id), "image": image, "task": task}
    return app.comic_image_operation(pid, "analysis", request), request, seen


def edit(app, pid, cid, image, report, monkeypatch, *, task="检查主体", approved=True,
         request_id="edit", request_fields=None):
    seen = []
    def model(messages):
        payload = json.loads(messages[-1]["content"])
        seen.append(payload)
        if isinstance(payload, list):
            return json.dumps({"constraints": [{"source_index": index,
                "source_quote": item["quote"], "target": "本轮授权内容", "scope": "output"}
                for index, item in enumerate(payload)]}, ensure_ascii=False)
        if payload.get("stage") == "constraint_extraction_review":
            return json.dumps({"complete": True, "scope_valid": approved,
                "constraint_replacements": [], "findings": [
                {"constraint_id": item["constraint_id"], "status": "covered",
                 "evidence_path": f"constraints.{item['constraint_id']}.source_quote"}
                for item in payload["constraints"]]})
        if "sections" in payload:
            return json.dumps({"complete": True, "scope_valid": approved, "findings": [
                {"constraint_id": item["constraint_id"], "status": "covered",
                 "evidence_path": "sections.positive_prompt"} for item in payload["constraints"]],
                "edit_findings": [{"constraint_id": item["requirement_id"], "status": "covered",
                    "evidence_path": "sections.positive_prompt"}
                    for item in payload.get("authorized_requirements", [])]})
        return json.dumps({"positive_prompt": "只修改选中的边缘，保留其余轮廓与当前约束。",
                           "negative_prompt": "", "summary": "离线修改指令"}, ensure_ascii=False)
    monkeypatch.setattr(app, "_comic_constraint_model", model)
    request = {**common(app, pid, cid, request_id), "image": image, "task": task,
               "analysis_artifact_id": report["artifact_id"],
               "selected_finding_ids": ["selected"], **(request_fields or {})}
    return app.comic_image_operation(pid, "edit", request), request, seen


def test_legacy_shot_upload_is_read_only_and_retains_original_when_editing(review_app, monkeypatch):
    app = review_app
    pid, cid, imported = setup_image(app)
    original = app.comic_media.get(imported["image_id"])
    artifact = app.runtime_store.get_artifact(original.artifact_id).model_copy(update={
        "id": "artifact-legacy-shot", "source": "comic.external_image",
        "metadata": {"origin": "real", "creation_mode": "external_upload", "project_id": pid,
                     "shot_id": "legacy-shot", "prompt_artifact_id": "legacy-prompt",
                     "sha256": original.sha256, "filename": "original.png"}})
    app.runtime_store.save_artifact(artifact)
    image = {"image_id": artifact.id, "version": 1}
    project_version = app.comic_projects.get(pid).project.current_version
    before = app.comic_media.list(pid)
    projected = app.comic_media.require_image(
        ImageVersionRef.model_validate(image), pid, cid)
    assert projected.artifact_id == artifact.id and projected.sha256 == original.sha256
    assert app.comic_media.list(pid) == before
    assert app.comic_projects.get(pid).project.current_version == project_version
    assert app.comic_media.versions(artifact.id) == [projected]
    report, _, seen = analyze(app, pid, cid, image, monkeypatch)
    assert seen[0][1]["content"][2]["image_url"]["url"].endswith(base64.b64encode(png()).decode())
    instruction, _, _ = edit(app, pid, cid, image, report, monkeypatch)
    app.comic_image_operation(pid, "confirm", {**common(app, pid, cid, "legacy-confirm"),
        "instruction_artifact_id": instruction["artifact_id"]})
    newer = upload(app, pid, cid, color=21, image_id=artifact.id, expected_image_version=1,
                   parent=image, instruction_artifact_id=instruction["artifact_id"])
    assert newer["version"] == 2 and newer["artifact_id"] != artifact.id
    assert [item.version for item in app.comic_media.versions(artifact.id)] == [2, 1]
    assert app.runtime_store.get_artifact(artifact.id) == artifact
    other = app.create_conversation({"interaction_mode": "guided", "domain": "comic"})["id"]
    with pytest.raises(ToolError, match="范围"):
        app.comic_media.require_image(ImageVersionRef.model_validate(image), pid, other)
    fake = artifact.model_copy(update={"id": "artifact-not-real", "metadata": {
        **artifact.metadata, "origin": "mock"}})
    app.runtime_store.save_artifact(fake)
    with pytest.raises(ToolError, match="找不到指定图片版本"):
        app.comic_media.get(fake.id)


@pytest.mark.parametrize("topic", ["机械鱼", "山间拱桥"])
def test_full_loop_selected_scope_actual_images_and_manual_selection(
    review_app, monkeypatch, topic,
):
    app = review_app
    pid, cid, image = setup_image(app)
    report, request, seen = analyze(app, pid, cid, image, monkeypatch,
                                    task=f"检查{topic}", output=observation(topic))
    assert seen[0][1]["content"][2]["image_url"]["url"].endswith(base64.b64encode(png()).decode())
    result, _, calls = edit(app, pid, cid, image, report, monkeypatch, task=request["task"])
    assert calls[0]["selected_findings"][0]["finding_id"] == "selected"
    assert "unselected" not in json.dumps(calls)
    assert "绿色天空" not in json.dumps(calls, ensure_ascii=False)
    assert app.runtime_store.get_artifact(result["artifact_id"]).type == ArtifactType.PROMPT
    app.comic_image_operation(pid, "confirm", {**common(app, pid, cid, "confirm"),
                             "instruction_artifact_id": result["artifact_id"]})
    restored = app.comic_image_reports(pid, cid, "edit", result["artifact_id"])
    assert restored["requires_confirmation"] is False and restored["confirmation"]["confirmed"]
    newer = upload(app, pid, cid, color=20, image_id=image["image_id"], expected_image_version=1,
                   parent=image, instruction_artifact_id=result["artifact_id"])
    assert newer["version"] == 2
    assert app.comic_media.list(pid)[0]["selection"] is None
    compared = []
    def model(messages):
        compared.append(messages)
        payload = json.loads(messages[1]["content"][0]["text"])
        return json.dumps({"summary": "两图公开对比", "changes": [{
            "check_id": item["check_id"], "status": "uncertain", "evidence": "尺寸过小",
            "confidence": 0.2} for item in payload["changes"]], "preservation": [{
            "check_id": item["check_id"], "status": "satisfied", "evidence": "轮廓保持",
            "confidence": 0.5} for item in payload["preservation"]],
            "other_observations": [], "limitations": ["人工判断"]})
    monkeypatch.setattr(app, "_comic_image_vision_model", model)
    request = {**common(app, pid, cid, "compare"), "before": image,
               "after": {"image_id": image["image_id"], "version": 2},
               "instruction_artifact_id": result["artifact_id"]}
    comparison = app.comic_image_operation(pid, "comparison", request)
    urls = [part["image_url"]["url"] for part in compared[0][1]["content"]
            if part["type"] == "image_url"]
    assert len(urls) == 2 and urls[0] != urls[1]
    assert comparison["advisory"] is True
    assert app.comic_media.list(pid)[0]["selection"] is None
    app.select_comic_image(image["image_id"], {**common(app, pid, cid, "select-old"),
                          "version": 1, "expected_selection_version": 0})
    assert app.comic_media.list(pid)[0]["selection"]["selected_version"] == 1
    assert len(app.comic_media.versions(image["image_id"])) == 2


@pytest.mark.parametrize("problem", ["new_problem", "damage", "uncertain", "clean"])
def test_regression_check_can_recommend_old_version_without_adopting(
    review_app, monkeypatch, problem,
):
    app = review_app
    pid, cid, image = setup_image(app)
    report, _, _ = analyze(app, pid, cid, image, monkeypatch)
    instruction, _, _ = edit(app, pid, cid, image, report, monkeypatch)
    app.comic_image_operation(pid, "confirm", {**common(app, pid, cid, "regression-confirm"),
        "instruction_artifact_id": instruction["artifact_id"]})
    after = upload(app, pid, cid, color=35, image_id=image["image_id"],
        expected_image_version=1, parent=image,
        instruction_artifact_id=instruction["artifact_id"])
    def vision(messages):
        payload = json.loads(messages[1]["content"][0]["text"])
        return json.dumps({"summary": "离线退步检查", "recommendation": "consider_after",
            "changes": [{"check_id": item["check_id"], "status": "satisfied",
                "evidence": "离线证据", "confidence": .9} for item in payload["changes"]],
            "preservation": [{"check_id": item["check_id"], "status":
                "not_satisfied" if problem == "damage" else
                "uncertain" if problem == "uncertain" else "satisfied",
                "evidence": "离线证据", "confidence": .9} for item in payload["preservation"]],
            "new_problems": ["新增结构损伤"] if problem == "new_problem" else [],
            "other_observations": [], "limitations": ["替身不证明视觉质量"]})
    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    result = app.comic_image_operation(pid, "comparison", {
        **common(app, pid, cid, "regression"), "before": image,
        "after": {"image_id": after["image_id"], "version": after["version"]},
        "instruction_artifact_id": instruction["artifact_id"]})
    assert result["output"]["recommendation"] == (
        "retain_before" if problem in {"damage", "new_problem"} else
        "uncertain" if problem == "uncertain" else "consider_after")
    assert result["assessment"]["resolved"] and not result["assessment"]["unresolved"]
    assert app.comic_media.list(pid)[0]["selection"] is None
    assert len(app.comic_media.versions(image["image_id"])) == 2


def test_plan_and_normalization_share_the_same_correction_allowance(review_app, monkeypatch):
    app = review_app
    pid, cid, image = setup_image(app)
    plans, normalizations, reviews = [], [], []
    def vision(messages):
        payload = json.loads(messages[1]["content"][0]["text"])
        if payload.get("stage") == "constraint_extraction_review":
            reviews.append(payload)
            return "{}"
        plans.append(messages)
        if len(plans) == 1:
            return json.dumps({"items": []})  # Missing requirement is a system omission.
        return json.dumps({"items": [{"item_id": "lighting", "operation": "modify",
            "object_id": "background",
            "requirement_ids": ["user-instruction"], "target": "背景", "attribute": "light",
            "desired_state": "略暗", "edit_bounds": None}]})
    def text_model(messages):
        normalizations.append(messages)
        return json.dumps({"constraints": []})  # A second correction must be refused.
    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", text_model)
    result = app.comic_image_operation(pid, "edit", {**common(app, pid, cid, "plan-budget"),
        "image": image, "task": "调整光线", "instruction": "背景暗一点，其他不变",
        "edit_scope": {"mode": "whole", "region_semantics": "locator"}})
    assert len(plans) == 2 and len(normalizations) == 1 and len(reviews) == 1
    assert result["correction"] == {"used": True, "stage": "edit_plan"}
    assert result["edit_status"] == "review_unavailable" and "artifact_id" not in result


@pytest.mark.parametrize("problem,expected", [
    ("source", "unknown_requirement_id"), ("bounds", "list_type"),
    ("missing", "missing"), ("operation", "missing"),
    ("extra", "extra_forbidden"), ("json", "json_invalid"),
])
def test_edit_plan_repairs_specific_protocol_errors_then_compiles(
    review_app, monkeypatch, problem, expected,
):
    app = review_app
    pid, cid, image = setup_image(app)
    report, _, _ = analyze(app, pid, cid, image, monkeypatch)
    item = {"item_id": "lighting", "operation": "modify", "requirement_ids": ["region:manual"],
        "object_id": "background", "target": "背景", "attribute": "lighting",
        "desired_state": "光线略暗", "edit_bounds": [.2, .2, .7, .7]}
    invalid = dict(item)
    if problem == "source":
        invalid["requirement_ids"] = ["manual"]  # Region identity is not a requirement identity.
    elif problem == "bounds":
        invalid["edit_bounds"] = {"x": .2, "y": .2, "width": .5, "height": .5}
    elif problem == "missing":
        invalid.pop("desired_state")
    elif problem == "operation":
        invalid.pop("operation")
    elif problem == "extra":
        invalid["private_debug"] = "must never enter correction inputs"
    calls = []
    def vision(messages):
        payload = json.loads(messages[1]["content"][0]["text"])
        if payload.get("stage") == "constraint_extraction_review":
            return app._comic_constraint_model([messages[0],
                {"role": "user", "content": json.dumps(payload)}])
        calls.append(payload)
        schema = json.loads(messages[0]["content"].rsplit("只返回 JSON：", 1)[1])
        for name in ("ImageEditPlanItem", "ImageEditPlanIssue"):
            field = schema["$defs"][name]["properties"]["requirement_ids"]
            assert field["items"]["enum"] == ["region:manual"] and field["uniqueItems"]
        if len(calls) == 1:
            return "not JSON" if problem == "json" else json.dumps({"items": [invalid]})
        assert expected in {issue["type"] for issue in payload["repair"]["issues"]}
        assert payload["repair"]["missing_requirement_ids"] == ["region:manual"]
        assert payload["fixed_items"] == [] and payload["fixed_questions"] == []
        assert "must never enter correction inputs" not in json.dumps(messages)
        assert "private_debug" not in json.dumps(messages)
        return json.dumps({"items": [item]})
    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    result, request, _ = edit(app, pid, cid, image, report, monkeypatch, request_fields={
        "selected_finding_ids": [], "edit_scope": {"mode": "regions",
            "region_semantics": "locator", "regions": [{"region_id": "manual",
                "bounds": [.1, .1, .8, .8], "instruction": "画面有点亮，背景暗一点"}]}})
    assert result["edit_status"] == "ready" and len(calls) == 2
    assert result["correction"] == {"used": True, "stage": "edit_plan"}
    run = app.runtime_store.get_run(result["run_id"])
    audit = run.state["image_edit_plan_review"]
    assert len(audit["attempts"]) == 2 and audit["attempts"][1]["issues"] == []
    assert expected in {issue["type"] for issue in audit["attempts"][0]["issues"]}
    assert all(len(attempt["response_sha256"]) == 64 for attempt in audit["attempts"])
    assert run.state["media_input"]["edit_scope"] == ImageEditScope.model_validate(
        request["edit_scope"]).frozen_scope()
    assert result["scope"]["authorized_requirements"][0]["requirement"] == "画面有点亮，背景暗一点"


def test_edit_plan_protocol_failure_is_specific_bounded_and_replayable(review_app, monkeypatch):
    app = review_app
    pid, cid, image = setup_image(app)
    report, _, _ = analyze(app, pid, cid, image, monkeypatch)
    calls = []
    def vision(messages):
        calls.append(messages)
        return json.dumps({"items": [{"item_id": "broken", "private_debug": "secret"}]})
    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    result, request, compiled = edit(app, pid, cid, image, report, monkeypatch, request_fields={
        "selected_finding_ids": [], "edit_scope": {"mode": "regions",
            "region_semantics": "locator", "regions": [{"region_id": "manual",
                "bounds": [.1, .1, .8, .8], "instruction": "画面暗一点"}]}})
    assert len(calls) == 2 and compiled == []
    assert result["edit_status"] == "review_unavailable" and "artifact_id" not in result
    assert result["candidate"] is None and "候选" not in result["message"]
    assert "选区已保留" in result["message"]
    assert any(issue["region_ids"] == ["manual"] and "遗漏" in issue["reason"]
               for issue in result["review_diagnostics"]["issues"])
    run = app.runtime_store.get_run(result["run_id"])
    assert run.status.value == "waiting"
    audit = run.state["image_edit_plan_review"]
    assert len(audit["attempts"]) == 2 and "secret" not in json.dumps(audit)
    assert "private_debug" not in json.dumps(calls[1])
    assert app.comic_image_operation(pid, "edit", request) == result
    assert len(calls) == 2  # Refresh/unknown delivery must not start another paid attempt.


@pytest.mark.parametrize("change", ["none", "item", "question"])
def test_edit_plan_repair_keeps_typed_items_and_real_ambiguity(
    review_app, monkeypatch, change,
):
    app = review_app
    pid, cid, image = setup_image(app)
    report, _, _ = analyze(app, pid, cid, image, monkeypatch)
    item = {"item_id": "lighting", "operation": "modify", "requirement_ids": ["region:first"],
        "object_id": "background", "target": "背景", "attribute": "lighting",
        "desired_state": "略暗", "edit_bounds": [.2, .2, .7, .7]}
    question = {"requirement_ids": ["region:second"], "reason": "有两个对象，请选择要修改的对象。"}
    calls = []
    def vision(messages):
        payload = json.loads(messages[1]["content"][0]["text"])
        calls.append(payload)
        if len(calls) == 1:
            return json.dumps({"items": [item, {"item_id": "broken",
                "requirement_ids": ["region:second"], "private_debug": "secret"}],
                "questions": [question]})
        assert payload["fixed_items"][0]["desired_state"] == "略暗"
        assert payload["fixed_questions"] == [question]
        assert "secret" not in json.dumps(messages)
        return json.dumps({"items": [{**item, **({"desired_state": "变亮"}
                            if change == "item" else {})}],
            "questions": [] if change == "question" else [question]})
    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    result, _, compiled = edit(app, pid, cid, image, report, monkeypatch, request_fields={
        "selected_finding_ids": [], "edit_scope": {"mode": "regions",
            "region_semantics": "locator", "regions": [
                {"region_id": "first", "bounds": [.1, .1, .8, .8], "instruction": "背景暗一点"},
                {"region_id": "second", "bounds": [.1, .1, .9, .9],
                 "instruction": "把它换个颜色"}]}})
    assert len(calls) == 2 and compiled == [] and "artifact_id" not in result
    assert result["edit_status"] == ("needs_clarification" if change == "none"
                                      else "review_unavailable")
    audit = app.runtime_store.get_run(result["run_id"]).state["image_edit_plan_review"]
    if change != "none":
        assert "fixed_" + change + "_changed" in {
            issue["type"] for issue in audit["attempts"][1]["issues"]}


def test_concurrent_idempotency_restart_and_unknown_never_resend(review_app, monkeypatch):
    app = review_app
    pid, cid, image = setup_image(app)
    report, request, seen = analyze(app, pid, cid, image, monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: app.comic_image_operation(pid, "analysis", request),
                                    range(2)))
    assert all(item["artifact_id"] == report["artifact_id"] for item in results) and len(seen) == 1
    restarted = web_studio.StudioApplication()
    try:
        assert restarted.comic_image_operation(pid, "analysis", request) == report
        with pytest.raises(ToolError, match="不同内容"):
            restarted.comic_image_operation(pid, "analysis", {**request, "task": "不同任务"})
    finally:
        restarted.runner.close()
    attempts = []
    def unavailable(_):
        attempts.append(True)
        raise TimeoutError("offline unknown")
    monkeypatch.setattr(app, "_comic_image_vision_model", unavailable)
    failed = {**common(app, pid, cid, "unknown"), "image": image, "task": "新检查"}
    with pytest.raises(TimeoutError):
        app.comic_image_operation(pid, "analysis", failed)
    with pytest.raises(ToolError, match="不会自动重发"):
        app.comic_image_operation(pid, "analysis", failed)
    assert len(attempts) == 1


def test_valid_empty_report_and_invalid_response_are_distinct(review_app, monkeypatch):
    app = review_app
    pid, cid, image = setup_image(app)
    clean = {**observation(), "findings": []}
    report, _, _ = analyze(app, pid, cid, image, monkeypatch, output=clean)
    assert report["output"]["findings"] == [] and report["advisory"]
    with pytest.raises(ToolError, match="有效公开结构"):
        analyze(app, pid, cid, image, monkeypatch, output={"private_reasoning": "never save"},
                request_id="bad-output")
    assert len(app.comic_image_reports(pid, cid, "analysis")["items"]) == 1


@pytest.mark.parametrize("change", ["task", "constraints", "references", "content", "conversation"])
def test_report_rejects_changed_basis_before_edit_call(review_app, monkeypatch, change):
    app = review_app
    pid, cid, image = setup_image(app)
    report, _, _ = analyze(app, pid, cid, image, monkeypatch)
    request = {**common(app, pid, cid, "edit-stale"), "image": image, "task": "检查主体",
               "analysis_artifact_id": report["artifact_id"], "selected_finding_ids": ["selected"]}
    if change == "task":
        request["task"] = "另一任务"
    elif change == "constraints":
        app.update_comic_brief(pid, {"expected_version": request["expected_project_version"],
            "original_request": "制作独立画面", "hard_constraints": ["保留所有背景"]})
        request.update(common(app, pid, cid, "edit-stale"))
    elif change == "references":
        app.bind_comic_references(pid, {**common(app, pid, cid, "bind"),
            "expected_reference_version": 0, "bindings": [
                {"image": image, "roles": ["composition"], "target": "布局"}]})
        request.update(common(app, pid, cid, "edit-stale"))
    elif change == "content":
        artifact = app.runtime_store.get_artifact(
            app.comic_media.get(image["image_id"]).artifact_id)
        Path(artifact.location).write_bytes(png(30))
        with pytest.raises(ToolError, match="内容"):
            app.get_core_artifact_content(artifact.id)
    else:
        request["conversation_id"] = app.create_conversation({
            "interaction_mode": "guided", "domain": "comic"})["id"]
    with pytest.raises(ToolError):
        app.comic_image_operation(pid, "edit", request)


def test_unconfirmed_import_and_scope_conflict_are_blocked(review_app, monkeypatch):
    app = review_app
    pid, cid, image = setup_image(app, constraints=["主体轮廓不能改变"])
    report, _, _ = analyze(app, pid, cid, image, monkeypatch)
    unresolved, _, _ = edit(app, pid, cid, image, report, monkeypatch, approved=False)
    assert unresolved["edit_status"] == "review_unavailable"
    assert "artifact_id" not in unresolved
    assert app.comic_image_reports(pid, cid, "edit")["items"] == []
    # The unresolved result is retained; only a new explicit request makes another attempt.
    report2, _, _ = analyze(app, pid, cid, image, monkeypatch, request_id="new-analysis")
    instruction, _, _ = edit(app, pid, cid, image, report2, monkeypatch, request_id="edit-new")
    with pytest.raises(ToolError, match="先确认"):
        upload(app, pid, cid, color=60, parent=image,
               instruction_artifact_id=instruction["artifact_id"])
    assert len(app.comic_media.list(pid)) == 1


def test_http_persisted_report_queries_are_read_only(review_app, monkeypatch):
    app = review_app
    pid, cid, image = setup_image(app)
    monkeypatch.setattr(app, "_comic_image_vision_model", lambda _: json.dumps(observation()))
    server = web_studio.make_server(app)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    headers = {"X-Studio-Token": app.token, "Content-Type": "application/json"}
    try:
        path = f"/api/comic/projects/{pid}/image-analyses"
        connection.request("POST", path, json.dumps({**common(app, pid, cid, "http-analysis"),
            "image": image, "task": "检查主体"}), headers)
        response = connection.getresponse()
        assert response.status == 201
        result = json.loads(response.read())
        monkeypatch.setattr(app, "_comic_image_vision_model",
                            lambda _: pytest.fail("查询不能调用模型"))
        for suffix in ("", "/" + result["artifact_id"]):
            connection.request("GET", path + suffix + "?conversation_id=" + cid, headers=headers)
            response = connection.getresponse()
            assert response.status == 200
            response.read()
        def blocked_edit(messages):
            payload = json.loads(messages[-1]["content"])
            if "sections" in payload:
                return json.dumps({"complete": True, "scope_valid": True, "findings": [],
                    "edit_findings": []})
            return json.dumps({"positive_prompt": "离线待核验正文", "negative_prompt": "",
                               "summary": "测试失败投影"})
        monkeypatch.setattr(app, "_comic_constraint_model", blocked_edit)
        connection.request("POST", f"/api/comic/projects/{pid}/edit-instructions", json.dumps({
            **common(app, pid, cid, "http-edit"), "image": image, "task": "检查主体",
            "analysis_artifact_id": result["artifact_id"], "selected_finding_ids": ["selected"]}),
            headers)
        response = connection.getresponse()
        assert response.status == 201
        failure = json.loads(response.read())
        assert failure["edit_status"] == "review_unavailable" and "artifact_id" not in failure
        assert failure["error_id"] and failure["trace_id"]
        assert failure["review_diagnostics"]["issues"][0]["finding_ids"] == ["selected"]
        assert failure["review_diagnostics"]["diagnostic_artifact_id"]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join()


def test_edit_review_uses_frozen_targets_and_persists_missing_and_scope_failures(
    review_app, monkeypatch,
):
    app = review_app
    pid, cid, image = setup_image(app)
    report, _, _ = analyze(app, pid, cid, image, monkeypatch)
    scope = {"mode": "regions", "regions": [{"region_id": "selected-region",
        "label": "区域1", "bounds": [0.1, 0.1, 0.8, 0.8], "finding_id": "selected",
        "instruction": ""}], "whole_finding_ids": [],
        "instruction_target": {"mode": "selected_regions"}}
    for verdict in ("missing", "scope", "fabricated", "normalize"):
        calls = []
        def model(messages, verdict=verdict, calls=calls):
            payload = json.loads(messages[-1]["content"])
            calls.append(payload)
            if isinstance(payload, list):
                return json.dumps({"constraints": [{"source_index": 0,
                    "source_quote": payload[0]["quote"], "target": "本轮区域", "scope": "output"}]})
            if payload.get("stage") == "constraint_extraction_review":
                identity = payload["constraints"][0]["constraint_id"]
                return json.dumps({"complete": False, "scope_valid": False,
                    "constraint_replacements": [], "findings": [{
                    "constraint_id": identity, "status": "conflict",
                    "reason": "离线输入冲突投影", "evidence_path":
                    "constraints." + identity + ".source_quote"}]})
            if "sections" not in payload:
                return json.dumps({"positive_prompt": "只修改指定区域，保留其他内容。",
                                   "negative_prompt": "", "summary": "离线指令"})
            assert payload["edit_scope"] == scope
            assert [item["requirement_id"] for item in payload["authorized_requirements"]] == [
                "finding:selected"]
            assert payload["authorized_requirements"][0]["target"] == {
                "mode": "regions", "region_ids": ["selected-region"]}
            assert "unselected" not in json.dumps(payload["edit_context"])
            finding = {"constraint_id": "finding:selected", "status": "covered",
                       "evidence_path": "sections.positive_prompt"}
            if verdict == "fabricated":
                finding.update(evidence_path=None, quote="不存在于正文的证据")
            return json.dumps({"complete": True, "scope_valid": verdict != "scope",
                "findings": [], "edit_findings": [] if verdict == "missing" else [finding],
                "scope_issues": [{"requirement_ids": ["finding:selected"],
                    "reason": "局部要求被扩展到区域外"}] if verdict == "scope" else []})
        monkeypatch.setattr(app, "_comic_constraint_model", model)
        failure = app.comic_image_operation(pid, "edit", {**common(app, pid, cid, verdict),
                "image": image, "task": "检查主体", "analysis_artifact_id": report["artifact_id"],
                "selected_finding_ids": ["selected"], "edit_scope": scope, "round_id": verdict,
                "instruction": "区域颜色改为蓝色，同时必须保持红色"
                if verdict == "normalize" else ""})
        assert failure["edit_status"] == "review_unavailable"
        assert len(calls) == 4
        diagnostic = failure["review_diagnostics"]
        assert diagnostic["stage"] == ("normalize" if verdict == "normalize" else "final_review")
        assert diagnostic["issues"][0]["finding_ids"] == (
            [] if verdict == "normalize" else ["selected"])
        if verdict == "normalize":
            assert "user-instruction" in diagnostic["issues"][0]["requirement_ids"]
        assert diagnostic["issues"][0]["region_ids"] == ["selected-region"]
        audit = app.runtime_store.get_artifact(diagnostic["diagnostic_artifact_id"])
        assert audit.type == ArtifactType.JSON and audit.metadata["internal"] is True
        assert audit.conversation_id == cid and audit.metadata["source_image"] == image
        assert audit.metadata["round_id"] == verdict and audit.metadata["trace_id"]
        run = app.runtime_store.get_run(diagnostic["run_id"])
        assert run.state["review_diagnostics"] == diagnostic
        assert any(item["id"] == run.id for item in app.list_comic_project_tasks(pid)["tasks"])
    assert app.comic_image_reports(pid, cid, "edit")["items"] == []


@pytest.mark.parametrize("first_failure", ["missing", "inconsistent_flags", "invalid_json"])
def test_edit_corrects_one_received_review_failure_before_publishing(
    review_app, monkeypatch, first_failure,
):
    app = review_app
    pid, cid, image = setup_image(app)
    report, _, _ = analyze(app, pid, cid, image, monkeypatch)
    calls = []

    def model(messages):
        payload = json.loads(messages[-1]["content"])
        calls.append(payload)
        if "sections" not in payload:
            return json.dumps({"positive_prompt": "修正已选边缘，其他部分保持原图。",
                               "negative_prompt": "", "summary": "离线改图候选"})
        reviews = sum("sections" in item for item in calls)
        if reviews == 1 and first_failure == "invalid_json":
            return '{"private_debug": "must never enter correction inputs"}'
        return json.dumps({"complete": reviews > 1 or first_failure != "inconsistent_flags",
            "scope_valid": True, "findings": [], "edit_findings": []
            if reviews == 1 and first_failure == "missing" else [{
                "constraint_id": "finding:selected", "status": "covered",
                "evidence_path": "sections.positive_prompt"}]})

    monkeypatch.setattr(app, "_comic_constraint_model", model)
    request = {**common(app, pid, cid, "review-repair"), "image": image,
        "task": "检查主体", "analysis_artifact_id": report["artifact_id"],
        "selected_finding_ids": ["selected"]}
    result = app.comic_image_operation(pid, "edit", request)
    assert result["edit_status"] == "ready"
    assert result["correction"]["used"] is True
    assert len(calls) == 4
    assert result["constraint_receipt"]["review"]["complete"] is True
    assert app.comic_image_operation(pid, "edit", request) == result
    assert len(calls) == 4
    assert "private_debug" not in json.dumps(calls)
    assert len(app.comic_image_reports(pid, cid, "edit")["items"]) == 1


def test_edit_unavailable_review_is_persisted_without_a_confirmable_prompt(review_app, monkeypatch):
    app = review_app
    pid, cid, image = setup_image(app)
    report, _, _ = analyze(app, pid, cid, image, monkeypatch)
    calls = []

    def model(messages):
        payload = json.loads(messages[-1]["content"])
        calls.append(payload)
        if "sections" not in payload:
            return json.dumps({"positive_prompt": "只修复边缘，保留其他内容。",
                               "negative_prompt": "", "summary": "未核验候选"})
        return json.dumps({"complete": False, "scope_valid": True,
                           "findings": [], "edit_findings": []})

    monkeypatch.setattr(app, "_comic_constraint_model", model)
    request = {**common(app, pid, cid, "review-unavailable"), "image": image,
        "task": "检查主体", "analysis_artifact_id": report["artifact_id"],
        "selected_finding_ids": ["selected"], "round_id": "review-round"}
    result = app.comic_image_operation(pid, "edit", request)
    assert result["edit_status"] == "review_unavailable"
    assert "artifact_id" not in result
    assert result["correction"]["used"] is True
    assert result["review_diagnostics"]["issues"][0]["finding_ids"] == ["selected"]
    run = app.runtime_store.get_run(result["run_id"])
    assert run.status.value == "waiting"
    assert run.state["image_edit_candidate"]["output"]["summary"] == "未核验候选"
    assert app.comic_image_operation(pid, "edit", request) == result
    assert len(calls) == 4
    assert app.comic_image_reports(pid, cid, "edit")["items"] == []


@pytest.mark.parametrize("final_missing", [False, True])
def test_normalization_and_compiler_share_one_correction_allowance(
    review_app, monkeypatch, final_missing,
):
    app = review_app
    pid, cid, image = setup_image(app)
    calls = []

    def model(messages):
        payload = json.loads(messages[-1]["content"])
        calls.append(payload)
        if isinstance(payload, list):
            assert payload[0]["edit_targets"][0]["requirement_id"] == "user-instruction"
            return json.dumps({"constraints": [{"source_index": 0,
                "source_quote": payload[0]["quote"], "target": "原有背景", "scope": "world"}]})
        if payload.get("stage") == "constraint_extraction_review":
            complete = sum(isinstance(item, list) for item in calls) > 1
            return json.dumps({"complete": complete, "scope_valid": True,
                "constraint_replacements": [], "findings": [{
                    "constraint_id": item["constraint_id"], "status": "covered",
                    "evidence_path": f"constraints.{item['constraint_id']}.source_quote"}
                    for item in payload["constraints"]] if complete else []})
        if "sections" not in payload:
            return json.dumps({"positive_prompt": "仅使背景变暗，保留其他内容。",
                               "negative_prompt": "", "summary": "离线候选"})
        return json.dumps({"complete": not final_missing, "scope_valid": True,
            "findings": [{"constraint_id": item["constraint_id"], "status": "covered",
                "evidence_path": "sections.positive_prompt"} for item in payload["constraints"]],
            "edit_findings": [] if final_missing else [{"constraint_id": "user-instruction",
                "status": "covered", "evidence_path": "sections.positive_prompt"}]})

    monkeypatch.setattr(app, "_comic_constraint_model", model)
    result = app.comic_image_operation(pid, "edit", {**common(app, pid, cid, "one-allowance"),
        "image": image, "task": "调整画面", "instruction": "背景暗一点，其他不变",
        "edit_scope": {"mode": "whole"}})
    assert len(calls) == 6
    assert result["correction"] == {"used": True, "stage": "normalize"}
    assert result["edit_status"] == ("review_unavailable" if final_missing else "ready")
    assert ("artifact_id" in result) is not final_missing


def test_review_transport_error_never_spends_correction_and_keeps_candidate(
    review_app, monkeypatch,
):
    app = review_app
    pid, cid, image = setup_image(app)
    report, _, _ = analyze(app, pid, cid, image, monkeypatch)
    calls = []

    def model(messages):
        payload = json.loads(messages[-1]["content"])
        calls.append(payload)
        if "sections" in payload:
            raise ToolError("离线模拟：供应商结果未知", detail="module=llm stage=transport")
        return json.dumps({"positive_prompt": "仅修复边缘。", "negative_prompt": "",
                           "summary": "供应商故障前的候选"})

    monkeypatch.setattr(app, "_comic_constraint_model", model)
    request = {**common(app, pid, cid, "unknown-review"), "image": image,
        "task": "检查主体", "analysis_artifact_id": report["artifact_id"],
        "selected_finding_ids": ["selected"]}
    with pytest.raises(ToolError) as raised:
        app.comic_image_operation(pid, "edit", request)
    diagnostic = raised.value.review_diagnostics
    assert diagnostic["stage"] == "final_review"
    run = app.runtime_store.get_run(diagnostic["run_id"])
    assert run.status.value == "failed"
    assert run.state["image_edit_candidate"]["output"]["summary"] == "供应商故障前的候选"
    with pytest.raises(ToolError, match="不会自动重发"):
        app.comic_image_operation(pid, "edit", request)
    assert len(calls) == 2
    assert app.comic_image_reports(pid, cid, "edit")["items"] == []


def test_unchanged_conflicting_candidate_cannot_be_promoted_by_another_review(
    review_app, monkeypatch,
):
    app = review_app
    pid, cid, image = setup_image(app)
    report, _, _ = analyze(app, pid, cid, image, monkeypatch)
    calls = []

    def model(messages):
        payload = json.loads(messages[-1]["content"])
        calls.append(payload)
        if "sections" not in payload:
            return json.dumps({"positive_prompt": "修复边缘", "negative_prompt": "禁止修复边缘",
                               "summary": "仅修改摘要" if "correction" in payload else "候选"})
        return json.dumps({"complete": True, "scope_valid": True, "findings": [],
            "edit_findings": [{"constraint_id": "finding:selected", "status": "conflict",
                "reason": "正负向指令互相矛盾", "evidence_path": "sections.positive_prompt"}]})

    monkeypatch.setattr(app, "_comic_constraint_model", model)
    result = app.comic_image_operation(pid, "edit", {**common(app, pid, cid, "unchanged-conflict"),
        "image": image, "task": "检查主体", "analysis_artifact_id": report["artifact_id"],
        "selected_finding_ids": ["selected"]})
    assert result["edit_status"] == "review_unavailable"
    assert result["review_diagnostics"]["issues"][0]["code"] == "conflict"
    assert "artifact_id" not in result and len(calls) == 3


def test_comic_vision_compacts_upload_in_memory_and_preserves_source_and_resolution(monkeypatch):
    picture = Image.effect_noise((1774, 887), 64).convert("RGB")
    with BytesIO() as buffer:
        picture.save(buffer, format="PNG")
        original = buffer.getvalue()
    picture.close()
    fingerprint = hashlib.sha256(original).hexdigest()
    messages = [{"role": "system", "content": "公开观察"}, {"role": "user", "content": [
        {"type": "text", "text": "当前图"}, {"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(original).decode()}}]}]
    saved = json.dumps(messages)
    calls = []

    def provider(prepared, **kwargs):
        calls.append((prepared, kwargs))
        return SimpleNamespace(content="{}")

    monkeypatch.setattr(web_studio, "vision_chat", provider)
    assert web_studio.StudioApplication._comic_image_vision_model(messages) == "{}"
    sent, options = calls[0]
    pixels = next(item for item in sent[1]["content"] if item["type"] == "image_url")
    encoded = base64.b64decode(pixels["image_url"]["url"].split(",", 1)[1])
    assert len(encoded) <= 1024 * 1024 < len(original)
    with Image.open(BytesIO(encoded)) as transmitted:
        assert transmitted.size == (1774, 887)
    assert json.dumps(messages) == saved
    assert fingerprint in json.dumps(sent)
    assert options["single_attempt"] is True and len(calls) == 1


def test_repeated_vision_inputs_are_prepared_once_per_operation(monkeypatch):
    image = png()
    messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {
        "url": "data:image/png;base64," + base64.b64encode(image).decode()}}]}]
    prepared_inputs = []
    prepare = web_studio.prepare_vision_image

    def prepare_once(*args, **kwargs):
        prepared_inputs.append(1)
        return prepare(*args, **kwargs)

    sent = []
    monkeypatch.setattr(web_studio, "prepare_vision_image", prepare_once)
    monkeypatch.setattr(web_studio, "vision_chat", lambda payload, **_: (
        sent.append(payload) or SimpleNamespace(content="{}")))
    with web_studio._reuse_vision_inputs():
        web_studio.StudioApplication._comic_image_vision_model(messages)
        web_studio.StudioApplication._comic_image_vision_model(messages)
        assert len(prepared_inputs) == 1 and sent[0] == sent[1]
        changed = [{"role": "user", "content": [{"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(png(120)).decode()}}]}]
        web_studio.StudioApplication._comic_image_vision_model(changed)
        assert len(prepared_inputs) == 2 and sent[2] != sent[0]
    with web_studio._reuse_vision_inputs():
        web_studio.StudioApplication._comic_image_vision_model(messages)
    assert len(prepared_inputs) == 3


@pytest.mark.parametrize("alpha", [False, True])
def test_vision_preparation_keeps_orientation_and_alpha_without_writing_files(alpha):
    from kantoku.capabilities.image import prepare_vision_image

    picture = Image.new("RGBA" if alpha else "RGB", (60, 40), (200, 30, 10, 80)
                        if alpha else (200, 30, 10))
    picture.putpixel((0, 0), (0, 255, 0, 80) if alpha else (0, 255, 0))
    exif = Image.Exif()
    exif[274] = 6
    with BytesIO() as buffer:
        picture.save(buffer, format="PNG", exif=exif)
        original = buffer.getvalue()
    picture.close()
    prepared = prepare_vision_image(original, "image/png")
    with Image.open(BytesIO(prepared.data)) as displayed:
        assert displayed.size == (40, 60)
        assert displayed.getexif().get(274, 1) == 1
        if alpha:
            assert displayed.mode == "RGBA" and displayed.getpixel((10, 10))[3] == 80
    assert prepared.metadata["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert prepared.metadata["orientation_applied"] is True


def test_vision_preparation_bounds_large_images_and_keeps_small_original_bytes():
    from kantoku.capabilities.image import prepare_vision_image

    small = png()
    unchanged = prepare_vision_image(small, "image/png")
    assert unchanged.data == small and unchanged.metadata["transformed"] is False
    with Image.new("RGB", (2400, 1200), (20, 40, 60)) as large, BytesIO() as buffer:
        large.save(buffer, format="PNG")
        prepared = prepare_vision_image(buffer.getvalue(), "image/png")
    assert prepared.metadata["display_size"] == [2400, 1200]
    assert prepared.metadata["sent_size"] == [2048, 1024]
    assert prepared.metadata["resized"] is True
    with pytest.raises(ToolError, match="解码"):
        prepare_vision_image(b"\x89PNG\r\n\x1a\ninvalid", "image/png")


def test_compact_vision_crosses_actual_sdk_upload_boundary_once_without_network(monkeypatch):
    import httpx2
    from openai import OpenAI

    picture = Image.effect_noise((1774, 887), 64).convert("RGB")
    with BytesIO() as buffer:
        picture.save(buffer, format="PNG")
        original = buffer.getvalue()
    picture.close()
    messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {
        "url": "data:image/png;base64," + base64.b64encode(original).decode()}}]}]
    settings = get_settings().model_copy(deep=True)
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    monkeypatch.setattr(web_studio, "get_settings", lambda: settings)
    monkeypatch.setattr(type(settings.llm), "vision_api_key", lambda _: "offline-key")
    calls, clients = [], []

    def transport(request):
        calls.append(len(request.content))
        # 离线模拟大请求上传受阻；不声称真实代理存在此固定限制。
        if len(request.content) > 2 * 1024 * 1024:
            raise httpx2.WriteTimeout("offline upload limit", request=request)
        return httpx2.Response(200, json={"id": "offline-vision", "object": "chat.completion",
            "created": 0, "model": "offline", "usage": {"prompt_tokens": 10,
            "completion_tokens": 5, "total_tokens": 15}, "choices": [{"index": 0,
            "finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}]})

    def factory(**_kwargs):
        client = OpenAI(base_url="https://offline.invalid/v1", api_key="offline-key", max_retries=0,
            http_client=httpx2.Client(transport=httpx2.MockTransport(transport), trust_env=False))
        clients.append(client)
        return client

    monkeypatch.setattr(llm, "_build_client", factory)
    with pytest.raises(LLMError, match="上传请求内容超时"):
        llm.vision_chat(messages, single_attempt=True)
    assert web_studio.StudioApplication._comic_image_vision_model(messages) == "{}"
    assert len(calls) == 2 and calls[0] > 2 * 1024 * 1024 > calls[1]
    assert all(client.is_closed() for client in clients)


def test_vision_single_attempt_preserves_old_default_and_usage(monkeypatch):
    settings = get_settings().model_copy(deep=True)
    settings.llm.retry = 2
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    monkeypatch.setattr(type(settings.llm), "vision_api_key", lambda _: "offline-key")
    calls = []
    def provider(**kwargs):
        calls.append(kwargs)
        if kwargs["on_usage"]:
            kwargs["on_usage"](3, 4, True)
        return SimpleNamespace(content="{}")
    monkeypatch.setattr(llm, "_call_provider", provider)
    usage = []
    llm.vision_chat([], single_attempt=True, on_usage=lambda *args: usage.append(args))
    llm.vision_chat([])
    assert [call["retry"] for call in calls] == [0, 2] and usage == [(3, 4, True)]


def test_reference_pixels_and_literal_edit_are_verified(review_app, monkeypatch):
    app = review_app
    pid, cid, image = setup_image(app)
    reference = upload(app, pid, cid, color=80)
    app.bind_comic_references(pid, {**common(app, pid, cid, "bind-ref"),
        "expected_reference_version": 0, "bindings": [{"image": {
            "image_id": reference["image_id"], "version": 1}, "roles": ["style"],
            "target": "当前画面"}]})
    report, _, seen = analyze(app, pid, cid, image, monkeypatch)
    assert len([part for part in seen[0][1]["content"] if part["type"] == "image_url"]) == 2
    instruction = "把铭牌上的文字原样改成 UNIT 37"
    calls = []
    def model(messages):
        payload = json.loads(messages[-1]["content"])
        calls.append(payload)
        if isinstance(payload, list):
            return json.dumps({"constraints": [{"source_index": 0,
                "source_quote": instruction, "requirement": "铭牌文字为 UNIT 37",
                "target": "铭牌", "scope": "output", "verification": "exact",
                "literal_value": "UNIT 37"}]}, ensure_ascii=False)
        if payload.get("stage") == "constraint_extraction_review":
            return json.dumps({"complete": True, "scope_valid": True,
                "constraint_replacements": [], "findings": [{
                "constraint_id": payload["constraints"][0]["constraint_id"],
                "status": "covered", "quote": instruction}]}, ensure_ascii=False)
        if "sections" in payload:
            return json.dumps({"complete": True, "scope_valid": True, "findings": [{
                "constraint_id": payload["constraints"][0]["constraint_id"],
                "status": "covered", "evidence_path": "sections.positive_prompt"}],
                "edit_findings": [{"constraint_id": item["requirement_id"], "status": "covered",
                    "evidence_path": "sections.positive_prompt"}
                    for item in payload["authorized_requirements"]]})
        return json.dumps({"positive_prompt": "将铭牌改成 UNIT 37；保留其余内容。",
                           "negative_prompt": "", "summary": "只修改指定文字"}, ensure_ascii=False)
    monkeypatch.setattr(app, "_comic_constraint_model", model)
    result = app.comic_image_operation(pid, "edit", {**common(app, pid, cid, "literal-edit"),
        "image": image, "task": "检查主体", "analysis_artifact_id": report["artifact_id"],
        "instruction": instruction})
    assert result["edit_constraints"][0]["literal_value"] == "UNIT 37"
    assert len(calls) == 4
    state = app.runtime_store.get_run(result["run_id"]).state
    assert state["media_input"]["instruction"] == instruction


@pytest.mark.parametrize("ambiguous", [False, True])
def test_manual_region_review_receives_actual_pixels_and_keeps_scope_gate(
    review_app, monkeypatch, ambiguous,
):
    app = review_app
    pid, cid, image = setup_image(app, constraints=["右侧铜鱼", "保留左侧铭牌"])
    instruction = "把这个换成陶瓷飞鸟"
    scope = {"mode": "regions", "regions": [{"region_id": "right", "label": "手动1",
        "bounds": [0.55, 0.2, 0.95, 0.85], "finding_id": None,
        "instruction": instruction}]}
    text_calls, vision_calls = [], []

    def text_model(messages):
        payload = json.loads(messages[-1]["content"])
        text_calls.append(payload)
        if isinstance(payload, list):
            assert payload[0]["quote"] == instruction
            return json.dumps({"constraints": [{"source_index": 0,
                "source_quote": instruction, "requirement": "换成陶瓷飞鸟",
                "target": "这个主体", "scope": "identity"}]})
        assert payload.get("stage") != "constraint_extraction_review"
        if "sections" not in payload:
            assert payload["constraints"][0]["enforcement"] == "scoped_replacement"
            return json.dumps({"positive_prompt": "仅在右侧选区换成陶瓷飞鸟，"
                "保留左侧铭牌及框外内容。",
                "negative_prompt": "不扩大范围", "summary": "离线范围契约"})
        return json.dumps({"complete": True, "scope_valid": True, "findings": [{
            "constraint_id": item["constraint_id"], "status": "covered",
            "evidence_path": "sections.positive_prompt"} for item in payload["constraints"]],
            "edit_findings": [{"constraint_id": "region:right", "status": "covered",
                "evidence_path": "sections.positive_prompt"}]})

    def vision_model(messages):
        vision_calls.append(messages)
        parts = messages[1]["content"]
        context = json.loads(parts[0]["text"])
        pixels = [part for part in parts if part["type"] == "image_url"]
        assert len(pixels) == 1
        assert base64.b64decode(pixels[0]["image_url"]["url"].split(",", 1)[1]) == png()
        assert context["edit_basis"]["edit_scope"] == scope
        requirement = context["edit_basis"]["authorized_requirements"][0]
        assert requirement["requirement"] == instruction
        assert requirement["target"] == {"mode": "regions", "region_ids": ["right"]}
        old = context["edit_basis"]["historical_constraints"][0]
        return json.dumps({"complete": not ambiguous, "scope_valid": not ambiguous,
            "findings": [{"constraint_id": item["constraint_id"], "status": "covered",
                "evidence_path": f"constraints.{item['constraint_id']}.source_quote"}
                for item in context["constraints"]],
            "constraint_replacements": [] if ambiguous else [{
                "constraint_id": old["constraint_id"], "requirement_id": "region:right",
                "source_quote": instruction, "reason": "离线视觉替身：仅替换框内对象"}],
            "scope_issues": [{"requirement_ids": ["region:right"],
                "reason": "框内存在多个可能对象"}] if ambiguous else []})

    monkeypatch.setattr(app, "_comic_constraint_model", text_model)
    monkeypatch.setattr(app, "_comic_image_vision_model", vision_model)
    request = {**common(app, pid, cid, "visual-edit"), "image": image,
               "task": "检查当前画面", "edit_scope": scope, "round_id": "visual-round"}
    if ambiguous:
        unresolved = app.comic_image_operation(pid, "edit", request)
        assert unresolved["edit_status"] == "needs_clarification"
        assert unresolved["review_diagnostics"]["issues"][0]["region_ids"] == ["right"]
        assert unresolved["correction"]["used"] is False
        assert app.comic_image_operation(pid, "edit", request) == unresolved
        assert len(text_calls) == 1
        assert app.comic_image_reports(pid, cid, "edit")["items"] == []
    else:
        result = app.comic_image_operation(pid, "edit", request)
        assert len(text_calls) == 3
        visual_basis = result["scope"]["constraint_resolution"]["visual_basis"]
        assert visual_basis["source_image"] == image
        assert visual_basis["sha256"] == result["basis"]["images"][0]["sha256"]
        assert app.comic_image_operation(pid, "edit", request) == result
        assert len(text_calls) == 3
    assert len(vision_calls) == 1


@pytest.mark.parametrize("old,new,is_text", [
    ("铜制机械鱼", "陶瓷飞鸟", False), ("OPEN 24/7", "UNIT 37", True)])
def test_manual_revision_supersedes_only_its_old_target_and_comparison_keeps_exceptions(
    review_app, monkeypatch, old, new, is_text,
):
    """明确离线替身：重复来源、主体/文字替换及后续真实两图接口的范围契约。"""
    from kantoku.domains.comic.models import ComicProjectInput, CreativeConstraint

    app = review_app
    cid = app.create_conversation({"interaction_mode": "guided", "domain": "comic"})["id"]
    historical = [CreativeConstraint.model_validate({
        "constraint_id": identity, "requirement": text, "target": target,
        "scope": "output" if is_text or identity == "keep-label" else "identity",
        "source_ref": {"id": "message-" + identity, "kind": "message"},
        "source_quote": text, "origin": "user_request", "trust": "source_verified",
        "verification": "exact" if is_text or identity == "keep-label" else "semantic",
        "literal_value": text if is_text or identity == "keep-label" else None,
    }) for identity, text, target in (
        ("old-a", old, "右侧框内主体"), ("old-b", old, "右侧框内主体"),
        ("keep-label", "ARCHIVE 42", "左侧铭牌"))]
    pid = app.comic_projects.create(ComicProjectInput.model_validate({
        "title": "离线迭代契约", "brief": {
        "original_request": old, "hard_constraints": list(dict.fromkeys(
            item.requirement for item in historical)), "constraint_contract_version": 2,
        "semantic_constraints": [item.model_dump(mode="json") for item in historical],
    }})).project.project_id
    imported = upload(app, pid, cid)
    image = {"image_id": imported["image_id"], "version": 1}
    original_brief = app.comic_projects.get(pid).creative_brief
    report, _, _ = analyze(app, pid, cid, image, monkeypatch, task="检查当前画面",
        output={**observation(old), "protected_features": [old + "的位置与形象", "保留左侧铭牌"]},
        request_id="analysis-revision")
    instruction = "将框内的主体替换为" + new
    scope = {"mode": "regions", "regions": [{"region_id": "right",
        "label": "手动1", "bounds": [0.55, 0.2, 0.95, 0.85], "finding_id": None,
        "instruction": instruction}], "whole_finding_ids": [],
        "instruction_target": {"mode": "selected_regions"}}
    calls = []
    def model(messages):
        payload = json.loads(messages[-1]["content"])
        calls.append(payload)
        if isinstance(payload, list):
            return json.dumps({"constraints": [{"source_index": 0,
                "source_quote": instruction, "requirement": new, "target": "右侧框内主体",
                "scope": "output" if is_text else "identity",
                "verification": "exact" if is_text else "semantic",
                "literal_value": new if is_text else None}]})
        if payload.get("stage") == "constraint_extraction_review":
            return json.dumps({"complete": True, "scope_valid": True, "findings": [{
                "constraint_id": item["constraint_id"], "status": "covered",
                "evidence_path": "constraints." + item["constraint_id"] + ".source_quote"}
                for item in payload["constraints"]], "constraint_replacements": [{
                    "constraint_id": identity, "requirement_id": "region:right",
                    "source_quote": instruction, "reason": "人工明确替换同一框内对象；框外仍保留"}
                    for identity in ("old-a", "old-b")]})
        if "sections" not in payload:
            assert payload["historical_constraint_ids"] == [
                item.constraint_id for item in historical]
            assert payload["authorized_requirements"][0]["target"] == {
                "mode": "regions", "region_ids": ["right"]}
            assert "protected_features" not in payload and "task" not in payload
            assert payload["constraints"][0]["enforcement"] == "scoped_replacement"
            assert payload["constraints"][0]["effective_requirement"]["requirement"] == instruction
            assert payload["constraints"][0]["original_requirement"]["requirement"] == old
            return json.dumps({"positive_prompt": "仅在手动1坐标范围内替换为" + new
                + "，保留区域外内容和左侧文字 ARCHIVE 42。", "negative_prompt": "不扩大修改范围",
                "summary": "离线局部替代，不证明模型质量"})
        assert payload["constraints"] == calls[-2]["constraints"]
        assert "protected_features" not in payload["edit_context"]
        return json.dumps({"complete": True, "scope_valid": True, "findings": [{
            "constraint_id": item["constraint_id"], "status": "covered",
            "evidence_path": "sections.positive_prompt"} for item in payload["constraints"]],
            "edit_findings": [{"constraint_id": "region:right", "status": "covered",
                "evidence_path": "sections.positive_prompt"}], "constraint_replacements": []})
    monkeypatch.setattr(app, "_comic_constraint_model", model)
    result = app.comic_image_operation(pid, "edit", {**common(app, pid, cid, "revision"),
        "image": image, "task": "检查当前画面", "edit_scope": scope, "round_id": "revision",
        "analysis_artifact_id": report["artifact_id"]})
    assert len(calls) == 4  # Original normalization, compilation and independent review only.
    assert result["compiler_version"].endswith(":edit-5")
    assert app.comic_projects.get(pid).creative_brief == original_brief
    assert len(result["constraint_receipt"]["review"]["constraint_replacements"]) == 2
    assert result["scope"]["constraint_resolution"]["constraint_replacements"] == result[
        "constraint_receipt"]["review"]["constraint_replacements"]
    def contradictory_model(messages):
        payload = json.loads(messages[-1]["content"])
        output = json.loads(model(messages))
        if "sections" in payload:
            for item in output["findings"]:
                if item["constraint_id"] in {"old-a", "old-b"}:
                    item.update(status="conflict", reason="负向无条件保留旧主体，与框内替换矛盾")
        elif isinstance(payload, dict) and payload.get("mode") == "edit":
            output["negative_prompt"] = "不能改变" + old
        return json.dumps(output)
    monkeypatch.setattr(app, "_comic_constraint_model", contradictory_model)
    failed = app.comic_image_operation(pid, "edit", {**common(app, pid, cid, "contradictory-edit"),
        "image": image, "task": "检查当前画面", "edit_scope": scope, "round_id": "failed"})
    assert failed["edit_status"] == "review_unavailable" and "artifact_id" not in failed
    assert failed["review_diagnostics"]["issues"][0]["region_ids"] == ["right"]
    app.comic_image_operation(pid, "confirm", {**common(app, pid, cid, "confirm-revision"),
        "instruction_artifact_id": result["artifact_id"]})
    newer = upload(app, pid, cid, color=17, image_id=image["image_id"], expected_image_version=1,
                   parent=image, instruction_artifact_id=result["artifact_id"])
    def compare_model(messages):
        context = json.loads(messages[1]["content"][0]["text"])
        checks = {item["check_id"]: item for item in context["preservation"]}
        assert checks["preserve-constraint-old-a"]["authorized_exceptions"][0]["target"] == {
            "mode": "regions", "region_ids": ["right"]}
        assert "authorized_exceptions" not in checks["preserve-constraint-keep-label"]
        assert context["changes"][0]["requirement"] == instruction
        return json.dumps({"summary": "离线两图契约", "changes": [{"check_id": item["check_id"],
            "status": "uncertain", "evidence": "离线图像不证明修改完成", "confidence": 0.1}
            for item in context["changes"]], "preservation": [{"check_id": item["check_id"],
            "status": "uncertain", "evidence": "尺寸过小", "confidence": 0.1}
            for item in context["preservation"]], "other_observations": [], "limitations": []})
    monkeypatch.setattr(app, "_comic_image_vision_model", compare_model)
    app.comic_image_operation(pid, "comparison", {**common(app, pid, cid, "compare-revision"),
        "before": image, "after": {"image_id": image["image_id"], "version": newer["version"]},
        "instruction_artifact_id": result["artifact_id"]})
    assert app.comic_projects.get(pid).creative_brief == original_brief
    next_image = {"image_id": image["image_id"], "version": newer["version"]}
    report, _, vision_calls = analyze(app, pid, cid, next_image, monkeypatch,
                                      task="检查当前画面", request_id="next-analysis")
    context = json.loads(vision_calls[0][1]["content"][0]["text"])
    assert [item["constraint_id"] for item in context["inherited_constraint_replacements"]] == [
        "old-a", "old-b"]
    next_calls = []
    def next_model(messages):
        payload = json.loads(messages[-1]["content"])
        next_calls.append(payload)
        if "sections" not in payload:
            assert len(payload["inherited_constraint_replacements"]) == 2
            return json.dumps({"positive_prompt": "仅修改右侧边缘，保留" + new + "及 ARCHIVE 42",
                "negative_prompt": "不恢复旧主体，不改变其他内容", "summary": "离线第二轮"})
        assert payload["constraints"] == next_calls[-2]["constraints"]
        assert payload["constraints"][0]["enforcement"] == "scoped_replacement"
        assert new in payload["constraints"][0]["effective_requirement"]["requirement"]
        return json.dumps({"complete": True, "scope_valid": True, "findings": [{
            "constraint_id": item["constraint_id"], "status": "covered"
            if item["constraint_id"] == "keep-label" else "superseded",
            "evidence_path": "sections.positive_prompt"} for item in payload["constraints"]],
            "edit_findings": [{"constraint_id": "finding:selected", "status": "covered",
                "evidence_path": "sections.positive_prompt"}]})
    monkeypatch.setattr(app, "_comic_constraint_model", next_model)
    next_result = app.comic_image_operation(pid, "edit", {**common(app, pid, cid, "next-edit"),
        "image": next_image, "task": "检查当前画面", "analysis_artifact_id": report["artifact_id"],
        "selected_finding_ids": ["selected"], "edit_scope": {**scope, "regions": [{
            **scope["regions"][0], "finding_id": "selected", "instruction": ""}]},
        "round_id": "next-round"})
    assert len(next_calls) == 2
    assert next_result["constraint_receipt"]["review"]["constraint_replacements"] == []
    app.comic_image_operation(pid, "confirm", {**common(app, pid, cid, "confirm-next"),
        "instruction_artifact_id": next_result["artifact_id"]})
    third = upload(app, pid, cid, color=18, image_id=image["image_id"], expected_image_version=2,
                   parent=next_image, instruction_artifact_id=next_result["artifact_id"])
    basis, _ = app.comic_image_review._basis(pid, cid, ImageVersionRef(
        image_id=image["image_id"], version=third["version"]), "检查当前画面")
    assert len(basis["inherited_constraint_replacements"]) == 2
    source = app.comic_stories._source_contexts([app.comic_media.get(
        image["image_id"], version=third["version"]).model_dump(mode="json")])[0]
    assert source["source_kind"] == "confirmed_edit"
    protected = {item["constraint_id"]: item for item in source["constraints"]}
    for identity in ("old-a", "old-b"):
        assert protected[identity]["enforcement"] == "scoped_replacement"
        assert new in json.dumps(protected[identity]["effective_requirement"], ensure_ascii=False)
    assert protected["keep-label"]["enforcement"] == "preserve"
    branch = upload(app, pid, cid, color=19, image_id=image["image_id"], expected_image_version=3,
                    parent=image)
    original_basis, _ = app.comic_image_review._basis(pid, cid, ImageVersionRef(
        image_id=image["image_id"], version=branch["version"]), "检查当前画面")
    assert "inherited_constraint_replacements" not in original_basis
    assert app.comic_projects.get(pid).creative_brief == original_brief


def test_edit_replacement_cannot_release_assets_current_demands_or_unknown_sources():
    from kantoku.domains.comic.constraints import verify_prompt_constraints
    from kantoku.domains.comic.models import CreativeConstraint

    for case in ("asset", "current", "analysis", "quote", "unknown", "unrecorded", "late", "scope"):
        constraint = CreativeConstraint.model_validate({"constraint_id": "protected",
            "requirement": "固定身份", "target": "主体", "scope": "identity",
            "source_ref": {"id": "original", "kind": "message"},
            "source_quote": "固定身份", "origin": "asset_fact" if case == "asset"
            else "user_request", "trust": "source_verified"})
        requirement = {"requirement_id": "region:right", "requirement": "将框内主体换成飞鸟",
            "source": {"kind": "selected_analysis" if case == "analysis" else "manual_region"},
            "target": {"mode": "regions", "region_ids": ["right"]}}
        scope = {"edit_scope": {"mode": "regions", "regions": [{"region_id": "right",
                    "bounds": [0.5, 0.1, 0.9, 0.9]}]},
            "authorized_requirements": [requirement],
            "historical_constraint_ids": [] if case == "current" else ["protected"]}
        review = {"complete": True, "scope_valid": case != "scope", "findings": [{
            "constraint_id": "protected", "status": "superseded",
            "evidence_path": "sections.positive_prompt"}], "edit_findings": [{
            "constraint_id": "region:right", "status": "covered",
            "evidence_path": "sections.positive_prompt"}], "constraint_replacements": []
            if case == "unrecorded" else [{"constraint_id": "protected",
                "requirement_id": "unknown" if case == "unknown" else "region:right",
                "source_quote": "不在原文的伪造授权" if case == "quote"
                else requirement["requirement"],
                "reason": "离线尝试解除保护"}]}
        scope["constraint_resolution"] = {"constraint_replacements": review[
            "constraint_replacements"] if case == "scope" else []}
        with pytest.raises(ToolError) as raised:
            verify_prompt_constraints([constraint],
                sections={"positive_prompt": "只在框内更换飞鸟"},
                negative_prompt="", edit_scope=scope,
                model_call=lambda _, review=review: json.dumps(review))
        if case != "scope":
            assert raised.value.review_issues[0]["code"] == "replacement_invalid"


def test_cross_instance_claim_prevents_concurrent_paid_submission(review_app, monkeypatch):
    app = review_app
    pid, cid, image = setup_image(app)
    request = {**common(app, pid, cid, "claim-once"), "image": image, "task": "检查主体"}
    entered, release, calls = threading.Event(), threading.Event(), []
    def model(_messages):
        calls.append(True)
        entered.set()
        assert release.wait(10)
        return json.dumps(observation())
    monkeypatch.setattr(app, "_comic_image_vision_model", model)
    second = web_studio.StudioApplication()
    monkeypatch.setattr(second, "_comic_image_vision_model", lambda _: pytest.fail("重复付费"))
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(app.comic_image_operation, pid, "analysis", request)
            try:
                assert entered.wait(10)
                with pytest.raises(ToolError, match="不会自动重发"):
                    second.comic_image_operation(pid, "analysis", request)
            finally:
                release.set()
            result = future.result()
        assert len(calls) == 1
        assert second.comic_image_operation(pid, "analysis", request) == result
    finally:
        second.runner.close()


def test_paid_acceptance_rejects_missing_ledger_and_removes_its_session(monkeypatch, tmp_path):
    import runpy
    import sys

    from kantoku.config.runtime_paths import get_test_runtime_paths
    from kantoku.config.settings import ROOT

    path = tmp_path / "qa.png"
    path.write_bytes(png())
    manager = get_test_runtime_paths()
    before = set((manager.tmp / "sessions").glob("kantoku-paid-acceptance-*"))
    monkeypatch.setattr(sys, "argv", ["verify", "--allow-paid", "--suite", "image-review",
        "--budget-id", "existing-multi-module-budget", "--image", str(path),
        "--task", "检查画面", "--edit-request", "只调整边缘"])
    script = runpy.run_path(str(ROOT / "scripts" / "verify_director_pipeline.py"))
    with pytest.raises(SystemExit) as failure:
        script["run"]()
    assert failure.value.code == 2
    assert set((manager.tmp / "sessions").glob("kantoku-paid-acceptance-*")) == before

def test_workbench_drafts_are_versioned_without_changing_project(review_app, monkeypatch):
    app = review_app
    pid, cid, image = setup_image(app)
    report, _, _ = analyze(app, pid, cid, image, monkeypatch)
    original_version = app.comic_projects.get(pid).project.current_version
    state = {"conversation_id": cid, "request_id": "workspace", "expected_version": 0,
             "image": image, "auto_analyze": False}
    saved = app.save_comic_workbench(pid, state)
    assert saved["version"] == 1
    assert app.save_comic_workbench(pid, state) == saved
    draft = {"conversation_id": cid, "request_id": "draft", "expected_version": 0,
             "image": image, "round_id": "round-1", "task": "检查主体",
             "analysis_artifact_id": report["artifact_id"], "selected_finding_ids": ["selected"],
             "instruction": "仅修改框内", "edit_scope": {"mode": "regions", "regions": [
                 {"region_id": "adjusted", "label": "区域1", "bounds": [0.2, 0.3, 0.6, 0.7],
                  "finding_id": "selected", "instruction": ""}]}}
    stored = app.save_comic_review_draft(image["image_id"], draft)
    assert app.save_comic_review_draft(image["image_id"], draft) == stored
    assert app.comic_review_draft(image["image_id"], 1, cid)["state"]["edit_scope"] == draft[
        "edit_scope"]
    assert app.comic_projects.get(pid).project.current_version == original_version
    with pytest.raises(ToolError, match="其他页面"):
        app.save_comic_review_draft(image["image_id"], {**draft, "request_id": "other"})
    other = app.create_conversation({"interaction_mode": "guided", "domain": "comic"})["id"]
    with pytest.raises(ToolError):
        app.comic_review_draft(image["image_id"], 1, other)


def test_manual_scope_reaches_compiler_and_excludes_unselected_candidates(review_app, monkeypatch):
    app = review_app
    pid, cid, image = setup_image(app)
    report, _, _ = analyze(app, pid, cid, image, monkeypatch)
    seen = []
    def model(messages):
        payload = json.loads(messages[-1]["content"])
        seen.append(payload)
        if isinstance(payload, list):
            return json.dumps({"constraints": [{"source_index": index,
                "source_quote": item["quote"], "target": "选中区域", "scope": "output"}
                for index, item in enumerate(payload)]}, ensure_ascii=False)
        if payload.get("stage") == "constraint_extraction_review":
            return json.dumps({"complete": True, "scope_valid": True,
                "constraint_replacements": [], "findings": [
                {"constraint_id": item["constraint_id"], "status": "covered",
                 "evidence_path": f"constraints.{item['constraint_id']}.source_quote"}
                for item in payload["constraints"]]})
        if "sections" in payload:
            return json.dumps({"complete": True, "scope_valid": True, "findings": [
                {"constraint_id": item["constraint_id"], "status": "covered",
                 "evidence_path": "sections.positive_prompt"}
                for item in payload["constraints"]],
                "edit_findings": [{"constraint_id": item["requirement_id"], "status": "covered",
                    "evidence_path": "sections.positive_prompt"}
                    for item in payload["authorized_requirements"]]})
        return json.dumps({"positive_prompt": "修改明确框选区域，保留其余内容。",
                           "negative_prompt": "", "summary": "离线区域指令"})

    monkeypatch.setattr(app, "_comic_constraint_model", model)
    scope = {"mode": "regions", "regions": [
        {"region_id": "user-box", "label": "手动1", "bounds": [0.25, 0.2, 0.75, 0.6],
         "finding_id": None, "instruction": "调整框内边缘"}]}
    request = {**common(app, pid, cid, "manual-edit"), "image": image, "task": "检查主体",
               "round_id": "manual-round", "edit_scope": scope, "instruction": "只改此区域"}
    manual = app.comic_image_operation(pid, "edit", request)
    assert manual["analysis_artifact_id"] is None
    assert manual["scope"]["protected_features"] == []
    compiled = next(item for item in seen if isinstance(item, dict) and "edit_scope" in item)
    assert compiled["edit_scope"] == scope
    assert compiled["selected_findings"] == []
    assert len(manual["edit_constraints"]) == 2
    selected_scope = {"mode": "regions", "regions": [
        {**scope["regions"][0], "finding_id": "selected"}]}
    request.update(common(app, pid, cid, "selected-edit"))
    selected = app.comic_image_operation(pid, "edit", {**request,
        "analysis_artifact_id": report["artifact_id"], "selected_finding_ids": ["selected"],
        "edit_scope": selected_scope})
    assert [item["finding_id"] for item in selected["scope"]["selected_findings"]] == ["selected"]
    assert "unselected" not in json.dumps(selected["scope"])
    with pytest.raises(ToolError, match="关联"):
        app.comic_image_operation(pid, "edit", {**request,
            **common(app, pid, cid, "invalid-region-link"), "edit_scope": selected_scope})


def test_pending_draft_and_mixed_scope_preserve_explicit_targets(review_app, monkeypatch):
    app = review_app
    pid, cid, image = setup_image(app)
    report, _, _ = analyze(app, pid, cid, image, monkeypatch)
    local = {"region_id": "chosen", "label": "区域1", "finding_id": "selected",
             "bounds": [0.2, 0.2, 0.7, 0.8], "instruction": ""}
    pending = {"mode": "regions", "regions": [local], "whole_finding_ids": [],
               "instruction_target": {"mode": "selected_regions"}}
    draft = {"conversation_id": cid, "request_id": "pending-draft", "expected_version": 0,
             "image": image, "round_id": "mixed-round", "task": "检查主体",
             "analysis_artifact_id": report["artifact_id"],
             "selected_finding_ids": ["selected", "unselected"], "edit_scope": pending}
    assert app.save_comic_review_draft(image["image_id"], draft)["state"]["edit_scope"] == pending
    with pytest.raises(ToolError, match="尚未定位"):
        app.comic_image_operation(pid, "edit", {**common(app, pid, cid, "pending-edit"),
            **{key: value for key, value in draft.items() if key not in {
                "request_id", "expected_version", "conversation_id"}}})
    mixed = {**pending, "mode": "mixed", "whole_finding_ids": ["unselected"],
             "instruction_target": {"mode": "whole"}}
    compiled, request, seen = edit(app, pid, cid, image, report, monkeypatch,
        request_fields={"selected_finding_ids": ["selected", "unselected"],
                        "edit_scope": mixed, "instruction": "柔化整图光线",
                        "round_id": "mixed-round"})
    assert compiled["scope"]["edit_scope"] == mixed
    assert compiled["scope"]["user_instruction"] == "柔化整图光线"
    assert any(isinstance(item, dict) and item.get("edit_scope") == mixed for item in seen)
    count = len(seen)
    assert app.comic_image_operation(pid, "edit", request) == compiled
    assert len(seen) == count


@pytest.mark.parametrize("change", ["unknown", "duplicate", "overlap", "deleted_target"])
def test_invalid_mixed_authorization_never_calls_model(review_app, monkeypatch, change):
    app = review_app
    pid, cid, image = setup_image(app)
    report, _, _ = analyze(app, pid, cid, image, monkeypatch)
    scope = {"mode": "mixed", "regions": [{"region_id": "chosen", "bounds": [0.1, 0.1, 0.8, 0.8],
              "finding_id": "selected"}], "whole_finding_ids": ["unselected"],
             "instruction_target": {"mode": "whole"}}
    if change == "unknown":
        scope["whole_finding_ids"] = ["another-report"]
    elif change == "duplicate":
        scope["whole_finding_ids"] = ["unselected", "unselected"]
    elif change == "overlap":
        scope["whole_finding_ids"] = ["selected", "unselected"]
    else:
        scope["instruction_target"] = {"mode": "region", "region_id": "removed"}
        draft = {"conversation_id": cid, "request_id": "missing-target-draft",
            "expected_version": 0,
            "image": image, "round_id": "invalid-round", "task": "检查主体",
            "analysis_artifact_id": report["artifact_id"],
            "selected_finding_ids": ["selected", "unselected"],
            "edit_scope": scope, "instruction": "调整边缘"}
        assert app.save_comic_review_draft(image["image_id"], draft)["version"] == 1
    with pytest.raises((ToolError, ValidationError)):
        app.comic_image_operation(pid, "edit", {**common(app, pid, cid, "invalid-scope"),
            "image": image, "task": "检查主体", "analysis_artifact_id": report["artifact_id"],
            "selected_finding_ids": ["selected", "unselected"], "edit_scope": scope,
            "instruction": "调整边缘"})


def test_legacy_scope_confirmation_and_request_fingerprint_are_preserved(review_app, monkeypatch):
    app = review_app
    pid, cid, image = setup_image(app)
    report, _, _ = analyze(app, pid, cid, image, monkeypatch)
    scope = {"mode": "regions", "regions": [{"region_id": "legacy", "label": "区域1",
        "bounds": [0.1, 0.1, 0.8, 0.8], "finding_id": "selected", "instruction": ""}]}
    compiled, request, seen = edit(app, pid, cid, image, report, monkeypatch,
        request_fields={"edit_scope": scope, "round_id": "legacy-round"})
    artifact = app.runtime_store.get_artifact(compiled["artifact_id"])
    frozen_metadata = json.dumps(artifact.metadata, sort_keys=True)
    assert compiled["scope"]["edit_scope"] == scope
    app.comic_image_operation(pid, "confirm", {**common(app, pid, cid, "confirm-legacy"),
        "instruction_artifact_id": compiled["artifact_id"]})
    draft = {"conversation_id": cid, "request_id": "legacy-draft", "expected_version": 0,
        "image": image, "round_id": "legacy-round", "task": "检查主体",
        "analysis_artifact_id": report["artifact_id"], "selected_finding_ids": ["selected"],
        "edit_scope": scope, "instruction_artifact_id": compiled["artifact_id"], "phase": "review"}
    saved = app.save_comic_review_draft(image["image_id"], draft)
    assert saved["state"]["edit_scope"] == scope
    assert "dismissed_finding_region_ids" not in saved["state"]
    assert app.save_comic_review_draft(image["image_id"], draft) == saved
    count = len(seen)
    replay = app.comic_image_operation(pid, "edit", request)
    assert replay["artifact_id"] == compiled["artifact_id"] and len(seen) == count
    assert json.dumps(app.runtime_store.get_artifact(artifact.id).metadata,
                      sort_keys=True) == frozen_metadata


def test_missing_file_restore_and_many_versions_use_real_files(review_app):
    app = review_app
    pid, cid, source = setup_image(app)
    image = app.comic_media.get(source["image_id"])
    path = Path(app.runtime_store.get_artifact(image.artifact_id).location)
    content = path.read_bytes()
    from kantoku.config.runtime_paths import get_runtime_paths
    get_runtime_paths().remove_file(path, file_type="artifact")
    restore = {**common(app, pid, cid, "restore-original"), "image": source,
               "filename": "original.png",
               "data_url": "data:image/png;base64," + base64.b64encode(content).decode()}
    wrong = {**restore, "request_id": "different-file",
             "data_url": "data:image/png;base64," + base64.b64encode(png(200)).decode()}
    with pytest.raises(ToolError, match="内容不同"):
        app.restore_comic_image_file(source["image_id"], wrong)
    assert not path.exists()
    result = app.restore_comic_image_file(source["image_id"], restore)
    assert result["restored"] and path.read_bytes() == content
    from kantoku.config.runtime_paths import atomic_write_bytes
    with pytest.raises(FileExistsError):
        atomic_write_bytes(path, png(99), replace=False)
    assert path.read_bytes() == content
    assert app.restore_comic_image_file(source["image_id"], restore) == result
    for number in range(2, 9):
        upload(app, pid, cid, color=number, image_id=source["image_id"],
               expected_image_version=number - 1, parent=source)
    page = app.comic_image_versions(source["image_id"], cid, limit=3)
    assert [item["version"] for item in page["versions"]] == [8, 7, 6]
    older = app.comic_image_versions(source["image_id"], cid, before=page["next_before"], limit=3)
    assert [item["version"] for item in older["versions"]] == [5, 4, 3]
    assert all(item["parent"] == source for item in page["versions"])
    assert path.read_bytes() == content
    assert app.comic_media.list(pid)[0]["selection"] is None



def _legacy_family(app, pid, cid, *, color=0):
    """旧分镜上传：图片以 Artifact ID 充当 image_id，v1 只是它的只读投影。"""
    imported = upload(app, pid, cid, color=color, request_id=f"legacy-upload-{color}")
    original = app.comic_media.get(imported["image_id"])
    artifact = app.runtime_store.get_artifact(original.artifact_id).model_copy(update={
        "id": f"artifact-legacy-{color}", "source": "comic.external_image",
        "metadata": {"origin": "real", "creation_mode": "external_upload", "project_id": pid,
                     "shot_id": f"legacy-shot-{color}", "prompt_artifact_id": "legacy-prompt",
                     "sha256": original.sha256, "filename": "original.png"}})
    app.runtime_store.save_artifact(artifact)
    return artifact, {"image_id": artifact.id, "version": 1}


@pytest.mark.parametrize("lifecycle", ["trashed", "purged"])
def test_retired_legacy_family_can_be_removed_without_reactivating_its_file(review_app, lifecycle):
    app = review_app
    pid, cid, kept = setup_image(app)
    legacy, ref = _legacy_family(app, pid, cid, color=41)
    app.select_comic_image(legacy.id, {
        **common(app, pid, cid, "adopt-retired"), "version": 1, "expected_selection_version": 0})
    trashed = app.change_core_artifact(legacy.id, "trash", {"expected_lifecycle_version": 1})
    if lifecycle == "purged":
        app.change_core_artifact(legacy.id, "purge", {
            "expected_lifecycle_version": trashed["lifecycle_version"]})
    family = next(item for item in app.list_comic_images(pid, cid)["images"]
                  if item["image_id"] == legacy.id)
    assert family["available"] is False and family["selection"]["available"] is False
    removed = app.delete_comic_image(pid, legacy.id, common(app, pid, cid, "remove-retired"))
    assert removed["image_id"] == legacy.id
    assert not app.comic_stories.conversation_candidates(cid)["images"]
    assert app.runtime_store.get_artifact(legacy.id).lifecycle_state.value == lifecycle
    assert legacy.id not in {item["image_id"] for item in app.comic_media.list(pid)}
    assert app.comic_media.require_image(ImageVersionRef.model_validate(kept), pid, cid)
    with pytest.raises(ToolError, match="找不到指定图片版本"):
        app.comic_media.get(ref["image_id"], version=1, _legacy_history=True)


def test_reports_are_scoped_to_content_not_to_a_reused_version_number(review_app, monkeypatch):
    """版本号可以被复用（删除后重新导入），报告归属必须看内容指纹。"""
    app = review_app
    pid, cid, _imported = setup_image(app)
    legacy, image_v1 = _legacy_family(app, pid, cid, color=0)
    report, _, _ = analyze(app, pid, cid, image_v1, monkeypatch, request_id="analyze-legacy")
    # 同一图片再追加一版不同内容：v2 的 sha 与 v1 不同。
    upload(app, pid, cid, color=7, image_id=legacy.id, expected_image_version=1,
           parent=image_v1, request_id="legacy-v2")

    # 把 v1 的报告错误地挂到 v2 上（旧实现只看 image_id+version，会把它当成 v2 的分析）。
    artifact = app.runtime_store.get_artifact(report["artifact_id"])
    metadata = dict(artifact.metadata)
    payload = json.loads(json.dumps(metadata["payload"]))
    payload["basis"]["source_image"]["version"] = 2
    metadata.update(payload=payload, sha256=value_hash(payload))
    app.runtime_store.save_artifact(artifact.model_copy(
        update={"id": "artifact-stale-version", "metadata": metadata}))

    assert app.comic_image_reports(pid, cid, "analysis", image=ImageVersionRef(
        image_id=legacy.id, version=2))["items"] == []
    # 原图（v1）自己的报告仍然可取。
    assert [item["artifact_id"] for item in app.comic_image_reports(
        pid, cid, "analysis", image=ImageVersionRef.model_validate(image_v1))["items"]] == [
            report["artifact_id"]]


def test_deleting_a_legacy_image_trashes_its_artifact_so_it_cannot_reappear(review_app):
    """不回收旧产物，删掉的图片下次读取会复活成 v1，新导入的图会被当成它的 v2。"""
    app = review_app
    pid, cid, _imported = setup_image(app)
    legacy, image = _legacy_family(app, pid, cid, color=3)
    assert legacy.id in [item["image_id"] for item in app.comic_media.list(pid)]

    app.delete_comic_image(pid, legacy.id, common(app, pid, cid, "delete-legacy"))

    assert legacy.id not in [item["image_id"] for item in app.comic_media.list(pid)]
    assert app.runtime_store.get_artifact(
        legacy.id).lifecycle_state == ArtifactLifecycle.TRASHED
    with pytest.raises(ToolError, match="找不到指定图片版本"):
        app.comic_media.get(legacy.id)
    # 追加版本的旧路径必须失败，否则新图会被写进已删除的图片家族。
    with pytest.raises(ToolError, match="找不到指定图片版本"):
        upload(app, pid, cid, color=9, image_id=legacy.id, expected_image_version=1,
               parent=image)


@pytest.mark.parametrize("lifecycle", ["trashed", "purged"])
@pytest.mark.parametrize("retire_revision", [False, True])
def test_retired_legacy_source_keeps_work_list_and_version_history_readable(
    review_app, lifecycle, retire_revision,
):
    """全局回收/清除旧原图不应让整份作品列表崩溃，也不能复活已删除文件。"""
    app = review_app
    parent = app.comic_workspace.create({"request_id": "source-work", "title": "Source work"})
    child = app.comic_workspace.create_task(parent["work_id"], {
        "request_id": "source-task", "expected_version": parent["version"],
        "title": "Existing image", "requirement": "Keep this image's original provenance."})
    pid, cid = child["task_id"], child["conversation_id"]
    legacy, first = _legacy_family(app, pid, cid, color=21)
    revised = upload(app, pid, cid, color=22, image_id=legacy.id, expected_image_version=1,
                     parent=first, request_id="legacy-second-version")
    original_brief = app.comic_media.image_brief_version(legacy.id)
    other = app.comic_workspace.create({"request_id": "other-work", "title": "Unaffected work"})
    before = [item.model_dump(mode="json") for item in app.comic_media.versions(legacy.id)]

    for artifact_id in [legacy.id, *([revised["artifact_id"]] if retire_revision else [])]:
        artifact = app.runtime_store.get_artifact(artifact_id)
        removed = app.change_core_artifact(artifact_id, "trash", {
            "expected_lifecycle_version": artifact.lifecycle_version})
        if lifecycle == "purged":
            app.change_core_artifact(artifact_id, "purge", {
                "expected_lifecycle_version": removed["lifecycle_version"]})

    # 正常图片入口仍拒绝停用的旧图；列目录和读取历史不要求旧文件仍可用。
    with pytest.raises(ToolError, match="找不到指定图片版本"):
        app.comic_media.get(legacy.id, version=1)
    works = app.comic_workspace.list()["works"]
    assert {item["work_id"] for item in works} == {parent["work_id"], other["work_id"]}
    assert next(item for item in works if item["work_id"] == parent["work_id"])["tasks"]
    listed = app.list_comic_images(pid, cid)["images"]
    image = next(item for item in listed if item["image_id"] == legacy.id)
    assert image["version"] == 2 and image["label"] == "original.png"
    assert image["source_brief_version"] == original_brief == 1
    history = app.comic_image_versions(legacy.id, cid)["versions"]
    assert [{key: item[key] for key in before[0]} for item in history] == before
    assert [item["available"] for item in history] == [not retire_revision, False]
    assert history[-1]["lifecycle_state"] == lifecycle
    assert app.runtime_store.get_artifact(legacy.id).lifecycle_state.value == lifecycle
    with pytest.raises(ToolError):
        app.comic_media.require_image(ImageVersionRef.model_validate(first), pid, cid)
    if retire_revision:
        with pytest.raises(ToolError, match="图片产物已失效"):
            app.comic_media.require_image(ImageVersionRef.model_validate({
                "image_id": legacy.id, "version": 2}), pid, cid)
    else:
        current = app.comic_media.require_image(ImageVersionRef.model_validate({
            "image_id": legacy.id, "version": 2}), pid, cid)
        assert Path(app.runtime_store.get_artifact(current.artifact_id).location).is_file()

    # 用真实 HTTP 验证原报错入口；GET 不改故事、图片版本或生命周期。
    server = web_studio.make_server(app)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        connection.request("GET", "/api/comic/works", headers={"X-Studio-Token": app.token})
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["works"] == works
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert [item.model_dump(mode="json") for item in app.comic_media.versions(legacy.id)] == before

    # 用户明确移除整个版本链后，连历史投影也不能把旧图重新带回来。
    app.delete_comic_image(pid, legacy.id, common(app, pid, cid, "remove-retired-family"))
    assert legacy.id not in [item["image_id"] for item in app.comic_media.list(pid)]
    with pytest.raises(ToolError, match="找不到指定图片版本"):
        app.comic_media.get(legacy.id, version=1, _legacy_history=True)


@pytest.mark.parametrize("lifecycle", ["trashed", "purged"])
@pytest.mark.parametrize("retire_revision", [False, True])
def test_global_retirement_of_adopted_legacy_version_does_not_break_story_candidates(
    review_app, lifecycle, retire_revision,
):
    """采用的是停用 v1；有效 v2 与其他图片都不能掩盖或放大这个失效引用。"""
    app = review_app
    parent = app.comic_workspace.create({"request_id": "candidate-work", "title": "Candidate work"})
    child = app.comic_workspace.create_task(parent["work_id"], {
        "request_id": "candidate-task", "expected_version": parent["version"],
        "title": "Source frames",
        "requirement": "Only explicitly adopted frames enter this story."})
    pid, cid = child["task_id"], child["conversation_id"]
    legacy, first = _legacy_family(app, pid, cid, color=61)
    revised = upload(app, pid, cid, color=62, image_id=legacy.id, expected_image_version=1,
                     parent=first, request_id="candidate-revision")
    kept = upload(app, pid, cid, color=63, request_id="candidate-kept")
    for ref in (first, {"image_id": kept["image_id"], "version": 1}):
        app.select_comic_image(ref["image_id"], {
            **common(app, pid, cid, "adopt-" + ref["image_id"]),
            "version": ref["version"], "expected_selection_version": 0})
    original_versions = [item.model_dump(mode="json")
                         for item in app.comic_media.versions(legacy.id)]
    selection = app.comic_image_versions(legacy.id, cid)["selection"]
    workbench = {"conversation_id": cid, "request_id": "candidate-preference",
                 "expected_version": 0, "image": {"image_id": legacy.id, "version": 2},
                 "edit_image": first, "auto_analyze": False}
    saved_workbench = app.save_comic_workbench(pid, workbench)
    draft = {"conversation_id": cid, "request_id": "candidate-draft", "expected_version": 0,
             "image": first, "round_id": "retained-local-round", "task": "Retain this draft.",
             "selected_finding_ids": [], "instruction": "Keep this saved local requirement."}
    saved_draft = app.save_comic_review_draft(legacy.id, draft)

    retired = [legacy.id, *([revised["artifact_id"]] if retire_revision else [])]
    for artifact_id in retired:
        artifact = app.runtime_store.get_artifact(artifact_id)
        trashed = app.change_core_artifact(artifact_id, "trash", {
            "expected_lifecycle_version": artifact.lifecycle_version})
        if lifecycle == "purged":
            app.change_core_artifact(artifact_id, "purge", {
                "expected_lifecycle_version": trashed["lifecycle_version"]})

    candidates = app.comic_work_story(parent["work_id"], "images")["images"]
    by_image = {item["image_id"]: item for item in candidates}
    assert set(by_image) == {legacy.id, kept["image_id"]}
    assert by_image[legacy.id]["version"] == 1
    assert by_image[legacy.id]["selection"]["selected_version"] == 1
    assert by_image[legacy.id]["available"] is False
    assert by_image[legacy.id]["unavailable_reason"]
    assert by_image[legacy.id]["lifecycle_state"] == lifecycle
    assert by_image[kept["image_id"]]["available"] is True
    assert by_image[kept["image_id"]]["project_id"] == pid
    with pytest.raises(ToolError):
        app.comic_media.require_image(ImageVersionRef.model_validate(first), pid, cid)
    assert app.comic_media.require_image(ImageVersionRef(
        image_id=kept["image_id"], version=1), pid, cid).artifact_id == kept["artifact_id"]

    # 页面实际请求的作品级候选接口必须成功，不能因一个悬空采用指针整组报错。
    server = web_studio.make_server(app)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        connection.request("GET", f"/api/comic/works/{parent['work_id']}/story/images",
                           headers={"X-Studio-Token": app.token})
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200, payload
        assert {item["image_id"] for item in payload["images"]} == set(by_image)
        assert next(item for item in payload["images"]
                    if item["image_id"] == legacy.id)["available"] is False
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert [item.model_dump(mode="json") for item in app.comic_media.versions(legacy.id)] == \
        original_versions
    current_selection = app.comic_image_versions(legacy.id, cid)["selection"]
    assert current_selection["selected_version"] == selection["selected_version"] == 1
    assert current_selection["selection_version"] == selection["selection_version"]
    assert app.comic_workbench_state(pid, cid)["state"] == saved_workbench["state"]

    if lifecycle == "trashed":
        for artifact_id in retired:
            artifact = app.runtime_store.get_artifact(artifact_id)
            app.change_core_artifact(artifact_id, "restore", {
                "expected_lifecycle_version": artifact.lifecycle_version})
        restored = app.comic_work_story(parent["work_id"], "images")["images"]
        original = next(item for item in restored if item["image_id"] == legacy.id)
        assert original["version"] == 1 and original["available"] is True
        assert original["selection"]["selected_version"] == 1
        assert app.comic_review_draft(legacy.id, 1, cid)["state"] == saved_draft["state"]
        assert app.comic_media.require_image(ImageVersionRef.model_validate(first), pid, cid)
    else:
        artifact = app.runtime_store.get_artifact(legacy.id)
        with pytest.raises(ToolError):
            app.change_core_artifact(legacy.id, "restore", {
                "expected_lifecycle_version": artifact.lifecycle_version})
