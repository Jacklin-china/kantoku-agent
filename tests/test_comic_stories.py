"""Story revisions and exported media contracts; every model is an offline substitute."""

import base64
import copy
import json
import struct
import threading
import zlib
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest
from pydantic import ValidationError

from kantoku.config import LLMError, ToolError, get_settings
from kantoku.config.runtime_paths import atomic_write_bytes
from kantoku.core import llm
from kantoku.core.runtime.models import ArtifactLifecycle
from kantoku.domains.comic.models import ComicStoryDraft
from kantoku.shells import web_studio
from kantoku.tools.image_gen import LocalFakeImageProvider


def _png(color=0):
    def chunk(name, content):
        return (struct.pack(">I", len(content)) + name + content
                + struct.pack(">I", zlib.crc32(name + content)))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes([0, color, 0, 0])))
            + chunk(b"IEND", b""))


def _forbidden(*_args, **_kwargs):
    pytest.fail("Story offline tests must never call an unmocked paid model")


@pytest.fixture
def story_app(monkeypatch, tmp_path):
    """Use the application's real domain stores and one managed, isolated database."""
    from kantoku.core.runtime import storage

    settings = get_settings().model_copy(deep=True)
    settings.storage.sqlite_path = tmp_path / "stories.db"
    settings.image.output_dir = tmp_path / "images"
    monkeypatch.setattr(web_studio, "get_settings", lambda: settings)
    monkeypatch.setattr(storage, "get_settings", lambda: settings)
    monkeypatch.setattr(web_studio, "_provider", lambda: LocalFakeImageProvider(
        tmp_path / "fake", model_id="offline-story-test", actual_fen=0))
    for module in (llm, web_studio):
        monkeypatch.setattr(module, "chat", _forbidden)
        monkeypatch.setattr(module, "vision_chat", _forbidden)
    application = web_studio.StudioApplication()
    def continuity_only(messages):
        content = messages[1]["content"]
        if not isinstance(content, list):
            return _forbidden()
        context = json.loads(content[0]["text"])
        if context.get("stage") != "visual_continuity":
            return _forbidden()
        # Existing routing tests claim no pixel observations. Dedicated continuity
        # tests inject visible evidence and verify its exact bytes and preservation.
        return json.dumps({"elements": [{"element_id": item["element_id"],
            "target": item["target"], "field": item["field"], "observation": "",
            "visibility": "uncertain"} for item in context["established_elements"]],
            "limitations": ["Offline routing substitute; no pixel facts asserted."]})

    monkeypatch.setattr(application, "_comic_image_vision_model", continuity_only)
    monkeypatch.setattr(application, "_comic_constraint_model", _forbidden)
    try:
        yield application
    finally:
        application.runner.close()


def _conversation(app):
    return app.create_conversation({"interaction_mode": "guided", "domain": "comic"})["id"]


def _project(app, instruction="Independent offline image"):
    return app.create_comic_project({"title": "Offline source", "brief": {
        "original_request": instruction}})["project"]["project_id"]


def _media_request(app, pid, cid, key):
    return {"conversation_id": cid, "request_id": key,
            "expected_project_version": app.comic_projects.get(pid).project.current_version}


def _upload(app, pid, cid, *, content=None, mime="image/png", filename="source.png", **fields):
    content = _png() if content is None else content
    return app.import_comic_image(pid, {
        **_media_request(app, pid, cid, f"import-{uuid4().hex}"), "filename": filename,
        "data_url": f"data:{mime};base64," + base64.b64encode(content).decode(), **fields})


def _ref(image):
    return {"image_id": image["image_id"], "version": image["version"]}


def _select(app, cid, image, *, expected=0):
    return app.select_comic_image(image["image_id"], {
        **_media_request(app, image["project_id"], cid, f"select-{uuid4().hex}"),
        "version": image["version"], "expected_selection_version": expected})


def _setup(app):
    cid = _conversation(app)
    pid = _project(app)
    image = _upload(app, pid, cid)
    _select(app, cid, image)
    return pid, cid, image


def _request(app, pid, cid, key, *, version=None):
    if version is None:
        story = app.get_comic_story(cid)["story"]
        version = story["version"] if story else 0
    return {**_media_request(app, pid, cid, key), "anchor_project_id": pid,
            "expected_story_version": version}


def _draft(*images, title="Offline bridge sequence"):
    return {"title": title, "summary": "An editable sequence based on explicitly selected images.",
            "instruction": "Preserve the supplied image identities.", "shots": [
                {"shot_id": f"story-shot-{index}", "image": _ref(image),
                 "action": f"A small visible movement in shot {index}.",
                 "camera": "Camera remains still.", "duration_seconds": 4.0,
                 "transition": "Cut."} for index, image in enumerate(images, 1)]}


def _timing_plan(shot):
    """Explicit offline reviewer estimate; covers both existing execution fields."""
    return [{"field": "action", "evidence": shot["action"][:1000], "seconds": 5.0},
            {"field": "transition", "evidence": shot["transition"][:1000], "seconds": 1.0}]


def _save(app, pid, cid, draft, key="save"):
    return app.comic_story_operation(cid, "save", {
        **_request(app, pid, cid, key), "draft": draft})


def _story_draft(story):
    return {key: story[key] for key in ("title", "summary", "instruction", "shots")}


def _review(app, pid, cid, monkeypatch, *, output=None, key="review"):
    seen = []

    def model(messages):
        seen.append(messages)
        return json.dumps(output or {"status": "pass", "findings": []})

    monkeypatch.setattr(app, "_comic_constraint_model", model)
    result = app.comic_story_operation(cid, "review", _request(app, pid, cid, key))
    return result, seen


def _confirm(app, pid, cid, monkeypatch, *, key="confirm"):
    _review(app, pid, cid, monkeypatch, key=key + "-review")
    return app.comic_story_operation(cid, "confirm", _request(app, pid, cid, key))


def test_fixed_versions_across_projects_survive_reselection_and_reload(story_app):
    app = story_app
    pid, cid, first = _setup(app)
    newer = _upload(app, pid, cid, content=_png(31), image_id=first["image_id"],
                    expected_image_version=1, parent=_ref(first))
    related_pid = _project(app, "A separate mechanical subject")
    second = _upload(app, related_pid, cid, content=_png(52))
    _select(app, cid, second)
    candidates = app.comic_story_images(cid)["images"]
    assert {_ref(item)["image_id"]: item["version"] for item in candidates} == {
        first["image_id"]: 1, second["image_id"]: 1}
    saved = _save(app, pid, cid, _draft(second, first, second))
    assert saved["story"]["shots"][1]["image"] == _ref(first)
    assert len(saved["story"]["fixed_images"]) == 2
    _select(app, cid, newer, expected=1)
    restored = app.get_comic_story(cid)
    assert restored["story"] == saved["story"]
    assert restored["story"]["shots"][0]["image"] == _ref(second)
    # A new service instance must rebuild the story from the original database.
    from kantoku.domains.comic.stories import ComicStoryService
    reloaded = ComicStoryService(app.comic_media).view(cid)
    assert reloaded["story"] == saved["story"]
    edited = _save(app, pid, cid, _draft(second, first, second, title="Reuse fixed old version"),
                   "reuse-old-version")
    assert edited["story"]["shots"][1]["image"] == _ref(first)


def test_cross_conversation_and_unadopted_images_cannot_enter_story(story_app):
    app = story_app
    pid, cid, first = _setup(app)
    _save(app, pid, cid, _draft(first))
    original = app.get_comic_story(cid)["story"]
    other_cid = _conversation(app)
    other_pid = _project(app)
    foreign = _upload(app, other_pid, other_cid)
    _select(app, other_cid, foreign)
    unadopted = _upload(app, pid, cid, content=_png(11))
    for index, image in enumerate((foreign, unadopted)):
        with pytest.raises(ToolError):
            _save(app, pid, cid, _draft(image), f"invalid-scope-{index}")
        assert app.get_comic_story(cid)["story"] == original


@pytest.mark.parametrize("modern", [False, True])
def test_story_uses_frozen_artifact_when_a_historical_image_version_is_reused(
    story_app, monkeypatch, modern,
):
    app = story_app
    if modern:
        work = app.comic_workspace.create({"request_id": uuid4().hex, "title": "Snapshot story"})
        task = app.comic_workspace.create_task(work["work_id"], {
            "request_id": uuid4().hex, "expected_version": work["version"],
            "title": "Original image", "requirement": "Original image"})
        pid, cid = task["task_id"], task["conversation_id"]
        image = _upload(app, pid, cid)
        _select(app, cid, image)
    else:
        pid, cid, image = _setup(app)
    original = _upload(app, pid, cid, content=_png(31), image_id=image["image_id"],
                       expected_image_version=1, parent=_ref(image))
    _select(app, cid, original, expected=1)

    def read():
        return app.comic_work_story(work["work_id"]) if modern else app.get_comic_story(cid)

    def mutate(kind, **fields):
        if modern:
            return app.comic_work_story(work["work_id"], kind, {
                "request_id": uuid4().hex,
                "expected_project_version": app.comic_workspace.get(work["work_id"])["version"],
                "expected_story_version": read()["story_version"], **fields})
        return app.comic_story_operation(cid, kind, {
            **_request(app, pid, cid, uuid4().hex), **fields})

    saved = mutate("save", draft=_draft(original))
    replacement = _upload(app, pid, cid, content=_png(81))
    # 仅在隔离测试库回放历史损坏：相同 image_id/version 后来指向了另一份内容。
    reused = {**app.comic_media.get(replacement["image_id"]).model_dump(mode="json"),
              "image_id": original["image_id"], "version": 2}
    with app.comic_projects._connect() as connection:
        connection.execute(
            "UPDATE comic_entity_versions SET payload_json=? "
            "WHERE project_id=? AND entity_type='image' AND entity_id=? AND version=2",
            (json.dumps(reused), pid, original["image_id"]),
        )
    assert app.comic_media.get(original["image_id"], version=2).sha256 == replacement["sha256"]
    assert read()["story"] == saved["story"] and not read()["missing_images"]
    assert mutate("save", draft=_draft(original))["story"] == saved["story"]
    pixels = []

    def vision(messages):
        pixels.extend(base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
                      for part in messages[-1]["content"] if part["type"] == "image_url")
        return json.dumps(_draft(original))

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model",
                        lambda _: json.dumps({"status": "pass", "findings": []}))
    generated = mutate("generate", images=[_ref(original)], instruction="One original shot.")
    assert generated["story"]["fixed_images"] == saved["story"]["fixed_images"]
    assert pixels == [_png(31)]
    mutate("review")
    mutate("confirm")
    exported = mutate("exports")
    artifact = app.runtime_store.get_artifact(exported["export_artifact_id"])
    with ZipFile(artifact.location) as bundle:
        filename = exported["manifest"]["images"][0]["filename"]
        assert bundle.read(filename) == _png(31)
    # 快照也必须实际验哈希，不能因为采用新读取方式而放行损坏的原图。
    old_artifact = app.runtime_store.get_artifact(original["artifact_id"])
    atomic_write_bytes(Path(old_artifact.location), _png(200), owner="offline.story.tamper")
    assert read()["missing_images"]
    with pytest.raises(ToolError):
        mutate("exports")


@pytest.mark.parametrize("duration", ["4s", float("inf"), float("nan")])
def test_duration_and_duplicate_identity_are_checked_as_structural_contract(duration):
    draft = _draft({"image_id": "offline-image", "version": 1})
    draft["shots"][0]["duration_seconds"] = duration
    with pytest.raises(ValidationError):
        ComicStoryDraft.model_validate(draft)
    draft["shots"][0]["duration_seconds"] = 4.0
    draft["shots"].append(copy.deepcopy(draft["shots"][0]))
    with pytest.raises(ValidationError):
        ComicStoryDraft.model_validate(draft)


@pytest.mark.parametrize("duration", [0, -1, 61, 0.05])
def test_invalid_duration_is_saved_then_located_by_validation(story_app, monkeypatch, duration):
    app = story_app
    pid, cid, image = _setup(app)
    draft = _draft(image)
    draft["shots"][0]["duration_seconds"] = duration
    _save(app, pid, cid, draft)
    monkeypatch.setattr(app, "_comic_constraint_model", _forbidden)
    checked = app.comic_story_operation(cid, "review", _request(app, pid, cid, "duration-check"))
    assert checked["review"]["status"] == "needs_revision"
    finding = checked["review"]["findings"][0]
    assert (finding["shot_id"], finding["field"], finding["code"]) == (
        "story-shot-1", "duration_seconds", "duration_conflict")
    assert "0.1–60" in finding["suggestion"]
    with pytest.raises(ToolError, match="尚未通过校验"):
        app.comic_story_operation(cid, "confirm", _request(app, pid, cid, "invalid-duration"))


def test_content_edit_clears_confirmation_restore_appends_and_stale_write_fails(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    v1 = _save(app, pid, cid, _draft(image))
    confirmed = _confirm(app, pid, cid, monkeypatch)
    assert confirmed["confirmation"]
    stale = {**_request(app, pid, cid, "stale"), "draft": _draft(image, title="Stale copy")}
    v2 = _save(app, pid, cid, _draft(image, title="Edited title"), "save-2")
    assert v2["story"]["version"] == 2
    assert not v2["confirmation"]
    with pytest.raises(ToolError):
        app.comic_story_operation(cid, "save", stale)
    restored = app.comic_story_operation(cid, "restore", {
        **_request(app, pid, cid, "restore"), "version": 1})
    assert restored["story"]["version"] == 3
    assert _story_draft(restored["story"]) == _story_draft(v1["story"])
    assert not restored["confirmation"]
    assert app.get_comic_story(cid, version=1)["story"] == v1["story"]


def test_incomplete_draft_is_saved_but_not_confirmed_and_review_invalidates_old_confirmation(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    incomplete = _draft(image)
    incomplete["shots"][0]["duration_seconds"] = None
    saved = _save(app, pid, cid, incomplete)
    assert saved["story"]["shots"][0]["duration_seconds"] is None
    with pytest.raises(ToolError):
        app.comic_story_operation(cid, "confirm", _request(app, pid, cid, "incomplete-confirm"))
    _save(app, pid, cid, _draft(image), "complete-fields")
    _confirm(app, pid, cid, monkeypatch)
    warning = {"status": "warning", "findings": [{"severity": "warning",
        "shot_id": "story-shot-1", "field": "camera", "message": "Consider another angle.",
        "evidence": "Camera remains still."}]}
    # 内容没改：复核沿用原结论与确认，不会"上次通过、这次不通过"。
    _review(app, pid, cid, monkeypatch, output=warning, key="same-content-review")
    assert app.get_comic_story(cid)["confirmation"]
    # 真的改了内容：旧结论失效，新的校验结果会覆盖原来的确认。
    _save(app, pid, cid, _draft(image, title="改过的标题"), "edited-after-confirm")
    reviewed, _ = _review(app, pid, cid, monkeypatch, output=warning, key="new-review")
    assert not reviewed["confirmation"]
    assert reviewed["review"]["status"] == "warning"
    assert not app.get_comic_story(cid)["confirmation"]


def test_first_story_anchor_is_claimed_once_under_two_projects(story_app):
    app = story_app
    pid, cid, first = _setup(app)
    other_pid = _project(app)
    second = _upload(app, other_pid, cid, content=_png(40))
    _select(app, cid, second)
    requests = [(pid, {**_request(app, pid, cid, "anchor-a"), "draft": _draft(first)}),
                (other_pid, {**_request(app, other_pid, cid, "anchor-b"),
                             "draft": _draft(second)})]
    barrier = threading.Barrier(2)

    def save(item):
        project_id, request = item
        barrier.wait(timeout=10)
        try:
            result = app.comic_story_operation(cid, "save", request)
            return project_id, result
        except ToolError:
            return project_id, None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, requests))
    winners = [(project_id, result) for project_id, result in results if result is not None]
    assert len(winners) == 1
    assert app.get_comic_story(cid)["story"]["anchor_project_id"] == winners[0][0]
    assert len(app.comic_story_versions(cid)["versions"]) == 1


def test_ai_draft_receives_selected_pixels_only_and_replay_makes_no_calls(story_app, monkeypatch):
    app = story_app
    pid, cid, first = _setup(app)
    other_pid = _project(app, "Selected second source")
    second = _upload(app, other_pid, cid, content=_png(71))
    _select(app, cid, second)
    excluded_pid = _project(app, "UNSELECTED_SOURCE_MUST_NOT_ENTER_STORY")
    excluded = _upload(app, excluded_pid, cid, content=_png(93))
    _select(app, cid, excluded)
    draft = _draft(second, first)
    draft["instruction"] = "Two independent subjects."
    vision_calls, review_calls = [], []

    def vision(messages):
        vision_calls.append(messages)
        return json.dumps(draft)

    def review(messages):
        review_calls.append(messages)
        return json.dumps({"status": "pass", "findings": []})

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    request = {**_request(app, pid, cid, "generate-pixels"),
               "images": [_ref(second), _ref(first)], "instruction": "Two independent subjects."}
    result = app.comic_story_operation(cid, "generate", request)
    assert _story_draft(result["story"]) == draft
    urls = [part["image_url"]["url"] for message in vision_calls[0]
            if isinstance(message["content"], list) for part in message["content"]
            if part["type"] == "image_url"]
    assert len(urls) == 2
    assert {base64.b64decode(url.split(",", 1)[1]) for url in urls} == {_png(), _png(71)}
    public_input = json.dumps(vision_calls + review_calls, ensure_ascii=False)
    assert "UNSELECTED_SOURCE_MUST_NOT_ENTER_STORY" not in public_input
    assert excluded["image_id"] not in public_input
    assert len(vision_calls) == len(review_calls) == 1
    monkeypatch.setattr(app, "_comic_image_vision_model", _forbidden)
    monkeypatch.setattr(app, "_comic_constraint_model", _forbidden)
    replay = app.comic_story_operation(cid, "generate", request)
    assert replay["story"] == result["story"]
    assert app.get_comic_story(cid)["story"] == result["story"]


@pytest.mark.parametrize("failure", ["bad-json", "unknown-image", "provider"])
def test_failed_generation_preserves_saved_story_and_replay_never_resubmits(
    story_app, monkeypatch, failure,
):
    app = story_app
    pid, cid, image = _setup(app)
    original = _save(app, pid, cid, _draft(image))["story"]
    calls = []

    def model(messages):
        calls.append(messages)
        if failure == "provider":
            raise ToolError("Offline provider result unavailable")
        if failure == "bad-json":
            return "not valid JSON"
        draft = _draft(image)
        draft["instruction"] = "A small independent sequence."
        draft["shots"][0]["image"]["image_id"] = "unknown-image"
        return json.dumps(draft)

    monkeypatch.setattr(app, "_comic_image_vision_model", model)
    request = {**_request(app, pid, cid, "unavailable-generation"),
               "images": [_ref(image)], "instruction": "A small independent sequence."}
    with pytest.raises(ToolError):
        app.comic_story_operation(cid, "generate", request)
    assert app.get_comic_story(cid)["story"] == original
    with pytest.raises(ToolError):
        app.comic_story_operation(cid, "generate", request)
    assert len(calls) == 1


def test_review_ids_and_warnings_cannot_be_bypassed(story_app, monkeypatch):
    app = story_app
    pid, cid, image = _setup(app)
    _save(app, pid, cid, _draft(image))
    finding = {"severity": "warning", "shot_id": "story-shot-1", "field": "camera",
               "message": "Offline artistic suggestion", "evidence": "Camera remains still."}
    with pytest.raises(ToolError):
        _review(app, pid, cid, monkeypatch, output={"status": "warning", "findings": [
            {**finding, "shot_id": "unknown-shot"}]}, key="invalid-review")
    assert not app.get_comic_story(cid)["confirmation"]
    result, _ = _review(app, pid, cid, monkeypatch,
                       output={"status": "warning", "findings": [finding]}, key="valid-warning")
    assert result["review"]["status"] == "warning"
    with pytest.raises(ToolError):
        app.comic_story_operation(cid, "confirm", _request(app, pid, cid, "reject-warning"))
    accepted = app.comic_story_operation(cid, "confirm", {
        **_request(app, pid, cid, "accept-warning"), "accept_warnings": True})
    assert accepted["confirmation"]
    assert accepted["review"]["status"] == "warning"


def test_submitted_request_does_not_issue_parallel_model_call(story_app, monkeypatch):
    from kantoku.domains.comic.models import ComicStoryGenerateRequest
    from kantoku.domains.comic.stories import ComicStoryService

    app = story_app
    pid, cid, image = _setup(app)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def model(messages):
        calls.append(messages)
        entered.set()
        assert release.wait(timeout=15)
        draft = _draft(image)
        draft["instruction"] = "Keep the fixed image."
        return json.dumps(draft)

    monkeypatch.setattr(app, "_comic_image_vision_model", model)
    monkeypatch.setattr(app, "_comic_constraint_model",
                        lambda _: json.dumps({"status": "pass", "findings": []}))
    request = {**_request(app, pid, cid, "pending-generation"),
               "images": [_ref(image)], "instruction": "Keep the fixed image."}
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(app.comic_story_operation, cid, "generate", request)
        try:
            assert entered.wait(timeout=10)
            with pytest.raises(ToolError):
                ComicStoryService(app.comic_media).generate(
                    ComicStoryGenerateRequest.model_validate(request),
                    vision_model_call=_forbidden, model_call=_forbidden,
                    model="offline", vision_model="offline", run_id="unexecuted-replay")
        finally:
            release.set()
        assert pending.result(timeout=15)["story"]
    assert len(calls) == 1


def test_late_generation_is_retained_as_candidate_without_overwriting_or_recalling_model(
    story_app, monkeypatch,
):
    from kantoku.domains.comic.models import ComicStorySaveRequest
    from kantoku.domains.comic.stories import ComicStoryService

    app = story_app
    pid, cid, image = _setup(app)
    _save(app, pid, cid, _draft(image))
    entered, release = threading.Event(), threading.Event()
    candidate = _draft(image, title="Late offline candidate")
    candidate["instruction"] = "Generate a candidate."

    def model(_messages):
        entered.set()
        assert release.wait(timeout=15)
        return json.dumps(candidate)

    monkeypatch.setattr(app, "_comic_image_vision_model", model)
    monkeypatch.setattr(app, "_comic_constraint_model",
                        lambda _: json.dumps({"status": "pass", "findings": []}))
    request = {**_request(app, pid, cid, "late-generation"),
               "images": [_ref(image)], "instruction": "Generate a candidate."}
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(app.comic_story_operation, cid, "generate", request)
        try:
            assert entered.wait(timeout=10)
            edited = ComicStoryService(app.comic_media).save(ComicStorySaveRequest.model_validate({
                **_request(app, pid, cid, "during-model"),
                "draft": _draft(image, title="User's later edit")}))
        finally:
            release.set()
        with pytest.raises(ToolError):
            pending.result(timeout=15)
    assert app.get_comic_story(cid)["story"] == edited["story"]
    runs = app.runtime_store.list_runs(domain="comic", conversation_id=cid)
    candidates = [run for run in runs if run.state.get("story_candidate")]
    assert len(candidates) == 1
    assert candidates[0].state["story_candidate"]["draft"] == candidate
    monkeypatch.setattr(app, "_comic_image_vision_model", _forbidden)
    monkeypatch.setattr(app, "_comic_constraint_model", _forbidden)
    adopted = app.comic_story_operation(cid, "adopt-candidate", {
        **_request(app, pid, cid, "adopt-late"), "candidate_run_id": candidates[0].id})
    assert adopted["story"]["version"] == edited["story"]["version"] + 1
    assert _story_draft(adopted["story"]) == candidate
    assert not adopted["confirmation"]


def _jpeg():
    # A real 1x1 JPEG fixture, encoded once with the operating system image codec.
    return base64.b64decode(
        "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYI"
        "DAoMDAsKCwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/2wBDAQMEBAUEBQkF"
        "BQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQU"
        "FBQUFBQUFBQUFBT/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAA"
        "AAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJx"
        "FDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVW"
        "V1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKz"
        "tLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEA"
        "AwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAx"
        "EEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5"
        "OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJma"
        "oqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3"
        "+Pn6/9oADAMBAAIRAxEAPwD876KKK6D6A//Z")


def _webp():
    return base64.b64decode("UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEADsD+JaQAA3AAAAAA")


def test_export_zip_preserves_formats_bytes_duplicate_mapping_and_shot_order(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, png_image = _setup(app)
    jpeg_image = _upload(app, pid, cid, content=_jpeg(), mime="image/jpeg", filename="source.jpeg")
    webp_image = _upload(app, pid, cid, content=_webp(), mime="image/webp",
                         filename="../../unsafe.webp")
    for image in (jpeg_image, webp_image):
        _select(app, cid, image)
    _save(app, pid, cid, _draft(webp_image, png_image, jpeg_image, webp_image))
    _confirm(app, pid, cid, monkeypatch)
    monkeypatch.setattr(app, "_comic_image_vision_model", _forbidden)
    monkeypatch.setattr(app, "_comic_constraint_model", _forbidden)
    request = _request(app, pid, cid, "export-formats")
    result = app.comic_story_operation(cid, "exports", request)
    artifact = app.runtime_store.get_artifact(result["export_artifact_id"])
    with ZipFile(BytesIO(Path(artifact.location).read_bytes())) as bundle:
        names = bundle.namelist()
        assert all(not name.startswith(("/", "\\")) and ".." not in name.split("/")
                   for name in names)
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest == result["manifest"]
        assert [item["shot_id"] for item in manifest["shots"]] == [
            "story-shot-1", "story-shot-2", "story-shot-3", "story-shot-4"]
        assert len(manifest["images"]) == 3
        expected = {webp_image["image_id"]: (_webp(), ".webp"),
                    png_image["image_id"]: (_png(), ".png"),
                    jpeg_image["image_id"]: (_jpeg(), ".jpg")}
        for image in manifest["images"]:
            content, suffix = expected[image["image_id"]]
            assert Path(image["filename"]).suffix == suffix
            assert bundle.read(image["filename"]) == content
        assert manifest["shots"][0]["filename"] == manifest["shots"][3]["filename"]
        assert bundle.read("story.md").decode("utf-8") == result["export_text"]
    assert app.comic_story_operation(cid, "exports", request)["export_artifact_id"] == artifact.id
    assert len(app.get_comic_story(cid)["exports"]) == 1


@pytest.mark.parametrize("failure", ["missing", "hash", "trashed"])
def test_unavailable_image_preserves_history_and_blocks_export(story_app, monkeypatch, failure):
    app = story_app
    pid, cid, image = _setup(app)
    story = _save(app, pid, cid, _draft(image))["story"]
    _confirm(app, pid, cid, monkeypatch)
    artifact = app.runtime_store.get_artifact(image["artifact_id"])
    path = Path(artifact.location)
    if failure == "missing":
        path.unlink()
    elif failure == "hash":
        atomic_write_bytes(path, _png(210), owner="offline.story.tamper")
    else:
        app.runtime_store.change_artifact_lifecycle(
            artifact.id, ArtifactLifecycle.TRASHED, artifact.lifecycle_version)
    view = app.get_comic_story(cid)
    assert view["story"] == story
    assert view["missing_images"]
    assert view["export_text"]
    with pytest.raises(ToolError):
        app.comic_story_operation(cid, "exports", _request(app, pid, cid, "invalid-export"))
    assert not app.get_comic_story(cid)["exports"]


@pytest.mark.parametrize("stage", ["write", "record"])
def test_atomic_export_failure_does_not_publish_a_fake_success(story_app, monkeypatch, stage):
    from kantoku.domains.comic import stories

    app = story_app
    pid, cid, image = _setup(app)
    _save(app, pid, cid, _draft(image))
    confirmed = _confirm(app, pid, cid, monkeypatch)

    def failed_output(*_args, **_kwargs):
        raise OSError("Offline disk failure before archive commit")

    if stage == "write":
        monkeypatch.setattr(stories, "atomic_write_bytes", failed_output)
    else:
        original_insert = app.comic_projects._insert_version

        def insert_version(*args, **kwargs):
            if kwargs.get("entity_type") == "story_export":
                raise OSError("Offline export record failure after archive write")
            return original_insert(*args, **kwargs)

        monkeypatch.setattr(app.comic_projects, "_insert_version", insert_version)
    with pytest.raises((OSError, ToolError)):
        app.comic_story_operation(cid, "exports", _request(app, pid, cid, "disk-failure"))
    view = app.get_comic_story(cid)
    assert view["story"] == confirmed["story"]
    assert view["confirmation"] == confirmed["confirmation"]
    assert view["exports"] == []
    assert not [artifact for artifact in app.runtime_store.list_artifacts()
                if artifact.source == "comic.story.export"]


def test_generated_instruction_is_bound_to_user_original_before_independent_review(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    instruction = "Use one image for two continuous segments; keep the source identity."
    draft = _draft(image, image)
    draft["instruction"] = "An invented model rewrite of the original request."
    inputs = []
    monkeypatch.setattr(app, "_comic_image_vision_model", lambda _: json.dumps(draft))

    def review(messages):
        inputs.append(json.loads(messages[-1]["content"]))
        return json.dumps({"status": "pass", "findings": []})

    monkeypatch.setattr(app, "_comic_constraint_model", review)
    generated = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "original-instruction"),
        "images": [_ref(image)], "instruction": instruction})
    assert generated["story"]["instruction"] == instruction
    assert inputs[0]["draft"]["instruction"] == instruction


def test_direct_upload_sources_are_local_and_do_not_inherit_project_briefs(story_app, monkeypatch):
    app = story_app
    cid = _conversation(app)
    pid = _project(app, "UNRELATED_OLD_USER_REQUEST_A")
    other_pid = _project(app, "UNRELATED_OLD_USER_REQUEST_B")
    images = [_upload(app, pid, cid), _upload(app, other_pid, cid, content=_png(92))]
    for image in images:
        _select(app, cid, image)
    seen = []

    def vision(messages):
        seen.append(json.loads(messages[-1]["content"][0]["text"]))
        draft = _draft(*images)
        draft["instruction"] = "Model text must not replace the user's request."
        return json.dumps(draft)

    def review(messages):
        seen.append(json.loads(messages[-1]["content"]))
        return json.dumps({"status": "pass", "findings": []})

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    result = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "local-sources"), "images": [_ref(image) for image in images],
        "instruction": "Treat these images as independent subjects."})
    expected = {image["image_id"]: image["project_id"] for image in images}
    for payload in seen:
        sources = payload["image_sources"]
        assert {source["image"]["image_id"]: source["source_project_id"]
                for source in sources} == expected
        assert all(source["constraints"] == [] for source in sources)
        assert all("negative_prompt" not in source for source in sources)
    assert "UNRELATED_OLD_USER_REQUEST" not in json.dumps(seen)
    assert len(result["story"]["source_contexts"]) == 2


@pytest.mark.parametrize("failure", ["hash", "trashed"])
def test_export_content_gateway_rechecks_original_zip(story_app, monkeypatch, failure):
    app = story_app
    pid, cid, image = _setup(app)
    _save(app, pid, cid, _draft(image))
    _confirm(app, pid, cid, monkeypatch)
    exported = app.comic_story_operation(cid, "exports", _request(app, pid, cid, "public-export"))
    artifact = app.runtime_store.get_artifact(exported["export_artifact_id"])
    path = app.get_core_artifact_content(artifact.id)
    assert path.read_bytes().startswith(b"PK")
    if failure == "hash":
        atomic_write_bytes(path, b"PK\x03\x04not-the-published-archive",
                           file_type="export", owner="offline.story.tamper")
    else:
        app.runtime_store.change_artifact_lifecycle(
            artifact.id, ArtifactLifecycle.TRASHED, artifact.lifecycle_version)
    with pytest.raises(ToolError):
        app.get_core_artifact_content(artifact.id)
    assert app.get_comic_story(cid)["exports"][0]["available"] is False


def test_http_story_save_and_read_routes_persist_without_model_calls(story_app):
    app = story_app
    pid, cid, image = _setup(app)
    server = web_studio.make_server(app)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    headers = {"X-Studio-Token": app.token, "Content-Type": "application/json"}
    path = f"/api/comic/conversations/{cid}/story"
    try:
        connection.request("PUT", path, json.dumps({
            **_request(app, pid, cid, "http-save"), "draft": _draft(image)}), headers)
        response = connection.getresponse()
        assert response.status in (200, 201)
        saved = json.loads(response.read())
        created = [event for event in app.runtime_store.list_events(saved["run_id"])
                   if event.payload.get("artifact_id") == saved["story"]["artifact_id"]]
        assert len(created) == 1 and created[0].payload["type"] == "document"
        for suffix in ("", "?version=1", "/versions", "/images"):
            connection.request("GET", path + suffix, headers=headers)
            response = connection.getresponse()
            assert response.status == 200
            payload = json.loads(response.read())
            if suffix in ("", "?version=1"):
                assert payload["story"] == saved["story"]
        connection.request("POST", path + "/confirm", json.dumps(
            _request(app, pid, cid, "http-unreviewed-confirm")), headers)
        response = connection.getresponse()
        assert response.status == 400
        failure = json.loads(response.read())
        assert failure["error_id"] and failure["trace_id"]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        assert not thread.is_alive()


def _generate(app, pid, cid, image, monkeypatch, *, draft, vision, review=None, key="generate"):
    monkeypatch.setattr(app, "_comic_image_vision_model", lambda _: json.dumps(vision))
    monkeypatch.setattr(app, "_comic_constraint_model",
                        lambda _: json.dumps(review or {"status": "pass", "findings": []}))
    return app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, key), "images": [_ref(image)],
        "instruction": draft.get("instruction", ""), "draft": draft})


def test_generate_uses_the_filled_framework_and_keeps_user_words(story_app, monkeypatch):
    app = story_app
    pid, cid, image = _setup(app)
    skeleton = {
        "title": "橙留香独自面对敌军", "summary": "他举起刀，独自走向阵前。", "instruction": "",
        "shots": [{"shot_id": "user-shot-1", "image": _ref(image), "action": "",
                   "camera": "", "duration_seconds": None, "transition": ""}],
    }
    model_draft = {"title": "模型改写的标题", "summary": "模型改写的概述", "instruction": "",
                   "shots": [{"shot_id": "ai-shot", "image": _ref(image),
                              "action": "他把握在手里的青龙偃月刀缓缓抬起", "camera": "推近到刀身",
                              "duration_seconds": 3.5, "transition": "切入下一镜"}]}
    seen = []

    def vision(messages):
        seen.append(json.loads(messages[-1]["content"][0]["text"]))
        return json.dumps(model_draft)

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model",
                        lambda _: json.dumps({"status": "pass", "findings": []}))
    result = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "framework"), "images": [_ref(image)],
        "instruction": "", "draft": skeleton})

    story = result["story"]
    # 用户已经写好的文字不被模型改写；模型只补空缺。
    assert story["title"] == "橙留香独自面对敌军"
    assert story["summary"] == "他举起刀，独自走向阵前。"
    assert story["shots"][0]["shot_id"] == "user-shot-1"
    assert story["shots"][0]["image"] == _ref(image)
    assert story["shots"][0]["action"] == "他把握在手里的青龙偃月刀缓缓抬起"
    assert story["shots"][0]["duration_seconds"] == 3.5
    # 模型确实收到了这份框架，且要求为空是合法的。
    assert seen[0]["current_draft"]["title"] == "橙留香独自面对敌军"
    assert seen[0]["instruction"] == ""
    assert result["review"]["status"] == "pass"


def test_review_names_the_shot_field_type_and_fix_without_calling_the_model(story_app, monkeypatch):
    app = story_app
    pid, cid, image = _setup(app)
    _save(app, pid, cid, {"title": "只有标题", "summary": "", "instruction": "", "shots": [
        {"shot_id": "shot-a", "image": _ref(image), "action": "",
         "camera": "", "duration_seconds": None, "transition": ""}]}, "incomplete")
    monkeypatch.setattr(app, "_comic_constraint_model", _forbidden)

    result = app.comic_story_operation(cid, "review", _request(app, pid, cid, "structural"))
    review = result["review"]
    assert review["status"] == "needs_revision"
    assert {(item["field"], item["code"]) for item in review["findings"]} == {
        ("summary", "missing_summary"), ("transition", "missing_transition"),
        ("action", "missing_action"), ("camera", "missing_camera"),
        ("duration_seconds", "missing_duration")}
    assert {item["shot_id"] for item in review["findings"]} == {None, "shot-a"}
    assert all(item["severity"] == "error" and item["suggestion"] for item in review["findings"])
    assert all("镜头 1" in item["message"] for item in review["findings"] if item["shot_id"])
    with pytest.raises(ToolError, match="尚未通过校验"):
        app.comic_story_operation(cid, "confirm", _request(app, pid, cid, "blocked"))


def test_unchanged_content_reuses_the_same_verdict_instead_of_asking_again(story_app, monkeypatch):
    app = story_app
    pid, cid, image = _setup(app)
    _save(app, pid, cid, _draft(image))
    calls = []

    def model(messages):
        calls.append(messages)
        return json.dumps({"status": "pass", "findings": []})

    monkeypatch.setattr(app, "_comic_constraint_model", model)
    first = app.comic_story_operation(cid, "review", _request(app, pid, cid, "first"))
    second = app.comic_story_operation(cid, "review", _request(app, pid, cid, "second"))
    assert first["review"]["status"] == second["review"]["status"] == "pass"
    assert len(calls) == 1 and second.get("review_reused") is True


def test_ai_draft_passes_validation_until_the_user_edits_it(story_app, monkeypatch):
    app = story_app
    pid, cid, image = _setup(app)
    review_calls = []

    def review_model(messages):
        review_calls.append(messages)
        return json.dumps({"status": "pass", "findings": []})

    monkeypatch.setattr(app, "_comic_image_vision_model", lambda _: json.dumps(_draft(image)))
    monkeypatch.setattr(app, "_comic_constraint_model", review_model)
    generated = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "gen"), "images": [_ref(image)], "instruction": "one shot"})
    assert generated["review"]["validation_requested"] is False
    assert generated["can_confirm"] is True
    direct = app.comic_story_operation(cid, "confirm", {
        **_request(app, pid, cid, "before-check")})
    assert direct["confirmation"]["confirmed"]
    exported = app.comic_story_operation(cid, "exports", _request(app, pid, cid, "direct-export"))
    assert exported["exports"][0]["story_version"] == generated["story"]["version"]
    checked = app.comic_story_operation(cid, "review", _request(app, pid, cid, "check"))
    assert checked["review"]["status"] == "pass"
    assert checked["review"]["validation_requested"] is True
    # 生成时已核验一次；内容没改，校验直接复用，不再付费调用。
    assert len(review_calls) == 1

    edited = _draft(image, title="改过的标题")
    _save(app, pid, cid, edited, "edit-after-draft")
    assert app.get_comic_story(cid)["review"] is None
    assert app.get_comic_story(cid)["can_confirm"] is False
    with pytest.raises(ToolError, match="尚未通过校验"):
        app.comic_story_operation(cid, "confirm", _request(app, pid, cid, "edited-unchecked"))
    again = app.comic_story_operation(cid, "review", _request(app, pid, cid, "recheck"))
    assert again["review"]["status"] == "pass"
    assert len(review_calls) == 2

    confirmed = app.comic_story_operation(cid, "confirm", {
        **_request(app, pid, cid, "confirmed")})
    assert confirmed["confirmation"]["confirmed"]
    exported = app.comic_story_operation(cid, "exports", _request(app, pid, cid, "exported"))
    assert exported["exports"][0]["story_version"] == again["story"]["version"]


def test_ai_organizing_fixes_advice_and_saves_only_after_a_clean_independent_review(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    original = _draft(image)
    _save(app, pid, cid, original)
    before = app.get_comic_story(cid)["story"]
    fixed = copy.deepcopy(original)
    fixed["shots"][0].update(camera="Start wide, then move toward the subject.",
                             duration_seconds=8.0, transition="Finish the gesture before cutting.")
    inputs, checks = [], []

    def vision(messages):
        payload = json.loads(messages[1]["content"][0]["text"])
        inputs.append(payload)
        assert app.get_comic_story(cid)["story"] == before
        return json.dumps(original if len(inputs) == 1 else fixed)

    def review(messages):
        current = json.loads(messages[1]["content"])["draft"]
        checks.append(current)
        return json.dumps({"status": "pass", "findings": []} if current["shots"] == fixed["shots"]
            else {"status": "warning", "findings": [{"severity": "warning",
                "shot_id": "story-shot-1", "field": "camera", "code": "camera_conflict",
                "evidence": original["shots"][0]["camera"], "message": "The opening needs room.",
                "suggestion": "Use a wide opening, extend the duration and finish the gesture."}]})

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    result = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "polish-advice"), "images": [_ref(image)],
        "instruction": original["instruction"], "draft": original})
    assert len(inputs) == len(checks) == 2
    assert result["generation_status"] == "ready" and result["can_confirm"]
    assert result["story"]["shots"] == fixed["shots"]
    assert not result["review"]["findings"] and result["story"]["version"] == before["version"] + 1
    monkeypatch.setattr(app, "_comic_constraint_model", _forbidden)
    repeated = app.comic_story_operation(cid, "review", _request(app, pid, cid, "check-polished"))
    assert repeated["review_reused"] and not repeated["review"]["findings"]


def test_unresolved_advice_is_not_published_as_a_finished_ai_story(story_app, monkeypatch):
    app = story_app
    pid, cid, image = _setup(app)
    original = _draft(image)
    _save(app, pid, cid, original)
    before = app.get_comic_story(cid)["story"]
    monkeypatch.setattr(app, "_comic_image_vision_model", lambda _: json.dumps(original))
    monkeypatch.setattr(app, "_comic_constraint_model", lambda _: json.dumps({
        "status": "warning", "findings": [{"severity": "warning", "shot_id": "story-shot-1",
            "field": "camera", "code": "camera_conflict",
            "evidence": original["shots"][0]["camera"],
            "message": "Framing still needs coordination.", "suggestion": "Coordinate framing."}]}))
    result = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "unresolved-advice"), "images": [_ref(image)],
        "instruction": original["instruction"], "draft": original})
    assert result["generation_status"] == "needs_revision"
    assert result["candidate_review"]["findings"][0]["severity"] == "warning"
    assert app.get_comic_story(cid)["story"] == before
    with pytest.raises(ToolError, match="候选.*未通过"):
        app.comic_story_operation(cid, "adopt-candidate", {
            **_request(app, pid, cid, "reject-advisory-candidate"),
            "candidate_run_id": result["candidate_run_id"]})


def test_repair_context_keeps_required_facts_without_repeating_three_complete_old_drafts(
    story_app, monkeypatch,
):
    from types import SimpleNamespace

    from kantoku.domains.comic.stories import ComicStoryService

    app = story_app
    _, _, image = _setup(app)
    draft = ComicStoryDraft.model_validate(_draft(*[image] * 6))
    for shot in draft.shots:
        shot.action = "人物完成当前动作并保持道具位置。" * 18
    fact = "起始画面的身份、衣服与道具必须保留。" * 120
    sources = [{"image": _ref(image), "constraints": [{"effective_requirement": fact}]}]
    findings = [{"severity": "error", "shot_id": shot.shot_id, "field": "action",
        "code": "continuity", "evidence": shot.action,
        "message": "需衔接下一镜。", "suggestion": "明确交接后的结束姿态。"}
        for shot in draft.shots]
    history = [{"draft": draft.model_dump(mode="json"),
                "review": {"status": "needs_revision", "findings": findings}} for _ in range(3)]
    calls = []

    def provider(messages, **kwargs):
        payload = json.loads(messages[1]["content"][0]["text"])
        calls.append(payload)
        assert payload["current_draft"] == draft.model_dump(mode="json")
        assert payload["user_intent"] == draft.model_dump(mode="json")
        assert fact in json.dumps(payload, ensure_ascii=False)
        return SimpleNamespace(content=draft.model_dump_json())

    monkeypatch.setattr(web_studio, "vision_chat", provider)
    result = app.comic_stories._repair(draft, findings, [image], sources,
        web_studio.StudioApplication._comic_image_vision_model, draft.instruction,
        user_intent=draft, repair_history=history)
    assert result == draft and len(calls) == 1
    assert len(calls[0]["repair_history"]) == 3
    assert all("draft" not in entry for entry in calls[0]["repair_history"])
    assert ComicStoryService._repair_scope(draft, findings)["shots"]


def test_six_image_repairs_with_timing_history_fit_the_real_visual_request_boundary(
    story_app, monkeypatch,
):
    from types import SimpleNamespace

    from kantoku.domains.comic.stories import ComicStoryService

    app = story_app
    pid, cid, first = _setup(app)
    images = [first, *[_upload(app, pid, cid, content=_png(index + 1)) for index in range(5)]]
    draft = ComicStoryDraft.model_validate(_draft(*images))
    for shot in draft.shots:
        shot.action = "人物完成当前动作并保持身份、衣服和道具位置。" * 12
        shot.transition = "收好灯笼并从腰间取出指南针，再进入下一起始画面。" * 3
    fields = list(ComicStoryService._review_fields(draft))
    sources = [{"image": _ref(image), "constraints": [{
        "effective_requirement": f"镜头{index}起始画面的身份、衣服、环境与道具必须保持。" * 12}]}
        for index, image in enumerate(images)]
    findings = [{"severity": "error", "shot_id": shot.shot_id, "field": "duration_seconds",
        "code": "duration_conflict", "evidence": str(shot.duration_seconds),
        "evidence_id": fields[index], "related_fields": fields[:20],
        "message": "当前动作和交接需完整时间。", "suggestion": "一次安排动作和交接的时长。",
        "timing_plan": [{"field": "action", "evidence": shot.action[:150], "seconds": 6.0},
                        {"field": "transition", "evidence": shot.transition, "seconds": 3.0}]}
        for index, shot in enumerate(draft.shots)]
    history = [{"draft": draft.model_dump(mode="json"),
                "review": {"status": "needs_revision", "findings": findings}} for _ in range(3)]
    calls = []
    def provider(messages, **kwargs):
        calls.append(messages)
        payload = json.loads(messages[1]["content"][0]["text"])
        assert payload["current_draft"] == payload["user_intent"] == draft.model_dump(mode="json")
        assert all("related_fields" not in finding and "evidence_id" not in finding
                   for finding in payload["issues"])
        assert all("timing_plan" not in finding for attempt in payload["repair_history"]
                   for finding in attempt["review"]["findings"])
        assert [finding["timing_plan"] for finding in payload["issues"]] == [
            finding["timing_plan"] for finding in findings]
        assert sum(part["type"] == "image_url" for part in messages[1]["content"]) == 6
        for source in sources:
            assert source["constraints"][0]["effective_requirement"] in json.dumps(
                payload, ensure_ascii=False)
        return SimpleNamespace(content=draft.model_dump_json())

    monkeypatch.setattr(web_studio, "vision_chat", provider)
    result = app.comic_stories._repair(draft, findings, images, sources,
        web_studio.StudioApplication._comic_image_vision_model, draft.instruction,
        user_intent=draft, repair_history=history, include_warnings=True)
    assert result == draft and len(calls) == 1


def test_organize_corrects_review_format_then_repairs_and_manual_validation_has_no_old_issues(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, first = _setup(app)
    second = _upload(app, pid, cid, content=_png(27))
    _select(app, cid, second)
    draft = _draft(first, second)
    draft["shots"][0].update(action="Close and put the lantern on the belt.",
                             transition="Take out and open the compass before cutting.")
    original = _save(app, pid, cid, draft)["story"]
    fixed = copy.deepcopy(draft)
    fixed["shots"][0]["duration_seconds"] = 8.0
    visions, reviews = [], []
    def vision(messages):
        payload = json.loads(messages[1]["content"][0]["text"])
        visions.append(payload)
        if len(visions) == 1:
            return json.dumps(draft)
        assert app.get_comic_story(cid)["story"] == original
        assert payload["issues"][0]["timing_plan"] == _timing_plan(draft["shots"][0])
        return json.dumps(fixed)

    def review(messages):
        payload = json.loads(messages[1]["content"])
        reviews.append(messages)
        if payload["draft"]["shots"] == fixed["shots"]:
            return json.dumps({"status": "pass", "findings": []})
        finding = {"severity": "error", "shot_id": "story-shot-1", "field": "duration_seconds",
            "code": "duration_conflict", "evidence": "4.0", "timing_plan": _timing_plan(
                draft["shots"][0]), "message": "Allow six seconds for the complete handoff.",
            "suggestion": "Extend the first shot to cover the movements before the cut."}
        if len(reviews) == 1:
            finding["timing_plan"][0]["seconds"] = "5 seconds"
        else:
            assert len(messages) == 3 and "schema_errors" in messages[-1]["content"]
        return json.dumps({"status": "needs_revision", "findings": [finding]})

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    request = {**_request(app, pid, cid, "format-then-repair"), "draft": draft,
               "instruction": draft["instruction"], "images": [_ref(first), _ref(second)]}
    result = app.comic_story_operation(cid, "generate", request)
    assert len(visions) == 2 and len(reviews) == 3
    assert result["generation_status"] == "ready" and result["can_confirm"]
    assert result["story"]["shots"] == fixed["shots"]
    assert result["story"]["version"] == original["version"] + 1
    monkeypatch.setattr(app, "_comic_constraint_model", _forbidden)
    monkeypatch.setattr(app, "_comic_image_vision_model", _forbidden)
    checked = app.comic_story_operation(
        cid, "review", _request(app, pid, cid, "validate-organized"))
    assert checked["review_reused"] and checked["review"]["findings"] == []
    app.comic_story_operation(cid, "confirm", _request(app, pid, cid, "confirm-organized"))
    assert app.comic_story_operation(cid, "generate", request) == result


def test_live_acceptance_replays_the_original_story_sources_and_checks_again(
    story_app, monkeypatch, tmp_path,
):
    from runpy import run_path
    from types import SimpleNamespace

    from kantoku.config.runtime_paths import atomic_write_text

    app = story_app
    _, _, image = _setup(app)
    draft = _draft(image)
    source = {"image": _ref(image), "user_requirements": ["Keep the silver lighthouse badge."]}
    snapshot = tmp_path / "source-story.json"
    atomic_write_text(snapshot, json.dumps({"draft": draft, "source_contexts": [source],
        "source_run_id": "offline-source-run", "images": [{"image": _ref(image),
            "sha256": image["sha256"]}]}))
    visions, reviews = [], []
    def vision(messages):
        payload = json.loads(messages[1]["content"][0]["text"])
        visions.append(payload)
        assert payload["image_sources"][0]["user_requirements"] == source["user_requirements"]
        current = payload["current_draft"]
        assert current["title"] == draft["title"]
        assert current["shots"][0]["image"] != _ref(image)
        current["shots"][0]["duration_seconds"] = 8.0
        return json.dumps(current)

    def review(messages):
        payload = json.loads(messages[1]["content"])
        reviews.append(payload)
        assert payload["draft"]["shots"][0]["duration_seconds"] == 8.0
        assert payload["image_sources"][0]["user_requirements"] == source["user_requirements"]
        return json.dumps({"status": "pass", "findings": []})

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    verify = run_path(str(Path(__file__).parents[1] / "scripts/verify_director_pipeline.py"))[
        "verify_story_export"]
    report = {"cases": []}
    verify(app, report, SimpleNamespace(story_snapshot=snapshot,
        image=Path(app.runtime_store.get_artifact(image["artifact_id"]).location),
        additional_image=[], task=None))
    assert len(visions) == 1 and len(reviews) == 2
    assert report["cases"][0]["passed"] is True
    assert report["cases"][0]["manual_review"]["findings"] == []
    assert report["cases"][0]["fresh_review"]["findings"] == []


def test_export_is_one_portable_video_prompt_with_timeline_and_cross_shot_handoff(story_app):
    app = story_app
    pid, cid, image = _setup(app)
    draft = _draft(image, image)
    draft["shots"][0].update(action="人物抬手接住灯笼。", camera="固定机位。",
                             duration_seconds=3.0, transition="手握灯笼转向街口，接下一镜。")
    draft["shots"][1].update(action="保持同一只手提灯，走向街口。", camera="顺行方向跟拍。",
                             duration_seconds=4.0, transition="背影远去后自然淡出。")
    view = _save(app, pid, cid, draft)
    text = view["export_text"]
    assert "完整视频生成提示词" in text and "一条完整" in text
    assert "7 秒" in text and "0–3 秒" in text and "3–7 秒" in text
    assert "跨镜连续性" in text
    for shot in draft["shots"]:
        assert shot["action"] in text and shot["transition"] in text
    assert text.count(view["manifest"]["shots"][0]["filename"]) >= 2


def test_export_reference_list_matches_deduplicated_files_in_first_shot_order(story_app):
    app = story_app
    pid, cid, first = _setup(app)
    duplicate = _upload(app, pid, cid, content=_png())
    other = _upload(app, pid, cid, content=_png(90))
    for image in (duplicate, other):
        _select(app, cid, image)
    view = _save(app, pid, cid, _draft(first, duplicate, other))
    shots = view["manifest"]["shots"]
    assert shots[0]["filename"] == shots[1]["filename"]
    references = view["export_text"].split("参考图对应（按分镜首次使用顺序上传）\n")[1]
    references = references.split("\n\n连续分镜时间轴")[0].splitlines()
    assert references == [f'参考图 1：{shots[0]["filename"]}',
                          f'参考图 2：{shots[2]["filename"]}']


def test_manual_review_resolves_current_field_reference_without_copying_long_quotes(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    draft = _draft(image)
    draft["shots"][0]["action"] = "先保持起始姿态，再追踪来箭并起身。" * 70
    saved = _save(app, pid, cid, draft)
    assert saved["can_confirm"] is False
    calls = []

    def review_model(messages):
        payload = json.loads(messages[1]["content"])
        calls.append(payload)
        evidence_id, coordinate = next((key, value) for key, value in
            payload["review_fields"].items() if value["field"] == "action")
        return json.dumps({"status": "needs_revision", "findings": [{
            **coordinate, "evidence_id": evidence_id, "severity": "error",
            "code": "duration_conflict", "message": "动作无法在当前时长内完成。",
            "timing_plan": _timing_plan(payload["draft"]["shots"][0]),
            "suggestion": "延长时长或简化动作。"}]})

    monkeypatch.setattr(app, "_comic_constraint_model", review_model)
    checked = app.comic_story_operation(cid, "review", _request(app, pid, cid, "direct-manual"))
    assert len(calls) == 1
    assert checked["story"] == saved["story"]
    finding = checked["review"]["findings"][0]
    assert finding["shot_id"] == draft["shots"][0]["shot_id"]
    assert finding["evidence"] == draft["shots"][0]["action"]
    assert finding["suggestion"] == "延长时长或简化动作。"
    assert checked["can_confirm"] is False


@pytest.mark.parametrize("invalid", ["unknown", "wrong_field", "stale", "invented_quote",
                                     "unknown_related"])
def test_review_field_reference_cannot_bypass_identity_or_current_content(
    story_app, monkeypatch, invalid,
):
    app = story_app
    pid, cid, image = _setup(app)
    _save(app, pid, cid, _draft(image))
    calls = []

    def review_model(messages):
        payload = json.loads(messages[1]["content"])
        calls.append(payload)
        evidence_id, coordinate = next((key, value) for key, value in
            payload["review_fields"].items() if value["field"] == "camera")
        finding = {**coordinate, "evidence_id": evidence_id, "severity": "error",
                   "message": "Public test conflict.", "suggestion": "Adjust this field."}
        if invalid == "unknown":
            finding["evidence_id"] = "not-in-current-draft"
        elif invalid == "wrong_field":
            finding["field"] = "action"
        elif invalid == "stale":
            finding["evidence_id"] = evidence_id.rsplit(":", 1)[0] + ":old-content-hash"
        elif invalid == "unknown_related":
            finding["related_fields"] = ["outside-current-draft"]
        else:
            finding["evidence"] = "Invented words never present in the camera field."
        return json.dumps({"status": "needs_revision", "findings": [finding]})

    monkeypatch.setattr(app, "_comic_constraint_model", review_model)
    with pytest.raises(ToolError):
        app.comic_story_operation(cid, "review", _request(app, pid, cid, invalid))
    assert len(calls) == 2
    assert app.get_comic_story(cid)["review"] is None
    assert app.get_comic_story(cid)["can_confirm"] is False


def test_ai_multishot_field_references_follow_repaired_content_then_confirm_without_review(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    skeleton = _draft(image)
    skeleton["shots"] = [{**skeleton["shots"][0], "shot_id": f"stable-{index}",
        "action": f"镜头{index}保持起始姿态，追踪来箭，再缓慢起身。" * 20}
        for index in range(5)]
    fixed = copy.deepcopy(skeleton)
    fixed["shots"][3].update(action="先追踪来箭，再起身闪避。", duration_seconds=8.0)
    vision_calls, review_calls, reference = [], [], []

    def vision(messages):
        vision_calls.append(messages)
        return json.dumps(skeleton if len(vision_calls) == 1 else fixed)

    def review_model(messages):
        payload = json.loads(messages[1]["content"])
        review_calls.append(payload)
        current_id, coordinate = next((key, value) for key, value in
            payload["review_fields"].items() if value == {"shot_id": "stable-3", "field": "action"})
        if len(review_calls) == 1:
            reference.append(current_id)
        elif len(review_calls) == 3:
            assert reference[0] != current_id
            assert "review_fields" in messages[-1]["content"]
            return json.dumps({"status": "pass", "findings": []})
        return json.dumps({"status": "needs_revision", "findings": [{
            **coordinate, "evidence_id": reference[0], "severity": "error",
            "code": "duration_conflict", "message": "动作与时长不匹配。",
            "timing_plan": _timing_plan(payload["draft"]["shots"][3]),
            "suggestion": "协调动作顺序并延长时长。"}]})

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review_model)
    request = {**_request(app, pid, cid, "multishot-reference"), "draft": skeleton,
               "images": [_ref(image)], "instruction": skeleton["instruction"]}
    result = app.comic_story_operation(cid, "generate", request)
    assert result["can_confirm"] and result["review"]["status"] == "pass"
    assert result["review"]["validation_requested"] is False
    assert result["story"]["shots"] == fixed["shots"]
    assert app.comic_story_operation(cid, "generate", request) == result
    app.comic_story_operation(cid, "confirm", _request(app, pid, cid, "direct-confirm"))
    exported = app.comic_story_operation(cid, "exports", _request(app, pid, cid, "direct-zip"))
    assert exported["exports"] and len(vision_calls) == 2 and len(review_calls) == 3


def test_cross_shot_error_repairs_all_verified_related_shots_without_changing_other_shots(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    skeleton = _draft(image, image, image)
    fixed = copy.deepcopy(skeleton)
    fixed["shots"][0]["transition"] = "Put down the lantern before picking up the beacon."
    fixed["shots"][1]["action"] = "Raise the beacon taken at the previous boundary."
    fixed["shots"][2]["action"] = "UNRELATED_CHANGE_MUST_NOT_BE_APPLIED"
    calls = []

    def review(messages):
        payload = json.loads(messages[1]["content"])
        calls.append(payload)
        if len(calls) == 2:
            assert payload["draft"]["shots"][0]["transition"] == fixed["shots"][0]["transition"]
            assert payload["draft"]["shots"][1]["action"] == fixed["shots"][1]["action"]
            assert payload["draft"]["shots"][2] == skeleton["shots"][2]
            return json.dumps({"status": "pass", "findings": []})
        fields = payload["review_fields"]
        primary = next(key for key, value in fields.items()
                       if value == {"shot_id": "story-shot-1", "field": "transition"})
        related = next(key for key, value in fields.items()
                       if value == {"shot_id": "story-shot-2", "field": "action"})
        return json.dumps({"status": "needs_revision", "findings": [{
            "severity": "error", "field": "transition", "evidence_id": primary,
            "related_fields": [related], "code": "requirement_conflict",
            "message": "A prop changes across this boundary without a handoff.",
            "suggestion": "Coordinate the handoff and the next shot's opening action."}]})

    visions = iter([skeleton, fixed])
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    monkeypatch.setattr(app, "_comic_image_vision_model", lambda _: json.dumps(next(visions)))
    result = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "related-shots"), "images": [_ref(image)],
        "draft": skeleton, "instruction": "Keep the story coherent."})
    assert result["can_confirm"] and len(calls) == 2


def test_partial_structural_repairs_use_the_existing_two_attempts_and_preserve_progress(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    original = _draft(image)
    first = copy.deepcopy(original)
    first["shots"][0].update(action="", camera="")
    second = copy.deepcopy(first)
    second["shots"][0]["action"] = original["shots"][0]["action"]
    visions = iter([first, second, original])
    monkeypatch.setattr(app, "_comic_image_vision_model", lambda _: json.dumps(next(visions)))
    monkeypatch.setattr(app, "_comic_constraint_model",
                        lambda _: json.dumps({"status": "pass", "findings": []}))
    result = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "partial-structure"), "images": [_ref(image)],
        "draft": first, "instruction": "Fill both execution fields."})
    assert result["story"]["shots"] == original["shots"]
    assert result["can_confirm"]
    assert len(app.runtime_store.get_run(result["run_id"]).state["story_attempts"]) == 3


def test_story_sources_are_bound_to_each_shots_actual_image_not_input_order():
    from kantoku.domains.comic.stories import ComicStoryService

    images = [{"image_id": "first", "version": 1}, {"image_id": "second", "version": 2}]
    draft = ComicStoryDraft.model_validate({"shots": [
        {"shot_id": "second-shot", "image": images[1]},
        {"shot_id": "first-shot", "image": images[0]},
        {"shot_id": "repeat-shot", "image": images[1]},
    ]})
    sources = [{"image": images[0]}, {"image": images[1]}]
    result = ComicStoryService._shot_source_map(draft, sources)
    assert result["first-shot"]["image_source_index"] == 0
    assert result["second-shot"]["image_source_index"] == 1
    assert result["repeat-shot"]["image_source_index"] == 1
    fields = ComicStoryService._review_fields(draft)
    related = next(key for key, value in fields.items() if value == {
        "shot_id": "second-shot", "field": "action"})
    scope = ComicStoryService._repair_scope(draft, [{"severity": "warning",
        "shot_id": "first-shot", "field": "action", "code": "requirement_conflict",
        "related_fields": [related]}])
    assert scope["shots"] == {}  # 艺术建议不能授权跨镜头重写。
    scope = ComicStoryService._repair_scope(draft, [{"severity": "error",
        "shot_id": "first-shot", "field": "action", "code": "image_conflict",
        "related_fields": [related]}])
    assert set(scope["shots"]) == {"first-shot", "second-shot"}


@pytest.mark.parametrize("stale", [True, False])
@pytest.mark.parametrize("severity", ["error", "warning"])
def test_review_only_discards_a_stale_quoted_claim_with_verified_current_counterevidence(
    story_app, monkeypatch, stale, severity,
):
    app = story_app
    pid, cid, image = _setup(app)
    draft = _draft(image)
    draft["shots"][0]["camera"] = "向右移动后停住。" if stale else "向左移动后停住。"
    _save(app, pid, cid, draft)
    calls = []

    def review(messages):
        payload = json.loads(messages[1]["content"])
        calls.append(payload)
        if payload.get("stage") == "story_finding_audit":
            field_id = next(key for key, value in payload["review_fields"].items()
                            if value == {"shot_id": "story-shot-1", "field": "camera"})
            return json.dumps({"checks": [{"finding": 1, "verdict": "stale_current_quote",
                "field_id": field_id, "claimed_quote": "向左", "reason": "该引用与现稿不一致。",
                "current_quote": draft["shots"][0]["camera"]}]})
        return json.dumps({"status": "needs_revision", "findings": [{
            "severity": severity, "shot_id": "story-shot-1", "field": "camera",
            "code": "camera_conflict", "message": "当前运镜写“向左”，与要求不一致。",
            "evidence": draft["shots"][0]["camera"], "suggestion": "改正运动方向。"}]})

    monkeypatch.setattr(app, "_comic_constraint_model", review)
    checked = app.comic_story_operation(cid, "review", _request(app, pid, cid, "quoted-claim"))
    expected = "needs_revision" if severity == "error" else "warning"
    assert checked["review"]["status"] == ("pass" if stale else expected)
    assert len(calls) == (2 if stale else 1)


@pytest.mark.parametrize("invalid", ["missing_check", "wrong_field", "false_counterquote"])
def test_invalid_audit_cannot_clear_a_real_finding(invalid):
    from types import SimpleNamespace

    from kantoku.domains.comic.models import ComicStoryReviewOutput
    from kantoku.domains.comic.stories import ComicStoryService

    service = ComicStoryService(SimpleNamespace(projects=None, runtime=None))
    draft = ComicStoryDraft.model_validate({"title": "Current story", "shots": [{
        "shot_id": "one", "image": {"image_id": "image", "version": 1},
        "camera": "Move right."}]})
    output = ComicStoryReviewOutput.model_validate({"status": "needs_revision", "findings": [{
        "severity": "error", "shot_id": "one", "field": "camera",
        "message": 'Current camera says "Move left".', "evidence": "Move right."}]})
    fields = service._review_fields(draft)
    camera = next(key for key, value in fields.items() if value["field"] == "camera")
    title = next(key for key, value in fields.items() if value["field"] == "title")
    check = {"finding": 1, "verdict": "stale_current_quote", "claimed_quote": "Move left",
             "field_id": title if invalid == "wrong_field" else camera,
             "current_quote": "Invented" if invalid == "false_counterquote" else "Move right.",
             "reason": "Must be verified against the same current field."}
    service._audit_finding_quotes(draft, output,
        lambda _: json.dumps({"checks": [] if invalid == "missing_check" else [check]}), [])
    assert output.status == "needs_revision" and len(output.findings) == 1


@pytest.mark.parametrize("bad_check", ["missing", "duplicate", "bad_quote"])
def test_one_bad_countercheck_keeps_its_issue_without_discarding_other_valid_checks(bad_check):
    from types import SimpleNamespace

    from kantoku.domains.comic.models import ComicStoryReviewOutput
    from kantoku.domains.comic.stories import ComicStoryService

    service = ComicStoryService(SimpleNamespace(projects=None, runtime=None))
    draft = ComicStoryDraft.model_validate({"title": "New title", "summary": "New summary"})
    findings = [{"severity": "error", "field": field, "evidence": value,
                 "message": f'Current {field} says "Old {field}".'}
                for field, value in (("title", draft.title), ("summary", draft.summary))]
    output = ComicStoryReviewOutput.model_validate({"status": "needs_revision",
                                                   "findings": findings})
    fields = service._review_fields(draft)
    def audit(messages):
        payload = json.loads(messages[1]["content"])
        assert set(payload["audit_requirements"]) == {"1", "2"}
        checks = [{"finding": index, "verdict": "stale_current_quote",
                   "field_id": next(ref for ref, coordinate in fields.items()
                                    if coordinate["field"] == field),
                   "claimed_quote": f"Old {field}", "current_quote": value,
                   "reason": "Only this obsolete quoted value supported this finding."}
                  for index, (field, value) in enumerate(
                      (("title", draft.title), ("summary", draft.summary)), 1)]
        if bad_check == "missing":
            checks.pop()
        elif bad_check == "duplicate":
            checks.append(copy.deepcopy(checks[1]))
        else:
            checks[1]["current_quote"] = "Invented current text"
        return json.dumps({"checks": checks})

    service._audit_finding_quotes(draft, output, audit, [])
    assert [finding.field for finding in output.findings] == ["summary"]
    assert output.status == "needs_revision"


def test_confirmed_edit_requirements_are_available_as_exact_source_counterevidence():
    from types import SimpleNamespace

    from kantoku.domains.comic.models import ComicStoryReviewOutput
    from kantoku.domains.comic.stories import ComicStoryService

    service = ComicStoryService(SimpleNamespace(projects=None, runtime=None))
    draft = ComicStoryDraft.model_validate({"shots": [
        {"shot_id": "first", "image": {"image_id": "a", "version": 1},
         "action": "Hold the lantern.", "transition": "Stow the lantern and open the compass."},
        {"shot_id": "second", "image": {"image_id": "b", "version": 2},
         "action": "Hold the open compass."}]})
    fields = service._review_fields(draft)
    primary = next(ref for ref, coordinate in fields.items()
                   if coordinate == {"shot_id": "second", "field": "action"})
    transition = next(ref for ref, coordinate in fields.items()
                      if coordinate == {"shot_id": "first", "field": "transition"})
    sources = [{"image": shot.image.model_dump(), "authorized_requirements": [
        {"requirement_id": "confirmed-edit", "requirement": shot.action,
         "source": {"kind": "manual_region"}}]} for shot in draft.shots]
    output = ComicStoryReviewOutput.model_validate({"status": "needs_revision", "findings": [{
        "severity": "error", "shot_id": "second", "field": "action", "code": "continuity",
        "evidence_id": primary, "related_fields": [transition],
        "message": "The compass has not been obtained."}]})
    calls = []
    def audit(messages):
        payload = json.loads(messages[1]["content"])
        calls.append(payload)
        assert len(payload["audit_requirements"]["1"]["source_fields_by_image"]) == 2
        quotes = {primary: draft.shots[1].action, transition: draft.shots[0].transition}
        quotes.update({ref: item["quote"] for ref, item in payload["source_fields"].items()})
        return json.dumps({"checks": [{"finding": 1, "verdict": "already_satisfied",
            "supporting_quotes": quotes, "reason": "Both actual sources and handoff agree."}]})

    service._audit_finding_quotes(draft, output, audit, sources)
    assert len(calls) == 1 and output.findings == []


def test_audit_deduplicates_source_text_without_losing_evidence_or_real_findings():
    from types import SimpleNamespace

    from kantoku.core.conversation_context import token_size
    from kantoku.domains.comic.models import ComicStoryReviewOutput
    from kantoku.domains.comic.stories import ComicStoryService

    service = ComicStoryService(SimpleNamespace(projects=None, runtime=None))
    draft = ComicStoryDraft.model_validate({"shots": [{"shot_id": str(index),
        "image": {"image_id": f"image-{index}", "version": 1},
        "action": f"Hold object {index}.", "transition": f"Prepare for frame {index + 1}."}
        for index in range(6)]})
    sources = []
    for index, shot in enumerate(draft.shots):
        requirement = f"起始画面 {index} 的有效要求：" + "保留画面中明确的道具与主体。" * 80
        sources.append({"image": shot.image.model_dump(), "constraints": [{"status": "active",
            "origin": "user_request", "source_quote": f"原始要求 {index}",
            "effective_requirement": requirement, "scope": "image"}]})
    fields = service._review_fields(draft)
    def ref(index, field):
        return next(key for key, value in fields.items()
                    if value == {"shot_id": str(index), "field": field})
    findings = [{"severity": "error", "shot_id": str(index), "field": "action",
        "evidence_id": ref(index, "action"), "evidence": draft.shots[index].action,
        "related_fields": [ref(index - 1, "transition")], "code": "continuity",
        "message": "The handoff needs checking.", "suggestion": "Explain the handoff."}
        for index in range(1, 6)]
    output = ComicStoryReviewOutput.model_validate({"status": "needs_revision",
                                                   "findings": findings})
    calls = []

    def audit(messages):
        calls.append(messages)
        payload = json.loads(messages[1]["content"])
        assert all("evidence" not in finding for finding in payload["findings"].values())
        assert all(item["primary_field_id"] in payload["review_fields"]
                   for item in payload["audit_requirements"].values())
        for index, source in enumerate(payload["image_sources"]):
            constraint = source["constraints"][0]
            reference = constraint["effective_requirement_ref"]
            assert payload["source_fields"][reference]["quote"] == (
                sources[index]["constraints"][0]["effective_requirement"])
            assert constraint["source_quote"] == f"原始要求 {index}"
            assert constraint["scope"] == "image"
        assert token_size(messages) <= 18000
        return json.dumps({"checks": [{"finding": int(index), "verdict": "retain",
            "reason": "The actual handoff still needs work."} for index in payload["findings"]]})

    service._audit_finding_quotes(draft, output, audit, sources)
    assert len(calls) == 1 and len(output.findings) == 5
    assert output.status == "needs_revision"


@pytest.mark.parametrize("invalid", ["missing", "wrong_quote", "missing_transition", "fits"])
def test_duration_errors_require_a_grounded_budget_for_action_and_transition(invalid):
    from types import SimpleNamespace

    from kantoku.domains.comic.stories import ComicStoryService

    service = ComicStoryService(SimpleNamespace(projects=None, runtime=None))
    draft = ComicStoryDraft.model_validate({"title": "A transfer", "shots": [{
        "shot_id": "one", "image": {"image_id": "a", "version": 1},
        "action": "Close and stow the lantern.", "transition": "Take out and open the compass.",
        "duration_seconds": 4.0}]})
    timing = [{"field": "action", "evidence": draft.shots[0].action, "seconds": 3.0},
              {"field": "transition", "evidence": draft.shots[0].transition, "seconds": 3.0}]
    bad = copy.deepcopy(timing)
    if invalid == "missing":
        bad = []
    elif invalid == "wrong_quote":
        bad[0]["evidence"] = "An action not in the current story."
    elif invalid == "missing_transition":
        bad.pop()
    else:
        for beat in bad:
            beat["seconds"] = 1.0
    calls = []
    def review(messages):
        calls.append(messages)
        finding = {"severity": "error", "shot_id": "one", "field": "duration_seconds",
            "code": "duration_conflict", "evidence": "4.0", "message": "Four seconds is too short.",
            "suggestion": "Allow six seconds for the two movements."}
        if len(calls) == 1:
            if bad:
                finding["timing_plan"] = bad
        else:
            content = messages[-1]["content"]
            diagnostics = json.loads(content[content.index("{"):])
            assert diagnostics["issues"][0]["reason"] == "invalid_timing_plan"
            issue = diagnostics["issues"][0]
            assert issue["required_phases"] == ["action", "transition"]
            assert issue["provided_phases"] == sorted({beat["field"] for beat in bad})
            assert issue["budget_seconds"] == sum(beat["seconds"] for beat in bad)
            assert issue["quote_mismatch_beats"] == ([0] if invalid == "wrong_quote" else [])
            assert issue["minimum_error_budget_exclusive"] == 4.0
            finding["timing_plan"] = timing
        return json.dumps({"status": "needs_revision", "findings": [finding]})

    checked = service._review_output(draft, [], review, [])
    assert len(calls) == 2
    assert checked["status"] == "needs_revision"
    assert checked["findings"][0]["timing_plan"] == timing


def test_ungrounded_duration_review_never_publishes_or_confirms_a_generated_draft(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    draft = _draft(image)
    original = _save(app, pid, cid, draft)["story"]
    visions, reviews = [], []
    def vision(messages):
        visions.append(messages)
        return json.dumps(draft)
    def review(messages):
        reviews.append(messages)
        return json.dumps({"status": "needs_revision", "findings": [{
            "severity": "error", "shot_id": "story-shot-1", "field": "duration_seconds",
            "code": "duration_conflict", "evidence": "4.0",
            "message": "Too many actions.", "suggestion": "Add one second."}]})

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    request = {**_request(app, pid, cid, "unproven-timing"), "draft": draft,
               "images": [_ref(image)], "instruction": "Allow time for the entire movement."}
    with pytest.raises(ToolError, match="节拍预算"):
        app.comic_story_operation(cid, "generate", request)
    assert len(visions) == 1 and len(reviews) == 2
    assert app.get_comic_story(cid)["story"] == original
    with pytest.raises(ToolError):
        app.comic_story_operation(cid, "generate", request)
    assert len(visions) == 1 and len(reviews) == 2
    run = next(run for run in app.runtime_store.list_runs(project_id=pid)
               if run.state.get("request_id") == request["request_id"])
    assert run.status.value == "failed"
    assert run.state["story_candidate"]["draft"] == {**draft, "instruction": request["instruction"]}
    with pytest.raises(ToolError, match="候选.*未通过"):
        app.comic_story_operation(cid, "adopt-candidate", {
            **_request(app, pid, cid, "reject-unproven-timing"), "candidate_run_id": run.id})


def test_duration_budget_allows_an_instantaneous_editorial_cut():
    from types import SimpleNamespace

    from kantoku.domains.comic.stories import ComicStoryService

    service = ComicStoryService(SimpleNamespace(projects=None, runtime=None))
    draft = ComicStoryDraft.model_validate({"shots": [{"shot_id": "one",
        "image": {"image_id": "a", "version": 1}, "action": "Walk to the mark.",
        "transition": "Cut.", "duration_seconds": 4.0}]})
    timing = [{"field": "action", "evidence": "Walk to the mark.", "seconds": 6.0},
              {"field": "transition", "evidence": "Cut.", "seconds": 0.0}]
    calls = []
    def review(messages):
        calls.append(messages)
        return json.dumps({"status": "needs_revision", "findings": [{
            "severity": "error", "shot_id": "one", "field": "duration_seconds",
            "code": "duration_conflict", "evidence": "4.0", "timing_plan": timing,
            "message": "The walk needs six seconds.", "suggestion": "Allow six seconds."}]})

    checked = service._review_output(draft, [], review, [])
    assert len(calls) == 1 and checked["status"] == "needs_revision"
    assert checked["findings"][0]["timing_plan"] == timing


@pytest.mark.parametrize("repair_changes_story", [True, False])
def test_unbudgeted_timing_warning_must_be_fixed_before_a_story_is_saved(
    story_app, monkeypatch, repair_changes_story,
):
    app = story_app
    pid, cid, image = _setup(app)
    draft = _draft(image)
    original = _save(app, pid, cid, draft)["story"]
    fixed = copy.deepcopy(draft)
    fixed["shots"][0]["duration_seconds"] = 6.0
    calls, checks = [], []

    def vision(messages):
        calls.append(messages)
        return json.dumps(fixed if len(calls) > 1 and repair_changes_story else draft)

    def review(messages):
        payload = json.loads(messages[1]["content"])
        checks.append(payload)
        shot = payload["draft"]["shots"][0]
        if shot["duration_seconds"] == 6.0:
            return json.dumps({"status": "pass", "findings": []})
        return json.dumps({"status": "warning", "findings": [{"severity": "warning",
            "shot_id": shot["shot_id"], "field": "duration_seconds", "code": "duration_conflict",
            "evidence": str(shot["duration_seconds"]), "message": "The pace can be improved.",
            "suggestion": "Allow six seconds for the whole movement.", "timing_plan": []}]})

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    result = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "unbudgeted-advice"), "draft": draft,
        "images": [_ref(image)], "instruction": draft["instruction"]})
    if repair_changes_story:
        assert result["generation_status"] == "ready" and len(checks) == 2
        assert result["story"]["shots"][0]["duration_seconds"] == 6.0
        assert result["review"]["findings"] == []
    else:
        assert result["generation_status"] == "needs_revision" and len(checks) == 1
        assert result["candidate_review"]["findings"][0]["severity"] == "warning"
        assert app.get_comic_story(cid)["story"] == original


def test_invalid_review_field_reference_correction_includes_the_current_field_id():
    from types import SimpleNamespace

    from kantoku.domains.comic.stories import ComicStoryService

    service = ComicStoryService(SimpleNamespace(projects=None, runtime=None))
    draft = ComicStoryDraft.model_validate({"shots": [{"shot_id": "one",
        "image": {"image_id": "a", "version": 1}, "action": "Put the lantern away.",
        "transition": "Take the compass.", "duration_seconds": 4.0}]})
    fields = service._review_fields(draft)
    expected = next(ref for ref, value in fields.items() if value["field"] == "transition")
    wrong = next(ref for ref, value in fields.items() if value["field"] == "action")
    calls = []

    def review(messages):
        calls.append(messages)
        if len(calls) == 2:
            content = messages[-1]["content"]
            diagnostics = json.loads(content[content.index("{"):])
            assert diagnostics["issues"][0]["expected_field_reference"] == expected
        return json.dumps({"status": "needs_revision", "findings": [{"severity": "error",
            "shot_id": "one", "field": "transition", "code": "other", "evidence": "",
            "evidence_id": wrong if len(calls) == 1 else expected,
            "message": "The transition needs a clear handoff.",
            "suggestion": "Clarify the handoff."}]})

    output = service._review_output(draft, [], review, [])
    assert len(calls) == 2 and output["status"] == "needs_revision"
    assert output["findings"][0]["evidence"] == draft.shots[0].transition


@pytest.mark.parametrize("severity", ["error", "warning"])
def test_repair_applies_the_verified_timing_budget_even_if_the_model_undercuts_it(severity):
    from types import SimpleNamespace

    from kantoku.domains.comic.stories import ComicStoryService

    service = ComicStoryService(SimpleNamespace(projects=None, runtime=None))
    draft = ComicStoryDraft.model_validate({"shots": [{"shot_id": "one",
        "image": {"image_id": "a", "version": 1}, "action": "Close the lantern.",
        "transition": "Stow the lantern and take out the compass.", "duration_seconds": 3.5}]})
    finding = {"severity": severity, "shot_id": "one", "field": "transition",
        "code": "duration_conflict", "evidence": draft.shots[0].transition,
        "message": "The complete action and handoff require 5.5 seconds.",
        "suggestion": "Allow the full timing budget.", "timing_plan": [
            {"field": "action", "evidence": draft.shots[0].action, "seconds": 3.0},
            {"field": "transition", "evidence": draft.shots[0].transition, "seconds": 2.5}]}
    inputs = []
    def repair(messages):
        payload = json.loads(messages[1]["content"][0]["text"])
        inputs.append(payload)
        assert payload["minimum_durations"] == {"one": 5.5}
        returned = draft.model_copy(deep=True)
        returned.shots[0].camera = "Follow the complete handoff."
        return returned.model_dump_json()

    fixed = service._repair_once(draft, [finding], [], [], repair, "", {("a", 1)},
                                  include_warnings=True)
    assert fixed.shots[0].duration_seconds == 5.5
    assert fixed.shots[0].camera == "Follow the complete handoff."
    assert fixed.shots[0].image == draft.shots[0].image
    assert draft.shots[0].duration_seconds == 3.5 and len(inputs) == 1


@pytest.mark.parametrize("changed_field", ["action", "transition"])
def test_old_timing_budget_is_not_applied_to_a_rewritten_action_or_transition(changed_field):
    from types import SimpleNamespace

    from kantoku.domains.comic.stories import ComicStoryService

    service = ComicStoryService(SimpleNamespace(projects=None, runtime=None))
    draft = ComicStoryDraft.model_validate({"shots": [{"shot_id": "one",
        "image": {"image_id": "a", "version": 1}, "action": "Close the lantern.",
        "transition": "Stow the lantern and take the compass.", "duration_seconds": 3.5}]})
    finding = {"severity": "error", "shot_id": "one", "field": "duration_seconds",
        "code": "duration_conflict", "evidence": "3.5", "message": "The movements need time.",
        "timing_plan": [{"field": "action", "evidence": draft.shots[0].action, "seconds": 3.0},
            {"field": "transition", "evidence": draft.shots[0].transition, "seconds": 2.5}]}
    def repair(messages):
        current = json.loads(messages[1]["content"][0]["text"])["current_draft"]
        current["shots"][0][changed_field] = "The movement has been changed."
        return json.dumps(current)

    result = service._repair_once(draft, [finding], [], [], repair, "", {("a", 1)})
    assert getattr(result.shots[0], changed_field) == "The movement has been changed."
    assert result.shots[0].duration_seconds == 3.5


def test_rewritten_timing_is_rechecked_and_unresolved_draft_is_not_saved(story_app, monkeypatch):
    app = story_app
    pid, cid, image = _setup(app)
    draft = _draft(image)
    draft["shots"][0].update(action="Close the lantern.",
        transition="Stow the lantern and take the compass.", duration_seconds=3.5)
    original = _save(app, pid, cid, draft)["story"]
    rewritten = copy.deepcopy(draft)
    rewritten["shots"][0]["action"] = "Close and secure the lantern."
    visions, reviews = [], []

    def vision(messages):
        visions.append(messages)
        return json.dumps(draft if len(visions) == 1 else rewritten)

    def review(messages):
        payload = json.loads(messages[1]["content"])
        reviews.append(payload)
        shot = payload["draft"]["shots"][0]
        return json.dumps({"status": "needs_revision", "findings": [{"severity": "error",
            "shot_id": shot["shot_id"], "field": "duration_seconds", "code": "duration_conflict",
            "evidence": str(shot["duration_seconds"]),
            "message": "The full handoff needs more time.",
            "timing_plan": [{"field": "action", "evidence": shot["action"],
                             "seconds": shot["duration_seconds"]},
                {"field": "transition", "evidence": shot["transition"], "seconds": 2.5}]}]})

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    result = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "recheck-changed-timing"), "draft": draft,
        "images": [_ref(image)], "instruction": draft["instruction"]})
    assert len(reviews) >= 2 and reviews[1]["draft"]["shots"] == rewritten["shots"]
    assert result["generation_status"] == "needs_revision"
    assert result["candidate_review"]["status"] == "needs_revision"
    assert app.get_comic_story(cid)["story"] == original


@pytest.mark.parametrize("severity", ["error", "warning"])
def test_one_organize_click_finishes_all_six_real_timing_fixes_before_saving(
    story_app, monkeypatch, severity,
):
    app = story_app
    pid, cid, image = _setup(app)
    draft = _draft(*[image] * 6)
    for index, shot in enumerate(draft["shots"], 1):
        shot.update(action=f"Close lantern {index}.",
                    transition=f"Stow lantern {index} and take out compass {index}.",
                    duration_seconds=3.5)
    original = _save(app, pid, cid, draft)["story"]
    visions, reviews = [], []
    def vision(messages):
        payload = json.loads(messages[1]["content"][0]["text"])
        visions.append(payload)
        assert app.get_comic_story(cid)["story"] == original
        # This substitute keeps returning the too-short duration: the application must apply
        # the independently verified budget, not depend on this writer following advice.
        return json.dumps(draft if len(visions) == 1 else payload["current_draft"])

    def review(messages):
        payload = json.loads(messages[1]["content"])
        reviews.append(payload)
        current = payload["draft"]
        remaining = next((shot for shot in current["shots"]
                          if shot["duration_seconds"] < 5.5), None)
        if remaining is None:
            assert all(shot["action"] == old["action"] and shot["transition"] == old["transition"]
                       for shot, old in zip(current["shots"], draft["shots"], strict=True))
            return json.dumps({"status": "pass", "findings": []})
        return json.dumps({"status": "needs_revision" if severity == "error" else "warning",
            "findings": [{"severity": severity, "shot_id": remaining["shot_id"],
                "field": "duration_seconds", "code": "duration_conflict",
                "evidence": str(remaining["duration_seconds"]),
                "message": "The two movements need 5.5 seconds.",
                "suggestion": "Apply the complete action and transition budget.",
                "timing_plan": [
                    {"field": "action", "evidence": remaining["action"], "seconds": 3.0},
                    {"field": "transition", "evidence": remaining["transition"],
                     "seconds": 2.5}]}]})

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    request = {**_request(app, pid, cid, "six-fixes-one-click"), "draft": draft,
               "images": [_ref(image)], "instruction": draft["instruction"]}
    result = app.comic_story_operation(cid, "generate", request)
    assert result["generation_status"] == "ready" and result["can_confirm"]
    assert len(visions) == len(reviews) == 7
    assert all(shot["duration_seconds"] == 5.5 for shot in result["story"]["shots"])
    assert result["story"]["version"] == original["version"] + 1
    assert len(app.runtime_store.get_run(result["run_id"]).state["story_attempts"]) == 7
    monkeypatch.setattr(app, "_comic_image_vision_model", _forbidden)
    monkeypatch.setattr(app, "_comic_constraint_model", _forbidden)
    checked = app.comic_story_operation(cid, "review", _request(app, pid, cid, "review-six-fixes"))
    assert checked["review"]["status"] == "pass" and checked["review"]["findings"] == []
    assert checked["review_reused"] and checked["story"]["shots"] == result["story"]["shots"]
    assert app.comic_story_operation(cid, "generate", request) == result


@pytest.mark.parametrize("invalid", ["stale_quote", "unbound_reference", "over_capacity"])
def test_timing_projection_cannot_apply_an_unverified_or_illegal_budget(invalid):
    from types import SimpleNamespace

    from kantoku.domains.comic.stories import ComicStoryService

    service = ComicStoryService(SimpleNamespace(projects=None, runtime=None))
    draft = ComicStoryDraft.model_validate({"shots": [{"shot_id": "one",
        "image": {"image_id": "a", "version": 1}, "action": "Put the lantern away.",
        "transition": "Take and open the compass.", "duration_seconds": 3.5}]})
    finding = {"severity": "error", "shot_id": "one", "field": "duration_seconds",
        "code": "duration_conflict", "evidence": "3.5", "message": "Complete the full handoff.",
        "timing_plan": [{"field": "action", "evidence": draft.shots[0].action, "seconds": 3.0},
                        {"field": "transition", "evidence": draft.shots[0].transition,
                         "seconds": 2.5}]}
    if invalid == "stale_quote":
        finding["timing_plan"][0]["evidence"] = "An action absent from the current draft."
    elif invalid == "unbound_reference":
        finding["evidence_id"] = "shot.1.duration_seconds:obsolete"
    else:
        for beat in finding["timing_plan"]:
            beat["seconds"] = 40.0
    assert service._minimum_durations(draft, [finding]) == {}
    assert draft.shots[0].duration_seconds == 3.5


@pytest.mark.parametrize("subjects", [("sword", "knife"), ("lantern", "compass")])
def test_organize_binds_scrambled_sources_and_repairs_before_the_next_starting_frame(
    story_app, monkeypatch, subjects,
):
    app = story_app
    pid, cid, first = _setup(app)
    second = _upload(app, pid, cid, content=_png(66))
    _select(app, cid, second)
    skeleton = _draft(first, second, first)
    for shot, subject in zip(skeleton["shots"], (*subjects, subjects[0]), strict=True):
        shot.update(action=f"Hold the {subject}.", transition="Keep holding it.")
    original = _save(app, pid, cid, skeleton)["story"]
    sources = [{"image": _ref(image), "user_requirements": [f"Hold the {subject}."]}
               for image, subject in zip((second, first), reversed(subjects), strict=True)]
    monkeypatch.setattr(app.comic_stories, "_source_contexts", lambda *_args, **_kwargs: sources)
    fixed = copy.deepcopy(skeleton)
    fixed["shots"][0].update(transition=f"Put away the {subjects[0]}, take the {subjects[1]}.",
                             duration_seconds=8.0)
    fixed["shots"][1].update(transition=f"Put away the {subjects[1]}, take the {subjects[0]}.",
                             duration_seconds=8.0)
    visions, reviews = [], []
    def assert_context(payload):
        assert [source["image"] for source in payload["image_sources"]] == [
            _ref(first), _ref(second)]
        assert [image["image_id"] for image in payload["images"]] == [
            first["image_id"], second["image_id"]]
        assert payload["image_sources"][0]["applies_to_shots"] == ["story-shot-1", "story-shot-3"]
        assert payload["shot_source_map"]["story-shot-3"]["image_source_index"] == 0
        boundaries = payload["shot_boundaries"]
        assert len(boundaries) == 2
        assert [(edge["from_shot_id"], edge["to_shot_id"]) for edge in boundaries] == [
            ("story-shot-1", "story-shot-2"), ("story-shot-2", "story-shot-3")]
        assert boundaries[0]["handoff_field"] in boundaries[0]["before_cut_fields"]
        assert boundaries[1]["handoff_field"].startswith("shot.2.transition:")

    def vision(messages):
        payload = json.loads(messages[1]["content"][0]["text"])
        assert_context(payload)
        visions.append(payload)
        # Actual visual attachments follow story order and are deduplicated for the repeated image.
        labels = [part["text"] for part in messages[1]["content"][1:] if part["type"] == "text"]
        assert len(labels) == 2 and first["image_id"] in labels[0]
        if len(visions) == 1:
            return json.dumps(skeleton)
        assert app.get_comic_story(cid)["story"] == original
        return json.dumps(fixed)

    def review(messages):
        payload = json.loads(messages[1]["content"])
        if payload.get("stage") == "story_finding_audit":
            return json.dumps({"checks": [{"finding": int(index), "verdict": "retain",
                "reason": "The missing before-cut movement is real."}
                for index in payload["findings"]]})
        assert_context(payload)
        reviews.append(payload)
        if payload["draft"]["shots"] == fixed["shots"]:
            return json.dumps({"status": "pass", "findings": []})
        findings = []
        for edge in payload["shot_boundaries"]:
            primary = next(ref for ref, coordinate in payload["review_fields"].items()
                           if coordinate == {"shot_id": edge["to_shot_id"], "field": "action"})
            findings.append({"severity": "error", "shot_id": edge["to_shot_id"],
                "field": "action", "code": "continuity", "evidence_id": primary,
                "related_fields": [edge["handoff_field"]],
                "message": "The handoff must occur before the next image, not after it.",
                "suggestion": "Show the put-away and take-out movement before cutting."})
        return json.dumps({"status": "needs_revision", "findings": findings})

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    request = {**_request(app, pid, cid, "ordered-boundary-repair"), "draft": skeleton,
               "images": [_ref(second), _ref(first)], "instruction": "Preserve all fixed frames."}
    result = app.comic_story_operation(cid, "generate", request)
    assert len(visions) == len(reviews) == 2
    assert result["generation_status"] == "ready" and result["story"]["shots"] == fixed["shots"]
    assert result["story"]["version"] == original["version"] + 1
    assert result["can_confirm"] and result["review"]["findings"] == []
    monkeypatch.setattr(app, "_comic_constraint_model", _forbidden)
    monkeypatch.setattr(app, "_comic_image_vision_model", _forbidden)
    assert app.comic_story_operation(cid, "generate", request) == result
    app.comic_story_operation(cid, "confirm", _request(app, pid, cid, "confirm-ordered-boundaries"))
    checked = app.comic_story_operation(cid, "review", _request(app, pid, cid, "check-fixed-draft"))
    assert checked["review_reused"] and checked["review"]["findings"] == []


def test_quote_audit_transport_unknown_stops_without_clearing_or_resubmitting():
    from types import SimpleNamespace

    from kantoku.domains.comic.models import ComicStoryReviewOutput
    from kantoku.domains.comic.stories import ComicStoryService

    service = ComicStoryService(SimpleNamespace(projects=None, runtime=None))
    draft = ComicStoryDraft.model_validate({"title": "New title"})
    output = ComicStoryReviewOutput.model_validate({"status": "needs_revision", "findings": [{
        "severity": "error", "field": "title", "message": 'Title says "Old title".',
        "evidence": "New title"}]})
    calls = []
    def unavailable(messages):
        calls.append(messages)
        raise ToolError("Offline result unknown")
    with pytest.raises(ToolError, match="unknown"):
        service._audit_finding_quotes(draft, output, unavailable, [])
    assert len(calls) == 1 and len(output.findings) == 1


@pytest.mark.parametrize("current_duration,verdict,expected_audit,expected_removed", [
    (10.0, "stale_current_quote", True, True),
    (10.0, "retain", True, False),
    (3.0, "retain", False, False),
])
def test_audit_checks_stale_current_duration_without_discarding_a_real_finding(
    current_duration, verdict, expected_audit, expected_removed,
):
    from types import SimpleNamespace

    from kantoku.domains.comic.models import ComicStoryReviewOutput
    from kantoku.domains.comic.stories import ComicStoryService

    service = ComicStoryService(SimpleNamespace(projects=None, runtime=None))
    draft = ComicStoryDraft.model_validate({"shots": [{"shot_id": "one",
        "image": {"image_id": "a", "version": 1}, "action": "Draw the sword.",
        "transition": "Parry the arrow.", "duration_seconds": current_duration}]})
    fields = service._review_fields(draft)
    primary = next(key for key, value in fields.items() if value["field"] == "duration_seconds")
    output = ComicStoryReviewOutput.model_validate({"status": "needs_revision", "findings": [{
        "severity": "error", "shot_id": "one", "field": "duration_seconds", "code": "other",
        "evidence_id": primary, "evidence": str(current_duration),
        "message": "本镜需在3秒内完成拔剑与格挡，时长不足。", "suggestion": "检查完整节拍。"}]})
    calls = []

    def audit(messages):
        calls.append(messages)
        payload = json.loads(messages[1]["content"])
        assert payload["audit_requirements"]["1"]["absent_current_quotes"] == ["3秒"]
        if verdict == "retain":
            return json.dumps({"checks": [{"finding": 1, "verdict": "retain",
                "reason": "Another current conflict is still present."}]})
        return json.dumps({"checks": [{"finding": 1, "verdict": "stale_current_quote",
            "field_id": primary, "claimed_quote": "3秒", "current_quote": "10.0",
            "reason": "The only claimed duration is from an old draft."}]})

    service._audit_finding_quotes(draft, output, audit, [])
    assert bool(calls) == expected_audit
    assert bool(output.findings) != expected_removed


@pytest.mark.parametrize("code", ["continuity", "other"])
@pytest.mark.parametrize("invalid", [
    None, "ids", "stale_id", "quote", "unrelated", "primary", "transition", "source",
    "source_quote", "model_choice",
])
def test_cross_image_audit_requires_bound_current_and_source_counterevidence(invalid, code):
    from types import SimpleNamespace

    from kantoku.domains.comic.models import ComicStoryReviewOutput
    from kantoku.domains.comic.stories import ComicStoryService

    service = ComicStoryService(SimpleNamespace(projects=None, runtime=None))
    draft = ComicStoryDraft.model_validate({"shots": [
        {"shot_id": "lantern", "image": {"image_id": "a", "version": 1},
         "action": "Hold the closed lantern.",
         "transition": "Clip the lantern to the belt, take out and open the compass."},
        {"shot_id": "compass", "image": {"image_id": "b", "version": 1},
         "action": "Hold the open compass.", "transition": "Cut to the landscape."},
        {"shot_id": "unrelated", "image": {"image_id": "c", "version": 1},
         "action": "Show the landscape."}]})
    sources = [{"image": shot.image.model_dump(), "constraints": [{"requirement": shot.action}]}
               for shot in draft.shots]
    if invalid == "model_choice":
        sources[0]["constraints"][0]["origin"] = "model_choice"
    fields = service._review_fields(draft)
    def ref(shot, field):
        return next(key for key, value in fields.items()
                    if value == {"shot_id": shot, "field": field})
    primary, transition = ref("compass", "action"), ref("lantern", "transition")
    output = ComicStoryReviewOutput.model_validate({"status": "needs_revision", "findings": [{
        "severity": "error", "shot_id": "compass", "field": "action",
        "evidence_id": primary, "related_fields": [transition], "code": code,
        "message": "The preceding shot does not explain how the compass is obtained.",
        "suggestion": "Add a handoff."}]})
    calls = []

    def audit(messages):
        payload = json.loads(messages[1]["content"])
        calls.append(payload)
        quotes = {primary: draft.shots[1].action, transition: draft.shots[0].transition}
        quotes.update({key: value["quote"] for key, value in payload["source_fields"].items()})
        if invalid == "ids":
            quotes = {key: key for key in quotes}
        elif invalid == "stale_id":
            quotes[transition] = transition + "-old"
        elif invalid == "quote":
            quotes[transition] = "A handoff that is not in the current draft."
        elif invalid == "unrelated":
            quotes[ref("unrelated", "action")] = draft.shots[2].action
        elif invalid == "primary":
            quotes.pop(primary)
        elif invalid == "transition":
            quotes.pop(transition)
        elif invalid == "source":
            quotes = {key: value for key, value in quotes.items() if key in fields}
        elif invalid == "source_quote":
            quotes[next(iter(payload["source_fields"]))] = "A fact absent from the fixed source."
        return json.dumps({"checks": [{"finding": 1, "verdict": "already_satisfied",
            "supporting_quotes": quotes, "reason": "The current transition supplies it."}]})

    service._audit_finding_quotes(draft, output, audit, sources)
    assert len(calls) == 1
    assert bool(output.findings) == (invalid not in (None, "ids"))
    assert sources[0]["constraints"][0]["requirement"] == "Hold the closed lantern."


def test_cross_image_audit_corrects_bad_repair_advice_without_dismissing_the_real_issue():
    from types import SimpleNamespace

    from kantoku.domains.comic.models import ComicStoryReviewOutput
    from kantoku.domains.comic.stories import ComicStoryService

    service = ComicStoryService(SimpleNamespace(projects=None, runtime=None))
    draft = ComicStoryDraft.model_validate({"shots": [
        {"shot_id": "sword", "image": {"image_id": "a", "version": 1},
         "action": "Parry with the sword.", "transition": "Stand with the sword."},
        {"shot_id": "knife", "image": {"image_id": "b", "version": 1},
         "action": "Hold the knife."}]})
    sources = [{"image": shot.image.model_dump(), "user_requirements": [shot.action]}
               for shot in draft.shots]
    fields = service._review_fields(draft)
    primary = next(key for key, value in fields.items()
                   if value == {"shot_id": "knife", "field": "action"})
    previous = next(key for key, value in fields.items()
                    if value == {"shot_id": "sword", "field": "transition"})
    output = ComicStoryReviewOutput.model_validate({"status": "needs_revision", "findings": [{
        "severity": "error", "shot_id": "knife", "field": "action", "evidence_id": primary,
        "related_fields": [previous], "code": "continuity", "message": "A handoff is missing.",
        "suggestion": "Change the knife into the sword in the second starting frame."}]})
    suggestion = "Sheathe the sword and draw the knife before cutting to the next frame."

    def audit(messages):
        payload = json.loads(messages[1]["content"])
        quotes = {primary: draft.shots[1].action, previous: draft.shots[0].transition}
        quotes.update({key: value["quote"] for key, value in payload["source_fields"].items()})
        return json.dumps({"checks": [{"finding": 1, "verdict": "retain",
            "supporting_quotes": quotes, "corrected_suggestion": suggestion,
            "reason": "Both fixed frames must stay; the missing event belongs before the cut."}]})

    service._audit_finding_quotes(draft, output, audit, sources)
    assert len(output.findings) == 1 and output.findings[0].severity == "error"
    assert output.findings[0].suggestion == suggestion
    assert draft.shots[1].action == "Hold the knife."


def test_incoming_continuity_repairs_can_fix_the_preceding_outgoing_transition():
    from kantoku.domains.comic.stories import ComicStoryService

    draft = ComicStoryDraft.model_validate({"shots": [
        {"shot_id": str(index), "image": {"image_id": str(index), "version": 1}}
        for index in range(4)]})
    scope = ComicStoryService._repair_scope(draft, [{"severity": "error", "shot_id": "2",
        "field": "action", "code": "continuity", "related_fields": []}])
    assert set(scope["shots"]) == {"1", "2"}
    assert all("image" not in fields for fields in scope["shots"].values())
    following = next(ref for ref, coordinate in ComicStoryService._review_fields(draft).items()
                     if coordinate == {"shot_id": "3", "field": "action"})
    outgoing = ComicStoryService._repair_scope(draft, [{"severity": "error", "shot_id": "2",
        "field": "action", "code": "continuity", "related_fields": [following]}])
    assert set(outgoing["shots"]) == {"2", "3"}, "An explicit outgoing issue cannot rewrite shot 1"
    previous_image = next(ref for ref, coordinate in ComicStoryService._review_fields(draft).items()
                          if coordinate == {"shot_id": "1", "field": "image"})
    image_conflict = [{"severity": "error", "shot_id": "2", "field": "action",
                       "code": "image_conflict", "related_fields": [previous_image]}]
    scoped = ComicStoryService._repair_scope(draft, image_conflict)["shots"]
    assert scoped["1"] == ["duration_seconds", "transition"]
    unrelated_image = next(ref for ref, coordinate in
        ComicStoryService._review_fields(draft).items()
        if coordinate == {"shot_id": "0", "field": "image"})
    image_conflict[0]["related_fields"] = [unrelated_image]
    assert set(ComicStoryService._repair_scope(draft, image_conflict)["shots"]) == {"2"}


def test_a_unique_timing_quote_can_be_bound_to_its_actual_phase():
    from types import SimpleNamespace

    from kantoku.domains.comic.models import ComicStoryReviewOutput
    from kantoku.domains.comic.stories import ComicStoryService

    service = ComicStoryService(SimpleNamespace(projects=None, runtime=None))
    draft = ComicStoryDraft.model_validate({"shots": [{"shot_id": "one",
        "image": {"image_id": "a", "version": 1}, "action": "Parry the arrow.",
        "transition": "Stand up, then take the knife.", "duration_seconds": 5.5}]})
    output = ComicStoryReviewOutput.model_validate({"status": "needs_revision", "findings": [{
        "severity": "error", "shot_id": "one", "field": "duration_seconds",
        "code": "duration_conflict", "evidence": "5.5", "message": "The handoff needs time.",
        "timing_plan": [{"field": "action", "evidence": "Parry the arrow.", "seconds": 2.5},
            {"field": "action", "evidence": "Stand up", "seconds": 1.5},
            {"field": "transition", "evidence": "take the knife", "seconds": 4.0}]}]})
    checked, issues = service._review_findings(draft, output)
    assert issues == [] and len(checked) == 1
    assert [beat.field for beat in checked[0].timing_plan] == ["action", "transition", "transition"]
    assert service._minimum_durations(draft, [checked[0].model_dump()]) == {"one": 8.0}


@pytest.mark.parametrize("invalid", ["wrong_quote", "fits"])
def test_unverified_other_timing_plan_is_not_forwarded_as_trusted_budget(invalid):
    from types import SimpleNamespace

    from kantoku.domains.comic.models import ComicStoryReviewOutput
    from kantoku.domains.comic.stories import ComicStoryService

    service = ComicStoryService(SimpleNamespace(projects=None, runtime=None))
    draft = ComicStoryDraft.model_validate({"shots": [{"shot_id": "one",
        "image": {"image_id": "a", "version": 1}, "action": "Parry the arrow.",
        "transition": "Put away the sword.", "duration_seconds": 5.0}]})
    timing = [{"field": "action", "evidence": draft.shots[0].action, "seconds": 4.0},
              {"field": "transition", "evidence": draft.shots[0].transition, "seconds": 4.0}]
    if invalid == "wrong_quote":
        timing[0]["evidence"] = "An action absent from the current story."
    else:
        timing[0]["seconds"] = timing[1]["seconds"] = 2.0
    output = ComicStoryReviewOutput.model_validate({"status": "needs_revision", "findings": [{
        "severity": "error", "shot_id": "one", "field": "duration_seconds", "code": "other",
        "evidence": "5.0", "message": "The timing still needs review.", "timing_plan": timing}]})
    checked, issues = service._review_findings(draft, output)
    assert issues == [] and len(checked) == 1
    assert checked[0].severity == "error" and checked[0].timing_plan == []


@pytest.mark.parametrize("code", ["image_conflict", "other"])
def test_image_conflict_can_repair_an_explicitly_linked_previous_image_handoff(
    story_app, monkeypatch, code,
):
    app = story_app
    pid, cid, first = _setup(app)
    second = _upload(app, pid, cid, content=_png(67))
    _select(app, cid, second)
    draft = _draft(first, second, first)
    draft["shots"][0].update(action="Parry with a sword.",
                             transition="Sheathe sword and take knife.")
    draft["shots"][1].update(action="Hold a knife.", transition="Keep holding the knife.")
    draft["shots"][2].update(action="Raise the knife.", transition="Cut to black.")
    original = _save(app, pid, cid, draft)["story"]
    fixed = copy.deepcopy(draft)
    fixed["shots"][1]["transition"] = "Put the knife away and draw the sword before the cut."
    fixed["shots"][2]["action"] = "Raise the sword."
    sources = [{"image": _ref(first), "user_requirements": ["The starting frame has a sword."]},
               {"image": _ref(second), "user_requirements": ["The starting frame has a knife."]}]
    monkeypatch.setattr(app.comic_stories, "_source_contexts", lambda *_args, **_kwargs: sources)
    visions, reviews = [], []

    def vision(messages):
        visions.append(messages)
        return json.dumps(draft if len(visions) == 1 else fixed)

    def review(messages):
        payload = json.loads(messages[1]["content"])
        if payload.get("stage") == "story_finding_audit":
            return json.dumps({"checks": [{"finding": int(index), "verdict": "retain",
                "reason": "The source conflict must be fixed."} for index in payload["findings"]]})
        reviews.append(payload)
        shots = payload["draft"]["shots"]
        if shots == fixed["shots"]:
            return json.dumps({"status": "pass", "findings": []})
        fields = payload["review_fields"]
        def ref(shot_id, field):
            return next(key for key, value in fields.items()
                        if value == {"shot_id": shot_id, "field": field})
        return json.dumps({"status": "needs_revision", "findings": [{
            "severity": "error", "shot_id": "story-shot-3", "field": "action",
            "code": code, "evidence_id": ref("story-shot-3", "action"),
            "related_fields": [ref("story-shot-2", "image"), ref("story-shot-3", "image")],
            "message": "The third frame requires a sword, not a knife.",
            "suggestion": "Respect both image sources and perform the handoff before the cut."}]})

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    request = {**_request(app, pid, cid, "source-bound-handoff"), "draft": draft,
               "images": [_ref(first), _ref(second)], "instruction": draft["instruction"]}
    result = app.comic_story_operation(cid, "generate", request)
    assert result["generation_status"] == "ready"
    assert result["story"]["version"] == original["version"] + 1
    assert result["story"]["shots"] == fixed["shots"]
    assert len(visions) == len(reviews) == 2
    checked = app.comic_story_operation(cid, "review", _request(app, pid, cid, "check-handoff"))
    assert checked["review"]["findings"] == [] and checked["review_reused"]


def test_source_compatible_handoff_is_repaired_reviewed_and_saved_in_one_organize_operation(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, first = _setup(app)
    second = _upload(app, pid, cid, content=_png(33))
    _select(app, cid, second)
    skeleton = _draft(first, second)
    skeleton["shots"][0].update(action="Parry with the sword.", transition="Stand with the sword.")
    skeleton["shots"][1]["action"] = "Hold the knife."
    original = _save(app, pid, cid, skeleton)["story"]
    fixed = copy.deepcopy(skeleton)
    fixed["shots"][0].update(
        transition="Sheathe the sword, take the knife from the belt and grip it before the cut.",
        duration_seconds=8.0)
    sources = [{"image": _ref(image), "user_requirements": [shot["action"]]}
               for image, shot in zip((first, second), skeleton["shots"], strict=True)]
    monkeypatch.setattr(app.comic_stories, "_source_contexts", lambda *_args, **_kwargs: sources)
    vision_calls, review_calls, audit_calls = [], [], []

    def vision(messages):
        payload = json.loads(messages[-1]["content"][0]["text"])
        vision_calls.append(payload)
        if len(vision_calls) == 1:
            return json.dumps(skeleton)
        assert set(payload["repair_scope"]["shots"]) == {"story-shot-1", "story-shot-2"}
        assert "Sheathe" in payload["issues"][0]["suggestion"]
        assert app.get_comic_story(cid)["story"] == original
        return json.dumps(fixed)

    def review(messages):
        payload = json.loads(messages[1]["content"])
        fields = payload["review_fields"]
        primary = next(key for key, value in fields.items()
                       if value == {"shot_id": "story-shot-2", "field": "action"})
        previous = next(key for key, value in fields.items()
                        if value == {"shot_id": "story-shot-1", "field": "transition"})
        if payload.get("stage") == "story_finding_audit":
            audit_calls.append(payload)
            quotes = {primary: skeleton["shots"][1]["action"],
                      previous: skeleton["shots"][0]["transition"]}
            quotes.update({key: value["quote"] for key, value in payload["source_fields"].items()})
            return json.dumps({"checks": [{"finding": 1, "verdict": "retain",
                "corrected_suggestion": fixed["shots"][0]["transition"],
                "supporting_quotes": quotes, "reason": "Keep each fixed starting frame."}]})
        review_calls.append(payload)
        if len(review_calls) == 1:
            return json.dumps({"status": "needs_revision", "findings": [{
                "severity": "error", "shot_id": "story-shot-2", "field": "action",
                "evidence_id": primary, "related_fields": [previous], "code": "continuity",
                "message": "No handoff between the fixed frames.",
                "suggestion": "Use a sword in both images."}]})
        assert payload["draft"]["shots"] == fixed["shots"]
        return json.dumps({"status": "pass", "findings": []})

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    result = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "grounded-handoff"), "images": [_ref(first), _ref(second)],
        "draft": skeleton, "instruction": "Preserve the distinct fixed frames."})
    assert len(vision_calls) == len(review_calls) == 2 and len(audit_calls) == 1
    assert result["generation_status"] == "ready" and result["can_confirm"]
    assert result["story"]["version"] == original["version"] + 1
    assert result["story"]["shots"] == fixed["shots"]
    run = app.runtime_store.get_run(result["run_id"])
    assert run.state["story_review_audits"][0]["checks"][0]["verdict"] == "retain"
    app.comic_story_operation(cid, "confirm", _request(app, pid, cid, "confirm-handoff"))
    again = app.comic_story_operation(cid, "review", _request(app, pid, cid, "review-handoff"))
    assert again["review_reused"] and again["review"]["findings"] == []
    assert len(review_calls) == 2


def test_repair_transport_unknown_is_not_treated_as_an_unchanged_draft():
    from types import SimpleNamespace

    from kantoku.domains.comic.stories import ComicStoryService

    service = ComicStoryService(SimpleNamespace(projects=None, runtime=None))
    draft = ComicStoryDraft.model_validate({"title": "A sequence", "shots": [{
        "shot_id": "one", "image": {"image_id": "image", "version": 1},
        "camera": "Move right."}]})
    calls = []
    def unavailable(messages):
        calls.append(messages)
        raise ToolError("Offline repair submission unknown")
    with pytest.raises(ToolError, match="submission unknown"):
        service._repair(draft, [{"severity": "error", "shot_id": "one", "field": "camera",
            "code": "camera_conflict"}], [], [], unavailable, "Coordinate the camera.")
    assert len(calls) == 1


def test_unfinished_candidate_cannot_be_promoted_and_ai_continues_it_before_delivery(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    draft = _draft(image)
    repaired = copy.deepcopy(draft)
    repaired["shots"][0]["camera"] = "Track forward, then stop."
    visions = []
    def vision(messages):
        visions.append(messages)
        return json.dumps(draft)
    def review(messages):
        current = json.loads(messages[1]["content"])["draft"]
        if current["shots"][0]["camera"] == repaired["shots"][0]["camera"]:
            return json.dumps({"status": "pass", "findings": []})
        return json.dumps({"status": "needs_revision", "findings": [{
            "severity": "error", "shot_id": "story-shot-1", "field": "camera",
            "code": "camera_conflict", "evidence": current["shots"][0]["camera"],
            "message": "A real movement conflict remains.",
            "suggestion": "Coordinate the movement."}]})
    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    request = {**_request(app, pid, cid, "quality-pending"), "draft": draft,
               "images": [_ref(image)], "instruction": "Track forward, then stop."}
    pending = app.comic_story_operation(cid, "generate", request)
    assert pending["generation_status"] == "needs_revision"
    with pytest.raises(ToolError, match="候选.*未通过"):
        app.comic_story_operation(cid, "adopt-candidate", {
            **_request(app, pid, cid, "reject-unreviewed"),
            "candidate_run_id": pending["candidate_run_id"]})
    assert app.get_comic_story(cid)["story"] is None
    inputs = []
    def repair(messages):
        payload = json.loads(messages[1]["content"][0]["text"])
        inputs.append(payload)
        assert payload["current_draft"] == pending["candidate_draft"]
        return json.dumps(repaired)
    monkeypatch.setattr(app, "_comic_image_vision_model", repair)
    rechecks = []
    def recheck(messages):
        current = json.loads(messages[1]["content"])["draft"]
        assert current["shots"] == repaired["shots"], "Known errors must be repaired first"
        rechecks.append(current)
        return json.dumps({"status": "pass", "findings": []})
    monkeypatch.setattr(app, "_comic_constraint_model", recheck)
    payload_calls = []
    build_payload = app.comic_stories._vision_payload
    def record_payload(*args, **kwargs):
        payload_calls.append(1)
        return build_payload(*args, **kwargs)
    monkeypatch.setattr(app.comic_stories, "_vision_payload", record_payload)
    continued_request = {
        **_request(app, pid, cid, "continue-quality"), "draft": pending["candidate_draft"],
        "candidate_run_id": pending["candidate_run_id"], "images": [_ref(image)],
        "instruction": request["instruction"]}
    result = app.comic_story_operation(cid, "generate", continued_request)
    assert len(payload_calls) == 1  # Only the actual repair needs image encoding.
    assert len(inputs) == 1  # 直接校验并修复原候选，没有重新生成另一份初稿。
    assert len(rechecks) == 1  # 已知错误稿不靠再次抽校验结论变成通过。
    assert result["generation_status"] == "ready" and result["can_confirm"]
    assert result["story"]["shots"] == repaired["shots"]
    assert result["review"]["status"] == "pass" and not result["review"]["findings"]
    monkeypatch.setattr(app, "_comic_constraint_model", _forbidden)
    monkeypatch.setattr(app, "_comic_image_vision_model", _forbidden)
    assert app.comic_story_operation(cid, "generate", continued_request) == result
    with pytest.raises(ToolError, match="候选与当前故事"):
        app.comic_story_operation(cid, "generate", {
            **_request(app, pid, cid, "stale-candidate"), "draft": pending["candidate_draft"],
            "candidate_run_id": pending["candidate_run_id"], "images": [_ref(image)],
            "instruction": request["instruction"]})


def test_final_reconciliation_is_independently_checked_before_delivery(story_app, monkeypatch):
    app = story_app
    pid, cid, image = _setup(app)
    original = _draft(image, image, image)
    drafts = [copy.deepcopy(original) for _ in range(4)]
    drafts[1]["shots"][0]["action"] = "Pick up the signal lamp before handing it over."
    drafts[2] = copy.deepcopy(drafts[1])
    drafts[2]["shots"][1]["action"] = "Put down the signal lamp and take the beacon."
    drafts[3] = copy.deepcopy(drafts[2])
    drafts[3]["shots"][2]["action"] = "Take the beacon after the handoff, then raise it."
    calls = []
    def vision(messages):
        payload = json.loads(messages[1]["content"][0]["text"])
        calls.append(payload)
        return json.dumps(drafts[len(calls) - 1])
    def review(messages):
        current = json.loads(messages[1]["content"])["draft"]
        if current["shots"] == drafts[3]["shots"]:
            return json.dumps({"status": "pass", "findings": []})
        index = next(i for i in range(3)
                     if current["shots"][i]["action"] != drafts[3]["shots"][i]["action"])
        return json.dumps({"status": "needs_revision", "findings": [{
            "severity": "error", "shot_id": current["shots"][index]["shot_id"],
            "field": "action", "code": "continuity", "evidence": current["shots"][index]["action"],
            "message": "The handoff is not yet coherent.",
            "suggestion": "Complete this handoff."}]})
    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    result = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "complete-quality"), "draft": original,
        "images": [_ref(image)],
        "instruction": "Hand over the lamp, then take and raise the beacon."})
    assert result["generation_status"] == "ready"
    assert result["story"]["shots"] == drafts[3]["shots"]
    assert calls[-1]["repair_strategy"] == "reconcile"
    assert len(calls[-1]["repair_history"]) == 3
    assert len(app.runtime_store.get_run(result["run_id"]).state["story_attempts"]) == 4


def test_content_conflict_returns_waiting_candidate_with_diagnostics_and_replays_without_models(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    saved = _save(app, pid, cid, _draft(image))
    calls = []
    finding = {"severity": "error", "shot_id": "story-shot-1", "field": "camera",
               "code": "requirement_conflict", "evidence": "Camera remains still.",
               "message": "Two explicit simultaneous requirements conflict.",
               "suggestion": "Choose which simultaneous camera requirement should apply."}
    monkeypatch.setattr(app, "_comic_image_vision_model", lambda messages: (
        calls.append("vision") or json.dumps(_draft(image))))
    monkeypatch.setattr(app, "_comic_constraint_model", lambda messages: (
        calls.append("review") or json.dumps({"status": "needs_revision", "findings": [finding]})))
    request = {**_request(app, pid, cid, "conflicting-input"), "images": [_ref(image)],
               "draft": _draft(image), "instruction": "Keep both explicit requirements."}
    result = app.comic_story_operation(cid, "generate", request)
    assert result["generation_status"] == "needs_revision"
    assert result["story"] == saved["story"]
    assert result["candidate_review"]["findings"][0]["suggestion"] == finding["suggestion"]
    run = app.runtime_store.get_run(result["run_id"])
    assert run.status.value == "waiting" and run.error is None
    assert run.state["task_status"] == "needs_revision"
    assert not result["can_confirm"] and result["confirmation"] is None
    before = list(calls)
    assert app.comic_story_operation(cid, "generate", request) == result
    assert calls == before
    assert app.get_comic_story(cid)["story"] == saved["story"]


def test_ai_repairs_only_reported_fields_before_publishing_a_passing_draft(story_app, monkeypatch):
    app = story_app
    pid, cid, image = _setup(app)
    draft = _draft(image)
    fixed = copy.deepcopy(draft)
    fixed["shots"][0]["camera"] = "Slowly move forward."
    fixed["title"] = "Unrequested rewrite must be ignored"
    outputs = iter([draft, fixed])
    review_outputs = iter([{"status": "needs_revision", "findings": [{
        "severity": "error", "shot_id": "story-shot-1", "field": "camera",
        "code": "camera_conflict", "message": "Camera conflicts with the requested movement.",
        "evidence": draft["shots"][0]["camera"], "suggestion": "Move the camera forward."}]},
        {"status": "pass", "findings": []}])
    monkeypatch.setattr(app, "_comic_image_vision_model", lambda _: json.dumps(next(outputs)))
    monkeypatch.setattr(app, "_comic_constraint_model", lambda _: json.dumps(next(review_outputs)))
    generated = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "repair"), "images": [_ref(image)], "instruction": "Push in."})
    assert generated["story"]["title"] == draft["title"]
    assert generated["story"]["shots"][0]["camera"] == "Slowly move forward."
    assert generated["review"]["status"] == "pass"
    monkeypatch.setattr(app, "_comic_constraint_model", _forbidden)
    checked = app.comic_story_operation(cid, "review", _request(app, pid, cid, "check-repair"))
    assert checked["review"]["status"] == "pass" and checked["review_reused"]


@pytest.mark.parametrize("structural", [False, True])
def test_unresolved_ai_errors_preserve_candidate_and_never_replace_current_story(
    story_app, monkeypatch, structural,
):
    app = story_app
    pid, cid, image = _setup(app)
    original = _save(app, pid, cid, _draft(image))["story"]
    draft = _draft(image, title="Invalid generated candidate")
    if structural:
        draft["shots"][0]["transition"] = ""
    output = {"status": "needs_revision", "findings": [{
        "severity": "error", "shot_id": "story-shot-1", "field": "camera",
        "code": "camera_conflict", "message": "Conflicting camera movement.",
        "evidence": draft["shots"][0]["camera"], "suggestion": "Remove the conflicting movement."}]}
    monkeypatch.setattr(app, "_comic_image_vision_model", lambda _: json.dumps(draft))
    monkeypatch.setattr(app, "_comic_constraint_model",
                        _forbidden if structural else lambda _: json.dumps(output))
    result = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "unresolved"), "images": [_ref(image)],
        "instruction": "A new sequence."})
    assert result["generation_status"] == "needs_revision"
    assert result["candidate_review"]["findings"]
    assert app.runtime_store.get_run(result["run_id"]).status.value == "waiting"
    assert app.get_comic_story(cid)["story"] == original
    runs = app.runtime_store.list_runs(domain="comic")
    candidates = [run.state["story_candidate"] for run in runs
                  if run.state.get("story_candidate")]
    assert len(candidates) == 1 and candidates[0]["review"]["status"] == "needs_revision"


def test_user_edit_is_rechecked_and_errors_block_confirmation_until_fixed(story_app, monkeypatch):
    app = story_app
    pid, cid, image = _setup(app)
    _save(app, pid, cid, _draft(image))
    _confirm(app, pid, cid, monkeypatch)
    edited = _draft(image)
    edited["shots"][0]["camera"] = "Move left and right simultaneously."
    _save(app, pid, cid, edited, "bad-edit")
    assert app.get_comic_story(cid)["review"] is None
    output = {"status": "pass", "findings": [{
        "severity": "error", "shot_id": "story-shot-1", "field": "camera",
        "code": "camera_conflict", "message": "Choose one camera direction.",
        "evidence": edited["shots"][0]["camera"], "suggestion": "Move left only."}]}
    checked, _ = _review(app, pid, cid, monkeypatch, output=output, key="bad-edit-review")
    assert checked["review"]["status"] == "needs_revision"
    assert checked["review"]["findings"][0]["suggestion"] == "Move left only."
    with pytest.raises(ToolError, match="尚未通过校验"):
        app.comic_story_operation(cid, "confirm", _request(app, pid, cid, "bad-edit-confirm"))
    with pytest.raises(ToolError):
        app.comic_story_operation(cid, "exports", _request(app, pid, cid, "bad-edit-export"))
    edited["shots"][0]["camera"] = "Move left only."
    _save(app, pid, cid, edited, "fixed-edit")
    assert _confirm(app, pid, cid, monkeypatch, key="fixed-confirm")["confirmation"]["confirmed"]


def test_review_rejects_failure_without_location_and_supplies_legacy_suggestion(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    draft = _draft(image)
    _save(app, pid, cid, draft)
    with pytest.raises(ToolError, match="位置和修改建议"):
        _review(app, pid, cid, monkeypatch,
                output={"status": "needs_revision", "findings": []}, key="empty-findings")
    checked, _ = _review(app, pid, cid, monkeypatch, key="legacy", output={
        "status": "needs_revision", "findings": [{"severity": "error", "shot_id": "story-shot-1",
        "field": "action", "message": "Describe one clear action.",
        "evidence": draft["shots"][0]["action"]}]})
    assert checked["review"]["findings"][0]["suggestion"]


@pytest.mark.parametrize("representation", [
    "whitespace", "duration", "duration_unit", "image_json",
])
def test_review_accepts_equivalent_field_formatting_without_dropping_findings(
    story_app, monkeypatch, representation,
):
    app = story_app
    pid, cid, image = _setup(app)
    draft = _draft(image)
    draft["shots"][0]["camera"] = "Track  slowly\nforward."
    _save(app, pid, cid, draft)
    field, evidence = {
        "whitespace": ("camera", "Track slowly forward."),
        "duration": ("duration_seconds", "4"),
        "duration_unit": ("duration_seconds", "4.00 秒"),
        "image_json": ("image", json.dumps({"version": image["version"],
                                            "image_id": image["image_id"]}, separators=(",", ":"))),
    }[representation]
    checked, calls = _review(app, pid, cid, monkeypatch, output={
        "status": "needs_revision", "findings": [{
            "severity": "error", "shot_id": "story-shot-1", "field": field,
            "message": "Resolve this field conflict.", "evidence": evidence,
            "suggestion": "Choose one consistent value."}]})
    assert len(calls) == 1
    review = checked["review"]
    assert review["status"] == "needs_revision" and len(review["findings"]) == 1
    quote = review["findings"][0]["evidence"]
    if field == "image":
        assert json.loads(quote) == _ref(image)
    elif field == "duration_seconds":
        assert quote == "4.0"
    else:
        assert quote == draft["shots"][0][field]
    with pytest.raises(ToolError, match="尚未通过校验"):
        app.comic_story_operation(cid, "confirm", _request(app, pid, cid, "blocked"))


@pytest.mark.parametrize("invalid", [
    "blank", "invented", "wrong_field", "duration_substring", "wrong_image_version",
    "string_image_version", "extra_image_field", "unknown_shot",
])
def test_review_never_accepts_evidence_from_another_value_or_scope(story_app, monkeypatch, invalid):
    app = story_app
    pid, cid, image = _setup(app)
    _save(app, pid, cid, _draft(image))
    finding = {"severity": "error", "shot_id": "story-shot-1", "field": "camera",
               "message": "A public conflict.", "evidence": "invented evidence"}
    finding.update({
        "blank": {"evidence": " \n "},
        "invented": {},
        "wrong_field": {"evidence": "A small visible movement in shot 1."},
        "duration_substring": {"field": "duration_seconds", "evidence": "14"},
        "wrong_image_version": {"field": "image", "evidence": json.dumps({
            "image_id": image["image_id"], "version": image["version"] + 1})},
        "string_image_version": {"field": "image", "evidence": json.dumps({
            "image_id": image["image_id"], "version": str(image["version"])})},
        "extra_image_field": {"field": "image", "evidence": json.dumps({
            **_ref(image), "untrusted": True})},
        "unknown_shot": {"shot_id": "not-a-current-shot", "evidence": "Camera remains still."},
    }[invalid])
    calls = []

    def model(messages):
        calls.append(messages)
        return json.dumps({"status": "needs_revision", "findings": [finding]})

    monkeypatch.setattr(app, "_comic_constraint_model", model)
    with pytest.raises(ToolError, match="审核"):
        app.comic_story_operation(cid, "review", _request(app, pid, cid, "invalid-evidence"))
    assert len(calls) == 2
    assert app.get_comic_story(cid)["review"] is None
    with pytest.raises(ToolError, match="尚未通过校验"):
        app.comic_story_operation(cid, "confirm", _request(app, pid, cid, "blocked"))


def test_review_corrects_bad_public_structure_once_and_preserves_verified_errors(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    _save(app, pid, cid, _draft(image))
    good = {"severity": "error", "shot_id": "story-shot-1", "field": "camera",
            "message": "A real camera conflict.", "evidence": "Camera remains still."}
    bad = {**good, "field": "action", "evidence": "INVALID_RESPONSE_MUST_NOT_BE_ECHOED"}
    replies = iter([{"status": "needs_revision", "findings": [good, bad]},
                    {"status": "pass", "findings": []}])
    calls = []

    def model(messages):
        calls.append(messages)
        return json.dumps(next(replies))

    monkeypatch.setattr(app, "_comic_constraint_model", model)
    checked = app.comic_story_operation(cid, "review", _request(app, pid, cid, "correct-review"))
    assert len(calls) == 2
    assert "INVALID_RESPONSE_MUST_NOT_BE_ECHOED" not in json.dumps(calls[1])
    assert "verified_findings" in calls[1][-1]["content"]
    assert checked["review"]["status"] == "needs_revision"
    assert len(checked["review"]["findings"]) == 1
    with pytest.raises(ToolError, match="尚未通过校验"):
        app.comic_story_operation(cid, "confirm", _request(app, pid, cid, "blocked"))


def test_review_correction_does_not_turn_field_reference_diagnostics_into_story_findings(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    _save(app, pid, cid, _draft(image))
    calls = []

    def review(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps({"status": "needs_revision", "findings": [{
                "severity": "error", "shot_id": "story-shot-1", "field": "action",
                "code": "continuity", "message": "A real handoff issue.",
                "evidence_id": "not-the-current-action", "evidence": "",
            }]})
        content = messages[-1]["content"]
        correction = json.loads(content[content.index("{"):])
        issue = correction["issues"][0]
        assert issue["reason"] == "invalid_field_reference"
        assert issue["expected_field_reference"].startswith("shot.1.action:")
        assert "message" not in issue
        return json.dumps({"status": "pass", "findings": []})

    monkeypatch.setattr(app, "_comic_constraint_model", review)
    result = app.comic_story_operation(cid, "review", _request(app, pid, cid, "repair-reference"))
    assert len(calls) == 2
    assert result["review"]["status"] == "pass"
    assert result["review"]["findings"] == []


@pytest.mark.parametrize("malformed", ["timing_type", "private_extra", "invalid_status"])
def test_review_keeps_valid_errors_when_another_part_has_a_schema_error(
    story_app, monkeypatch, malformed,
):
    app = story_app
    pid, cid, image = _setup(app)
    draft = _draft(image)
    _save(app, pid, cid, draft)
    good = {"severity": "error", "shot_id": "story-shot-1", "field": "camera",
            "evidence": draft["shots"][0]["camera"], "message": "A real direction conflict.",
            "suggestion": "Choose one direction."}
    bad = {"severity": "error", "shot_id": "story-shot-1", "field": "duration_seconds",
           "code": "duration_conflict", "evidence": "4.0", "message": "Allow enough time.",
           "timing_plan": _timing_plan(draft["shots"][0])}
    first = {"status": "needs_revision", "findings": [good, bad]}
    secret = "PRIVATE_INVALID_VALUE_MUST_NOT_BE_ECHOED"
    if malformed == "timing_type":
        bad["timing_plan"][0]["seconds"] = {"private": secret}
    elif malformed == "private_extra":
        bad[secret] = secret
    else:
        first["status"] = secret
        first["findings"] = [good]
    calls = []
    def review(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps(first)
        content = messages[-1]["content"]
        correction = json.loads(content[content.index("{"):])
        diagnostics = correction["issues"][0].get("schema_errors", [])
        assert diagnostics, "Correction needs field locations and error types, not a generic error"
        assert secret not in json.dumps(messages)
        assert correction["verified_findings"][0]["message"] == good["message"]
        return json.dumps({"status": "pass", "findings": []})

    monkeypatch.setattr(app, "_comic_constraint_model", review)
    checked = app.comic_story_operation(cid, "review", _request(app, pid, cid, "partial-schema"))
    assert len(calls) == 2
    assert checked["review"]["status"] == "needs_revision"
    assert [finding["field"] for finding in checked["review"]["findings"]] == ["camera"]
    assert checked["can_confirm"] is False


def test_review_repairs_a_bad_timing_number_with_a_precise_field_diagnostic():
    from types import SimpleNamespace

    from kantoku.domains.comic.stories import ComicStoryService

    service = ComicStoryService(SimpleNamespace(projects=None, runtime=None))
    draft = ComicStoryDraft.model_validate({"shots": [{"shot_id": "one",
        "image": {"image_id": "a", "version": 1}, "action": "Put the lamp away.",
        "transition": "Take and open the compass.", "duration_seconds": 4.0}]})
    timing = [{"field": "action", "evidence": draft.shots[0].action, "seconds": 3.0},
              {"field": "transition", "evidence": draft.shots[0].transition, "seconds": 3.0}]
    finding = {"severity": "error", "shot_id": "one", "field": "duration_seconds",
        "code": "duration_conflict", "evidence": "4.0", "message": "The handoff needs six seconds.",
        "suggestion": "Give the full handoff six seconds.", "timing_plan": timing}
    calls = []
    def review(messages):
        calls.append(messages)
        current = copy.deepcopy(finding)
        if len(calls) == 1:
            current["timing_plan"][0]["seconds"] = "3 seconds"
        else:
            content = messages[-1]["content"]
            correction = json.loads(content[content.index("{"):])
            assert correction["issues"][0]["schema_errors"][0]["loc"] == [
                "findings", 0, "timing_plan", 0, "seconds"]
        return json.dumps({"status": "needs_revision", "findings": [current]})

    result = service._review_output(draft, [], review, [])
    assert len(calls) == 2 and result["status"] == "needs_revision"
    assert result["findings"][0]["timing_plan"] == timing


@pytest.mark.parametrize("stage", ["first_review", "after_repair"])
@pytest.mark.parametrize("failure", ["evidence", "schema", "transport", "billing"])
def test_review_failures_retain_recoverable_candidate_without_replacing_current_story(
    story_app, monkeypatch, stage, failure,
):
    app = story_app
    pid, cid, image = _setup(app)
    original = _save(app, pid, cid, _draft(image))["story"]
    draft = _draft(image, title="A new independent sequence")
    fixed = copy.deepcopy(draft)
    fixed["shots"][0]["camera"] = "Track forward."
    visions = iter([draft, fixed])
    vision_calls, review_calls = [], []
    first_review = {"status": "needs_revision", "findings": [{
        "severity": "error", "shot_id": "story-shot-1", "field": "camera",
        "code": "camera_conflict", "message": "Choose the requested movement.",
        "evidence": "Camera remains still.", "suggestion": "Track forward."}]}

    def vision(messages):
        vision_calls.append(messages)
        return json.dumps(next(visions))

    def review(messages):
        review_calls.append(messages)
        if stage == "after_repair" and len(review_calls) == 1:
            return json.dumps(first_review)
        if failure == "transport":
            raise ToolError("Offline transport result unknown")
        if failure == "billing":
            raise LLMError("模型服务账户余额不足")
        if failure == "schema":
            return '{"private_reasoning":"DO_NOT_ECHO_INVALID_RESPONSE"}'
        output = copy.deepcopy(first_review)
        output["findings"][0]["evidence"] = "Not present in any current field."
        return json.dumps(output)

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    request = {**_request(app, pid, cid, "review-failed"),
               "images": [_ref(image)], "instruction": "Track forward."}
    with pytest.raises(LLMError if failure == "billing" else ToolError,
                       match="余额不足" if failure == "billing" else "候选已保留"):
        app.comic_story_operation(cid, "generate", request)
    assert len(vision_calls) == (2 if stage == "after_repair" else 1)
    assert len(review_calls) == (1 if failure in {"transport", "billing"} else 2) + (
        stage == "after_repair")
    assert "DO_NOT_ECHO_INVALID_RESPONSE" not in json.dumps(review_calls)
    assert app.get_comic_story(cid)["story"] == original
    runs = [run for run in app.runtime_store.list_runs(domain="comic", conversation_id=cid)
            if run.state.get("story_candidate")]
    assert len(runs) == 1
    candidate = runs[0].state["story_candidate"]
    assert candidate["draft"]["title"] == draft["title"]
    assert candidate["draft"]["shots"] == draft["shots"]
    assert ("review" in candidate) == (stage == "after_repair")
    if stage == "after_repair":
        assert candidate["review"]["status"] == "needs_revision"
        assert candidate["review"]["findings"][0]["evidence"] == "Camera remains still."
    monkeypatch.setattr(app, "_comic_image_vision_model", _forbidden)
    monkeypatch.setattr(app, "_comic_constraint_model", _forbidden)
    with pytest.raises(ToolError):
        app.comic_story_operation(cid, "generate", request)
    with pytest.raises(ToolError, match="候选.*未通过"):
        app.comic_story_operation(cid, "adopt-candidate", {
            **_request(app, pid, cid, "recover"), "candidate_run_id": runs[0].id})
    assert app.get_comic_story(cid)["story"] == original
    # 模型失败不会污染用户当前稿件，当前稿仍可独立校验与确认。
    assert _confirm(app, pid, cid, monkeypatch)["confirmation"]["confirmed"]


def test_repair_with_no_effective_change_keeps_real_review_without_calling_again(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    skeleton = _draft(image)
    generated = copy.deepcopy(skeleton)
    vision_calls, review_calls = [], []

    def vision(messages):
        vision_calls.append(messages)
        return json.dumps(generated)

    def review(messages):
        review_calls.append(messages)
        assert len(review_calls) == 1, "An unchanged draft must not be reviewed twice"
        return json.dumps({"status": "needs_revision", "findings": [{
            "severity": "error", "shot_id": "story-shot-1", "field": "camera",
            "message": "User camera conflicts with requested tracking.",
            "evidence": "Camera remains still.", "suggestion": "Track forward."}]})

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    result = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "no-op-repair"), "images": [_ref(image)],
        "draft": skeleton, "instruction": "Track forward."})
    assert result["generation_status"] == "needs_revision"
    assert len(vision_calls) == 3 and len(review_calls) == 1
    assert json.loads(vision_calls[-1][1]["content"][0]["text"])["repair_strategy"] == "reconcile"


def test_ai_corrects_nonempty_problem_fields_and_preserves_valid_user_content(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, first = _setup(app)
    second = _upload(app, pid, cid, content=_png(7), filename="another-source.png")
    _select(app, cid, second)
    skeleton = _draft(first, second, title="User's independent sequence")
    skeleton["summary"] = "An unfinished, inconsistent summary."
    skeleton["shots"][0].update(action="Sit and stand at the same time.",
                                camera="Move left and right at the same time.",
                                transition="Continue the incompatible movement.")
    skeleton["shots"][1]["action"] = "Raise a hand and deliver the requested spoken line."
    fixed = copy.deepcopy(skeleton)
    fixed["summary"] = "A coherent sequence with the requested speech."
    fixed["shots"][0].update(action="Stand up, then remain standing.",
                             camera="Track slowly to the left.", transition="Cut to the next shot.")
    fixed["shots"][1]["duration_seconds"] = 8.0
    # 完整模型响应中的顺手改写不应落盘，问题字段则必须改掉已有原文。
    fixed["title"] = "Unrequested model title"
    fixed["shots"][1]["camera"] = "Track slowly forward while the spoken line completes."
    fixed["shots"].reverse()
    changed = [(None, "summary"), ("story-shot-1", "action"), ("story-shot-1", "camera"),
               ("story-shot-1", "transition"), ("story-shot-2", "duration_seconds")]
    findings = []
    for shot_id, field in changed:
        source = next(shot for shot in skeleton["shots"] if shot["shot_id"] == shot_id) \
            if shot_id else skeleton
        findings.append({"severity": "error", "shot_id": shot_id, "field": field,
                         "message": "Resolve the inconsistent field.",
                         "evidence": str(source[field]), "suggestion": "Use a consistent value."})
    visions = iter([skeleton, fixed])
    reviews = iter([{"status": "needs_revision", "findings": findings},
                    {"status": "pass", "findings": []}])
    monkeypatch.setattr(app, "_comic_image_vision_model", lambda _: json.dumps(next(visions)))
    monkeypatch.setattr(app, "_comic_constraint_model", lambda _: json.dumps(next(reviews)))
    result = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "correct-filled"), "images": [_ref(first), _ref(second)],
        "draft": skeleton, "instruction": "Preserve the dialogue; correct inconsistent fields."})
    story = result["story"]
    assert result["review"]["status"] == "pass"
    assert story["title"] == skeleton["title"]
    assert story["summary"] == fixed["summary"]
    assert [shot["shot_id"] for shot in story["shots"]] == ["story-shot-1", "story-shot-2"]
    assert [shot["image"] for shot in story["shots"]] == [_ref(first), _ref(second)]
    assert story["shots"][0]["action"] == "Stand up, then remain standing."
    assert story["shots"][0]["camera"] == "Track slowly to the left."
    assert story["shots"][0]["transition"] == "Cut to the next shot."
    assert story["shots"][1]["duration_seconds"] == 8.0
    assert story["shots"][1]["camera"] == "Track slowly forward while the spoken line completes."
    assert story["shots"][1]["action"] == skeleton["shots"][1]["action"]
    monkeypatch.setattr(app, "_comic_constraint_model", _forbidden)
    monkeypatch.setattr(app, "_comic_image_vision_model", _forbidden)
    checked = app.comic_story_operation(cid, "review", _request(app, pid, cid, "check-corrected"))
    assert checked["review_reused"] and checked["review"]["validation_requested"]
    confirmed = app.comic_story_operation(
        cid, "confirm", _request(app, pid, cid, "confirm-corrected"))
    assert confirmed["confirmation"]["confirmed"]
    exported = app.comic_story_operation(
        cid, "exports", _request(app, pid, cid, "export-corrected"))
    assert exported["exports"][0]["story_version"] == story["version"]


@pytest.mark.parametrize("passes", [True, False])
def test_ai_applies_followup_corrections_with_a_bounded_final_reconciliation(
    story_app, monkeypatch, passes,
):
    app = story_app
    pid, cid, image = _setup(app)
    skeleton = _draft(image)
    first = copy.deepcopy(skeleton)
    first["shots"][0]["camera"] = "Track forward."
    second = copy.deepcopy(first)
    second["shots"][0]["duration_seconds"] = 8.0
    calls = {"vision": 0, "review": 0}
    visions = iter([skeleton, first, second, second])

    def vision(_messages):
        calls["vision"] += 1
        return json.dumps(next(visions))

    def review(messages):
        calls["review"] += 1
        draft = json.loads(messages[1]["content"])["draft"]
        if draft["shots"][0]["camera"] != "Track forward.":
            field = "camera"
        elif draft["shots"][0]["duration_seconds"] != 8.0 or not passes:
            field = "duration_seconds"
        else:
            return json.dumps({"status": "pass", "findings": []})
        return json.dumps({"status": "needs_revision", "findings": [{
            "severity": "error", "shot_id": "story-shot-1", "field": field,
            "message": "Correct this remaining conflict.",
            "evidence": str(draft["shots"][0][field]), "suggestion": "Adjust this field."}]})

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    request = {**_request(app, pid, cid, "two-corrections"), "images": [_ref(image)],
               "draft": skeleton, "instruction": "Track forward with enough time."}
    if passes:
        result = app.comic_story_operation(cid, "generate", request)
        assert result["review"]["status"] == "pass"
        assert result["story"]["shots"][0]["duration_seconds"] == 8.0
        assert result["story"]["shots"][0]["camera"] == "Track forward."
    else:
        result = app.comic_story_operation(cid, "generate", request)
        assert result["generation_status"] == "needs_revision"
        assert app.get_comic_story(cid)["story"] is None
    assert calls == {"vision": 3 if passes else 4, "review": 3}


@pytest.mark.parametrize("subject", ["A robot crosses a rain-soaked bridge",
                                   "A keeper lights a coastal lighthouse"])
def test_repaired_multishot_draft_recovers_from_stale_evidence_then_validates_and_exports(
    story_app, monkeypatch, subject,
):
    app = story_app
    pid, cid, first = _setup(app)
    second = _upload(app, pid, cid, content=_webp(), mime="image/webp", filename="second.webp")
    _select(app, cid, second)
    draft = _draft(first, second, title=subject)
    draft["summary"] = subject + "; two related moments."
    fixed = copy.deepcopy(draft)
    fixed["shots"][1]["camera"] = "Track  slowly\nforward."
    visions = iter([draft, fixed])
    first_review = {"status": "needs_revision", "findings": [{
        "severity": "error", "shot_id": "story-shot-2", "field": "camera",
        "message": "Movement must follow the request.", "evidence": "Camera remains still.",
        "suggestion": "Track forward."}]}
    # 复审误引修复前的旧字段；一次契约纠正应读取当前稿，而不是丢弃整个故事。
    replies = iter([first_review, first_review, {"status": "pass", "findings": []}])
    calls = []

    def review(messages):
        calls.append(messages)
        return json.dumps(next(replies))

    monkeypatch.setattr(app, "_comic_image_vision_model", lambda _: json.dumps(next(visions)))
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    generated = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "complete-pipeline"), "images": [_ref(first), _ref(second)],
        "instruction": "Track forward in the second shot."})
    assert len(calls) == 3
    assert generated["story"]["title"] == subject
    assert generated["story"]["shots"][1]["camera"] == fixed["shots"][1]["camera"]
    assert generated["review"]["status"] == "pass"
    assert generated["review"]["validation_requested"] is False
    monkeypatch.setattr(app, "_comic_image_vision_model", _forbidden)
    monkeypatch.setattr(app, "_comic_constraint_model", _forbidden)
    checked = app.comic_story_operation(cid, "review", _request(app, pid, cid, "validate"))
    assert checked["review_reused"] and checked["review"]["validation_requested"]
    confirmed = app.comic_story_operation(cid, "confirm", _request(app, pid, cid, "confirm"))
    assert confirmed["confirmation"]["confirmed"]
    exported = app.comic_story_operation(cid, "exports", _request(app, pid, cid, "export"))
    artifact = app.runtime_store.get_artifact(exported["exports"][0]["artifact_id"])
    with ZipFile(artifact.location) as bundle:
        contents = [bundle.read(name) for name in bundle.namelist() if name.startswith("images/")]
    assert sorted(contents) == sorted([_png(), _webp()])


@pytest.mark.parametrize("old_contract", [
    None, "comic-story-1", "comic-story-2", "comic-story-3", "comic-story-4", "comic-story-5",
    "comic-story-6", "comic-story-7", "comic-story-8", "comic-story-9", "comic-story-10",
    "comic-story-11", "comic-story-12", "comic-story-13",
])
def test_restoring_an_old_candidate_rechecks_instead_of_relabeling_its_old_review(
    story_app, monkeypatch, old_contract,
):
    app = story_app
    pid, cid, image = _setup(app)
    draft = _draft(image)
    old_review = {"status": "needs_revision", "findings": [{
        "severity": "error", "shot_id": "story-shot-1", "field": "camera",
        "message": "An old static-image policy rejected this camera.",
        "evidence": draft["shots"][0]["camera"], "suggestion": "Reconsider under current policy."}]}
    monkeypatch.setattr(app, "_comic_image_vision_model", lambda _: json.dumps(draft))
    monkeypatch.setattr(app, "_comic_constraint_model", lambda _: json.dumps(old_review))
    pending = app.comic_story_operation(cid, "generate", {
            **_request(app, pid, cid, "legacy-candidate"), "images": [_ref(image)],
            "instruction": "A coherent sequence."})
    run = next(run for run in app.runtime_store.list_runs(domain="comic", conversation_id=cid)
               if run.state.get("story_candidate"))
    candidate = copy.deepcopy(run.state["story_candidate"])
    if old_contract is None:
        candidate.pop("review_contract")
    else:
        candidate["review_contract"] = old_contract
    app.runtime_store.update_run(run.id, status=run.status,
        state={**run.state, "story_candidate": candidate}, current_node=run.current_node,
        error=run.error)
    with pytest.raises(ToolError, match="候选.*未通过"):
        app.comic_story_operation(cid, "adopt-candidate", {
            **_request(app, pid, cid, "restore-legacy"), "candidate_run_id": run.id})
    calls = []
    def current_review(messages):
        calls.append(messages)
        return json.dumps({"status": "pass", "findings": []})
    monkeypatch.setattr(app, "_comic_constraint_model", current_review)
    monkeypatch.setattr(app, "_comic_image_vision_model", _forbidden)
    checked = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "recheck-legacy"), "candidate_run_id": run.id,
        "images": [_ref(image)], "draft": pending["candidate_draft"],
        "instruction": "A coherent sequence."})
    assert len(calls) == 1
    assert checked["review"]["status"] == "pass"
    assert pending["generation_status"] == "needs_revision"
    assert checked["review"]["contract"] == "comic-story-14"
    confirmed = app.comic_story_operation(cid, "confirm", _request(app, pid, cid, "new-confirm"))
    assert confirmed["confirmation"]["confirmed"]


def test_initial_generation_keeps_coordinated_execution_fields_from_the_model(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    skeleton = _draft(image, title="Keep the user's sequence title")
    skeleton["summary"] = "Raise the signal lamp, then call the waiting ship."
    skeleton["instruction"] = ""
    skeleton["shots"][0].update(
        action="Raise the lamp and call the ship.", camera="Start close, then reveal the coast.",
        duration_seconds=4.0, transition="Move to the opposite view.")
    generated = copy.deepcopy(skeleton)
    generated.update(title="An unsolicited model title", summary="An unsolicited summary")
    generated["shots"][0].update(
        action="Raise the lamp over two seconds, then call the waiting ship.",
        camera="Pull back from the lamp to reveal the keeper and the coast.",
        duration_seconds=8.0, transition="Hold the wide view to end the sequence.")
    vision_calls, review_calls = [], []

    def vision(messages):
        vision_calls.append(messages)
        return json.dumps(generated)

    def review(messages):
        review_calls.append(messages)
        draft = json.loads(messages[1]["content"])["draft"]
        assert draft["shots"] == generated["shots"], "Merging must not restore inconsistent fields"
        return json.dumps({"status": "pass", "findings": []})

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    result = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "coordinated-initial"), "images": [_ref(image)],
        "draft": skeleton, "instruction": ""})
    assert result["story"]["title"] == skeleton["title"]
    assert result["story"]["summary"] == skeleton["summary"]
    assert result["story"]["shots"] == generated["shots"]
    assert result["review"]["status"] == "pass"
    assert len(vision_calls) == len(review_calls) == 1


def test_action_finding_can_repair_timing_and_camera_without_changing_another_shot(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, first = _setup(app)
    second = _upload(app, pid, cid, content=_png(51), filename="next-moment.png")
    _select(app, cid, second)
    skeleton = _draft(first, second)
    skeleton["shots"][0]["action"] = "Lift the beacon, speak the required line, and hold it aloft."
    fixed = copy.deepcopy(skeleton)
    fixed["shots"][0].update(
        action="Lift the beacon, say the required line, then hold it aloft for two seconds.",
        camera="Pull back slowly over the complete action.", duration_seconds=9.0,
        transition="Hold before a clean cut to the next moment.")
    fixed["shots"][1]["action"] = "UNRELATED_SHOT_REWRITE_MUST_NOT_BE_APPLIED"
    visions = iter([skeleton, fixed])
    review_calls = []

    def review(messages):
        review_calls.append(messages)
        draft = json.loads(messages[1]["content"])["draft"]
        if len(review_calls) == 1:
            return json.dumps({"status": "needs_revision", "findings": [{
                "severity": "error", "shot_id": "story-shot-1", "field": "action",
                "code": "duration_conflict",
                "timing_plan": _timing_plan(draft["shots"][0]),
                "message": "The action needs time to complete the line.",
                "evidence": draft["shots"][0]["action"],
                "suggestion": "Coordinate the action, camera movement and timing."}]})
        assert draft["shots"][0] == fixed["shots"][0]
        assert draft["shots"][1] == skeleton["shots"][1]
        return json.dumps({"status": "pass", "findings": []})

    monkeypatch.setattr(app, "_comic_image_vision_model", lambda _: json.dumps(next(visions)))
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    result = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "action-timing-group"),
        "images": [_ref(first), _ref(second)], "draft": skeleton,
        "instruction": "Keep the required line and give each action enough time."})
    assert result["review"]["status"] == "pass"
    assert result["story"]["shots"][0]["duration_seconds"] == 9.0
    assert result["story"]["shots"][1] == skeleton["shots"][1]
    assert len(review_calls) == 2


def test_same_image_repeated_shots_match_stable_ids_when_model_order_is_reversed(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    skeleton = _draft(image, image)
    for shot in skeleton["shots"]:
        shot.update(action="", camera="", duration_seconds=None, transition="")
    generated = copy.deepcopy(skeleton)
    generated["shots"][0].update(
        action="First the keeper finds the signal lamp.", camera="Push in toward the lamp.",
        duration_seconds=3.5, transition="Cut to the keeper lifting it.")
    generated["shots"][1].update(
        action="Then the keeper raises the lamp and calls the ship.", camera="Pull back slowly.",
        duration_seconds=7.0, transition="Hold the final view.")
    expected = copy.deepcopy(generated["shots"])
    generated["shots"].reverse()
    monkeypatch.setattr(app, "_comic_image_vision_model", lambda _: json.dumps(generated))
    monkeypatch.setattr(app, "_comic_constraint_model",
                        lambda _: json.dumps({"status": "pass", "findings": []}))
    result = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "same-image-stable-shots"), "images": [_ref(image)],
        "draft": skeleton, "instruction": "Find the lamp first, then lift it and call the ship."})
    assert result["story"]["shots"] == expected
    assert len(result["story"]["fixed_images"]) == 1


def test_original_user_intent_reaches_generation_repair_and_internal_review_only(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    skeleton = _draft(image)
    skeleton["instruction"] = ""
    skeleton["shots"][0]["action"] = "Lift the beacon and say ORIGINAL_SIGNAL_LINE_74."
    fixed = copy.deepcopy(skeleton)
    fixed["shots"][0].update(
        action="Lift the beacon, then clearly say ORIGINAL_SIGNAL_LINE_74.",
        duration_seconds=8.0)
    visions = iter([skeleton, fixed])
    vision_inputs, review_inputs = [], []

    def vision(messages):
        payload = json.loads(messages[-1]["content"][0]["text"])
        vision_inputs.append(payload)
        assert payload["user_intent"] == skeleton
        return json.dumps(next(visions))

    def review(messages):
        payload = json.loads(messages[1]["content"])
        review_inputs.append(payload)
        if len(review_inputs) <= 2:
            assert payload["user_intent"] == skeleton
        else:
            assert payload.get("user_intent") is None
            assert "ORIGINAL_SIGNAL_LINE_74" not in json.dumps(messages)
            assert "NEW_MANUAL_SIGNAL_LINE_85" in payload["draft"]["shots"][0]["action"]
        if len(review_inputs) == 1:
            return json.dumps({"status": "needs_revision", "findings": [{
                "severity": "error", "shot_id": "story-shot-1", "field": "action",
                "code": "duration_conflict", "message": "The requested line needs more time.",
                "timing_plan": _timing_plan(payload["draft"]["shots"][0]),
                "evidence": skeleton["shots"][0]["action"],
                "suggestion": "Preserve the line and extend the action timing."}]})
        return json.dumps({"status": "pass", "findings": []})

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    generated = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "original-intent"), "images": [_ref(image)],
        "draft": skeleton, "instruction": ""})
    assert generated["review"]["status"] == "pass"
    assert len(vision_inputs) == len(review_inputs) == 2
    assert review_inputs[1]["draft"]["shots"][0]["action"] != skeleton["shots"][0]["action"]
    manually_edited = _story_draft(generated["story"])
    manually_edited["shots"][0]["action"] = "Lower the beacon and say NEW_MANUAL_SIGNAL_LINE_85."
    _save(app, pid, cid, manually_edited, "replace-user-intent")
    result = app.comic_story_operation(cid, "review", _request(app, pid, cid, "manual-intent"))
    assert result["review"]["status"] == "pass"
    assert len(review_inputs) == 3


def test_model_inputs_separate_static_negative_prompt_from_explicit_persistent_requirements(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    negative = "Do not show the face or rotate toward the viewer in this still image."
    persistent = "The silver lighthouse badge remains visible throughout the entire video."
    source_constraints = [
        {"constraint_id": "source-starting-view", "scope": "shot",
         "requirement": "The keeper starts with their back toward the camera.",
         "source_quote": "Start with the keeper's back toward the camera.",
         "origin": "user_request", "trust": "source_verified", "status": "active"},
        {"constraint_id": "source-continuous-badge", "scope": "identity",
         "requirement": persistent, "source_quote": persistent,
         "origin": "user_request", "trust": "source_verified", "status": "active"},
    ]
    original_sources = app.comic_stories._source_contexts

    def sources(images, *, previous=None):
        return [{**source, "constraints": copy.deepcopy(source_constraints),
                 "negative_prompt": negative}
                for source in original_sources(images, previous=previous)]

    monkeypatch.setattr(app.comic_stories, "_source_contexts", sources)
    skeleton = _draft(image)
    skeleton["shots"][0].update(
        action="Show the badge, then lift the signal lamp.",
        camera="Begin behind the keeper, then rotate to a front three-quarter view.")
    fixed = copy.deepcopy(skeleton)
    fixed["shots"][0]["duration_seconds"] = 8.0
    visions = iter([skeleton, fixed])
    model_inputs = []

    def check_sources(payload):
        model_inputs.append(payload)
        assert len(payload["image_sources"]) == 1
        source = payload["image_sources"][0]
        assert source["reference_role"] == "initial_frame"
        assert source["constraints"] == source_constraints
        assert "negative_prompt" not in source
        assert negative not in json.dumps(payload)
        assert persistent in json.dumps(payload)

    def vision(messages):
        check_sources(json.loads(messages[-1]["content"][0]["text"]))
        return json.dumps(next(visions))

    def review(messages):
        payload = json.loads(messages[1]["content"])
        check_sources(payload)
        if payload["draft"]["shots"][0]["duration_seconds"] == 4.0:
            return json.dumps({"status": "needs_revision", "findings": [{
                "severity": "error", "shot_id": "story-shot-1", "field": "duration_seconds",
                "code": "duration_conflict", "message": "Give the lamp lift and camera orbit time.",
                "timing_plan": _timing_plan(payload["draft"]["shots"][0]),
                "evidence": "4", "suggestion": "Extend the duration while retaining the badge."}]})
        return json.dumps({"status": "pass", "findings": []})

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    result = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "source-roles"), "images": [_ref(image)],
        "draft": skeleton, "instruction": "Start behind the keeper, then turn to the front."})
    assert result["review"]["status"] == "pass"
    assert len(model_inputs) == 4, "Generation, repair and both reviews use the same source roles"
    assert result["story"]["source_contexts"][0]["negative_prompt"] == negative
    assert result["story"]["source_contexts"][0]["constraints"] == source_constraints


@pytest.mark.parametrize("code", ["continuity", "transition_conflict"])
@pytest.mark.parametrize("severity", ["error", "warning"])
def test_only_real_transition_errors_authorize_repairing_the_next_shot(
    story_app, monkeypatch, code, severity,
):
    app = story_app
    pid, cid, image = _setup(app)
    skeleton = _draft(image, image, image)
    skeleton["shots"][0]["transition"] = "Cut before the hand reaches the signal lever."
    fixed = copy.deepcopy(skeleton)
    for index, shot in enumerate(fixed["shots"], 1):
        shot.update(action=f"Complete coordinated step {index}.",
                    camera=f"Follow the movement in step {index}.",
                    duration_seconds=float(index + 6), transition=f"Close step {index} smoothly.")
    visions = iter([skeleton, fixed])
    vision_calls, review_calls = [], []
    editable = {"story-shot-1", "story-shot-2"}
    if severity == "warning":
        editable.add("story-shot-3")  # 本轮 AI 整理同时处理优化建议及真实错误。

    def vision(messages):
        payload = json.loads(messages[-1]["content"][0]["text"])
        vision_calls.append(payload)
        if len(vision_calls) == 2:
            scope = payload["repair_scope"]
            assert set(scope["shots"]) == editable
            assert all(set(fields) == {"action", "camera", "duration_seconds", "transition"}
                       for fields in scope["shots"].values())
            assert scope["story"] == []
        return json.dumps(next(visions))

    def review(messages):
        payload = json.loads(messages[1]["content"])
        review_calls.append(payload)
        if len(review_calls) == 1:
            findings = [{
                "severity": severity, "shot_id": "story-shot-1", "field": "transition",
                "code": code, "message": "The lever movement must continue into the next shot.",
                "evidence": skeleton["shots"][0]["transition"],
                "suggestion": "Coordinate this ending with the next shot's starting action."}]
            if severity == "warning":
                findings.append({
                    "severity": "error", "shot_id": "story-shot-3", "field": "action",
                    "code": "duration_conflict", "message": "The final action needs more time.",
                    "timing_plan": _timing_plan(payload["draft"]["shots"][2]),
                    "evidence": skeleton["shots"][2]["action"],
                    "suggestion": "Coordinate the final action and its duration."})
            return json.dumps({"status": "needs_revision", "findings": findings})
        for index, shot in enumerate(payload["draft"]["shots"]):
            expected = fixed if shot["shot_id"] in editable else skeleton
            assert shot == expected["shots"][index]
        return json.dumps({"status": "pass", "findings": []})

    monkeypatch.setattr(app, "_comic_image_vision_model", vision)
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    result = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "boundary-authorization"), "images": [_ref(image)],
        "draft": skeleton, "instruction": "Keep the three moments in this order."})
    assert result["review"]["status"] == "pass"
    assert len(vision_calls) == len(review_calls) == 2
    assert [shot["image"] for shot in result["story"]["shots"]] == [_ref(image)] * 3


@pytest.mark.parametrize("swap_both", [False, True])
def test_initial_model_cannot_reuse_a_user_shot_id_for_another_images_execution_fields(
    story_app, monkeypatch, swap_both,
):
    app = story_app
    pid, cid, first = _setup(app)
    second = _upload(app, pid, cid, content=_png(32), filename="different-frame.png")
    _select(app, cid, second)
    skeleton = _draft(first, second)
    generated = copy.deepcopy(skeleton)
    generated["shots"][0].update(
        image=_ref(second), action="FOREIGN_IMAGE_FIELDS_MUST_NOT_BE_BORROWED",
        camera="A camera instruction for a different source.", duration_seconds=12.0,
        transition="A transition describing a different source.")
    if swap_both:
        generated["shots"][1].update(
            image=_ref(first), action="FOREIGN_IMAGE_FIELDS_MUST_NOT_BE_BORROWED",
            camera="Another mismatched camera.", duration_seconds=11.0,
            transition="Another mismatched ending.")
    else:
        generated["shots"][1]["camera"] = "A valid adjustment using the second shot's own image."
    seen = []

    def review(messages):
        draft = json.loads(messages[1]["content"])["draft"]
        seen.append(draft)
        assert draft["shots"][0] == skeleton["shots"][0]
        assert draft["shots"][1] == (skeleton if swap_both else generated)["shots"][1]
        return json.dumps({"status": "pass", "findings": []})

    monkeypatch.setattr(app, "_comic_image_vision_model", lambda _: json.dumps(generated))
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    result = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "reject-cross-image-field-borrowing"),
        "images": [_ref(first), _ref(second)], "draft": skeleton,
        "instruction": "Keep each shot bound to its original image."})
    assert result["review"]["status"] == "pass"
    assert len(seen) == 1
    assert "FOREIGN_IMAGE_FIELDS_MUST_NOT_BE_BORROWED" not in json.dumps(result["story"])
    assert [shot["image"] for shot in result["story"]["shots"]] == [_ref(first), _ref(second)]


def test_missing_shots_explicitly_authorizes_creation_in_the_public_repair_scope():
    from kantoku.domains.comic.stories import ComicStoryService

    draft = ComicStoryDraft(title="Signal from the coast", summary="A keeper signals a ship.")
    findings = ComicStoryService._structural_findings(draft)
    assert len(findings) == 1 and findings[0]["code"] == "missing_shots"
    scope = ComicStoryService._repair_scope(draft, findings)
    assert scope["create_shots"] is True
    assert scope["shots"] == {} and scope["story"] == []
    advisory = [{**findings[0], "severity": "warning"}]
    assert ComicStoryService._repair_scope(draft, advisory)["create_shots"] is False


def test_repeated_image_does_not_lend_one_unmatched_model_shot_to_two_user_events(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    skeleton = _draft(image, image)
    skeleton["shots"][0]["action"] = "FIRST_USER_EVENT: find the signal lamp."
    skeleton["shots"][1]["action"] = "SECOND_USER_EVENT: lift the lamp and call the ship."
    skeleton["shots"][1]["duration_seconds"] = 7.0
    generated = _draft(image)
    generated["shots"][0].update(
        shot_id="model-lost-the-original-identities",
        action="AMBIGUOUS_MODEL_EVENT_MUST_NOT_REPLACE_EITHER_USER_EVENT",
        camera="An ambiguous camera for one of two repeated images.", duration_seconds=12.0,
        transition="An ending with no known corresponding user shot.")
    seen = []

    def review(messages):
        draft = json.loads(messages[1]["content"])["draft"]
        seen.append(draft)
        assert draft["shots"] == skeleton["shots"]
        return json.dumps({"status": "pass", "findings": []})

    monkeypatch.setattr(app, "_comic_image_vision_model", lambda _: json.dumps(generated))
    monkeypatch.setattr(app, "_comic_constraint_model", review)
    result = app.comic_story_operation(cid, "generate", {
        **_request(app, pid, cid, "ambiguous-duplicate-image"), "images": [_ref(image)],
        "draft": skeleton, "instruction": skeleton["instruction"]})
    assert result["review"]["status"] == "pass"
    assert len(seen) == 1
    assert result["story"]["shots"] == skeleton["shots"]
    assert "AMBIGUOUS_MODEL_EVENT" not in json.dumps(result["story"])


@pytest.mark.parametrize("lifecycle", ["trashed", "purged"])
def test_global_asset_lifecycle_preserves_story_approval_but_blocks_unavailable_exports(
    story_app, monkeypatch, lifecycle,
):
    """全局生命周期与领域整图删除不同：内容和旧交付保留，失效图不能再用于制作。"""
    app = story_app
    pid, cid, image = _setup(app)
    saved = _save(app, pid, cid, _draft(image))
    confirmed = _confirm(app, pid, cid, monkeypatch)
    delivered = app.comic_story_operation(cid, "exports", _request(app, pid, cid, "before-trash"))
    package = delivered["exports"][0]
    package_path = app.get_core_artifact_content(package["artifact_id"])
    package_bytes = package_path.read_bytes()
    artifact = app.runtime_store.get_artifact(image["artifact_id"])
    image_path = Path(artifact.location)
    metadata_before = copy.deepcopy(artifact.metadata)
    trashed = app.change_core_artifact(artifact.id, "trash", {
        "expected_lifecycle_version": artifact.lifecycle_version})
    if lifecycle == "purged":
        app.change_core_artifact(artifact.id, "purge", {
            "expected_lifecycle_version": trashed["lifecycle_version"]})
        assert not image_path.exists()
    else:
        assert image_path.is_file()
    monkeypatch.setattr(app, "_comic_constraint_model", _forbidden)
    monkeypatch.setattr(app, "_comic_image_vision_model", _forbidden)

    unavailable = app.get_comic_story(cid)
    assert unavailable["story"] == saved["story"]
    assert unavailable["confirmation"] == confirmed["confirmation"]
    assert {item["shot_id"] for item in unavailable["missing_images"]} == {"story-shot-1"}
    candidates = app.comic_story_images(cid)["images"]
    selected = next(item for item in candidates if item["image_id"] == image["image_id"])
    assert selected["available"] is False
    assert selected["selection"]["selected_version"] == 1
    with pytest.raises(ToolError):
        app.comic_story_operation(cid, "confirm", _request(app, pid, cid, "inactive-confirm"))
    with pytest.raises(ToolError):
        app.comic_story_operation(cid, "exports", _request(app, pid, cid, "inactive-export"))
    assert app.get_core_artifact_content(package["artifact_id"]).read_bytes() == package_bytes
    assert app.runtime_store.get_artifact(artifact.id).metadata == metadata_before

    current = app.runtime_store.get_artifact(artifact.id)
    if lifecycle == "trashed":
        app.change_core_artifact(artifact.id, "restore", {
            "expected_lifecycle_version": current.lifecycle_version})
        restored = app.get_comic_story(cid)
        assert restored["story"] == saved["story"]
        assert restored["confirmation"] == confirmed["confirmation"]
        assert restored["missing_images"] == []
        assert image_path.read_bytes() == _png()
        assert next(item for item in app.comic_story_images(cid)["images"]
                    if item["image_id"] == image["image_id"])["available"] is True
        exported = app.comic_story_operation(
            cid, "exports", _request(app, pid, cid, "restored-export"))
        assert any(item["story_version"] == saved["story"]["version"]
                   and item["artifact_id"] != package["artifact_id"]
                   for item in exported["exports"])
    else:
        with pytest.raises(ToolError):
            app.change_core_artifact(artifact.id, "restore", {
                "expected_lifecycle_version": current.lifecycle_version})
    assert app.get_core_artifact_content(package["artifact_id"]).read_bytes() == package_bytes


def test_family_delete_protects_parent_work_story_without_locking_its_old_references_forever(
    story_app,
):
    app = story_app
    parent = app.comic_workspace.create({"request_id": "protected-work", "title": "Parent story"})
    child = app.comic_workspace.create_task(parent["work_id"], {
        "request_id": "protected-task", "expected_version": parent["version"],
        "title": "Child image source", "requirement": "A frame owned by this child task."})
    pid, cid = child["task_id"], child["conversation_id"]
    first = _upload(app, pid, cid, filename="first-source.png")
    replacement = _upload(app, pid, cid, content=_png(93), filename="replacement-source.png")
    for image in (first, replacement):
        _select(app, cid, image)

    def write_story(image, expected_story_version, request_id):
        return app.comic_work_story(parent["work_id"], "save", {
            "request_id": request_id,
            "expected_project_version": app.comic_workspace.get(parent["work_id"])["version"],
            "expected_story_version": expected_story_version, "draft": _draft(image)})

    original = write_story(first, 0, "parent-story-v1")
    assert original["story"]["anchor_project_id"] == parent["work_id"] != pid
    before = app.comic_projects.get(pid).project.current_version
    with pytest.raises(ToolError, match="故事"):
        app.delete_comic_image(pid, first["image_id"],
                               _media_request(app, pid, cid, "reject-child-deletion"))
    assert app.comic_projects.get(pid).project.current_version == before
    assert app.runtime_store.get_artifact(first["artifact_id"]).lifecycle_state == \
        ArtifactLifecycle.ACTIVE
    assert app.comic_work_story(parent["work_id"])["story"] == original["story"]

    current = write_story(replacement, 1, "parent-story-v2")
    app.delete_comic_image(pid, first["image_id"],
                           _media_request(app, pid, cid, "delete-unfixed-history-source"))
    live = app.comic_work_story(parent["work_id"])
    assert live["story"] == current["story"] and live["missing_images"] == []
    historical = app.comic_work_story(parent["work_id"], version=1)
    assert historical["story"] == original["story"]
    assert {item["shot_id"] for item in historical["missing_images"]} == {"story-shot-1"}
    assert app.runtime_store.get_artifact(first["artifact_id"]).lifecycle_state == \
        ArtifactLifecycle.TRASHED
    assert app.runtime_store.get_artifact(replacement["artifact_id"]).lifecycle_state == \
        ArtifactLifecycle.ACTIVE
