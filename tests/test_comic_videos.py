"""Video orchestration uses isolated storage and explicit offline protocol substitutes."""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_comic_stories import (
    _confirm,
    _draft,
    _png,
    _request,
    _save,
    _select,
    _setup,
    _upload,
)
from test_comic_stories import story_app as story_app_fixture

from kantoku.capabilities.video import VideoGenerationResult, VideoService, xiaoyunque
from kantoku.capabilities.video.provider import VideoPreparationError
from kantoku.capabilities.video.xiaoyunque import XiaoyunqueVideoProvider
from kantoku.config import ToolError
from kantoku.config.runtime_paths import atomic_write_bytes
from kantoku.config.settings import VideoSettings
from kantoku.core import budget
from kantoku.domains.comic.videos import VIDEO_WORKFLOW
from kantoku.shells import web_studio

story_app = story_app_fixture


def test_xiaoyunque_accepts_visual_api_key_without_ak_sk(monkeypatch):
    monkeypatch.setenv("TEST_VISUAL_API_KEY", "test-secret-value")
    settings = VideoSettings(enabled=True, provider="xiaoyunque", pricing_confirmed=True,
        model=XiaoyunqueVideoProvider.req_key, authentication="api_key",
        api_key_env="TEST_VISUAL_API_KEY", reference_urls={"a": "https://x.volces.com/a"})
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size):
            return json.dumps({"code": 10000, "data": {"task_id": "task-1"}}).encode()

    class Opener:
        def open(self, request, timeout):
            seen.append(request)
            return Response()

    monkeypatch.setattr(xiaoyunque, "build_opener", lambda *_: Opener())
    provider = XiaoyunqueVideoProvider(settings)
    assert provider.readiness() is None
    result = provider._call("CVSync2AsyncSubmitTask", {"req_key": provider.req_key})
    assert result["data"]["task_id"] == "task-1"
    assert len(seen) == 1
    assert seen[0].get_header("Authorization") == "Bearer test-secret-value"
    assert seen[0].full_url == ("https://visual.volcengineapi.com?"
                                "Action=CVSync2AsyncSubmitTask&Version=2022-08-31")


def test_xiaoyunque_visual_api_key_failure_does_not_expose_secret(monkeypatch):
    monkeypatch.setenv("TEST_VISUAL_API_KEY", "test-secret-value")
    settings = VideoSettings(authentication="api_key", api_key_env="TEST_VISUAL_API_KEY")

    class Opener:
        def open(self, request, timeout):
            raise OSError("test-secret-value")

    monkeypatch.setattr(xiaoyunque, "build_opener", lambda *_: Opener())
    with pytest.raises(ToolError) as error:
        XiaoyunqueVideoProvider(settings)._call("CVSync2AsyncSubmitTask", {})
    assert "test-secret-value" not in str(error.value)


def test_xiaoyunque_signed_request_failure_does_not_expose_temporary_url():
    class Client:
        api_info = {"CVSync2AsyncSubmitTask": SimpleNamespace(query={})}

        def common_json_handler(self, action, payload):
            raise OSError("sensitive-temporary-url")

    provider = XiaoyunqueVideoProvider(VideoSettings(), client=Client())
    with pytest.raises(ToolError) as error:
        provider._call("CVSync2AsyncSubmitTask", {})
    assert "sensitive-temporary-url" not in str(error.value)


def test_xiaoyunque_uploads_all_frozen_images_to_private_tos_before_submit(
    monkeypatch, tmp_path,
):
    import hashlib

    import tos

    from kantoku.capabilities.video import VideoGenerationRequest

    contents = [_png(1), _png(2)]
    sources = []
    paths = {}
    for index, content in enumerate(contents):
        path = tmp_path / f"reference-{index}.png"
        atomic_write_bytes(path, content)
        artifact_id = f"image-{index}"
        paths[artifact_id] = path
        sources.append({"artifact_id": artifact_id, "sha256": hashlib.sha256(content).hexdigest()})

    class Store:
        def get_artifact(self, artifact_id):
            return SimpleNamespace(location=str(paths[artifact_id]))

    class Tos:
        def __init__(self):
            self.uploads = []

        def put_object(self, bucket, key, **kwargs):
            self.uploads.append((bucket, key, kwargs))

        def pre_signed_url(self, method, bucket, key, expires):
            assert method is tos.HttpMethodType.Http_Method_Get
            assert expires == 12 * 60 * 60
            return SimpleNamespace(signed_url=f"https://{bucket}.tos-cn-beijing.volces.com/{key}")

    cloud = Tos()
    settings = VideoSettings(enabled=True, provider="xiaoyunque", pricing_confirmed=True,
        model=XiaoyunqueVideoProvider.req_key, tos_bucket="linjiuri", tos_region="cn-beijing")
    provider = XiaoyunqueVideoProvider(settings, client=object(), store=Store(), tos_client=cloud,
        read_url=lambda url, limit: next(item[2]["content"] for item in cloud.uploads
                                        if url.endswith(item[1])))
    calls = []
    monkeypatch.setattr(provider, "_call", lambda action, payload: calls.append(payload)
                        or {"code": 10000, "data": {"task_id": "task-1"}})
    request = VideoGenerationRequest(request_id="req", run_id="run", node_id="node",
        image_artifact_id="image-0", prompt="A full story", project="project", shot_no=1,
        config={"duration_seconds": 20}, source_images=sources)
    provider.preflight(request)
    assert not cloud.uploads
    result = provider.generate(request)
    assert result.provider_job_id == "task-1"
    assert [item[2]["content"] for item in cloud.uploads] == contents
    assert all(item[2]["acl"] is tos.ACLType.ACL_Private and item[2]["forbid_overwrite"]
               for item in cloud.uploads)
    assert len(calls) == 1 and len(calls[0]["img_url_list"]) == 2
    assert calls[0]["duration"] == "～30s"


def test_reference_upload_failure_releases_video_budget_without_submitting(
    story_app, monkeypatch, tmp_path,
):
    app = story_app
    _, cid, provider, request = prepared(app, monkeypatch, tmp_path)

    def fail_before_submit(_request):
        raise VideoPreparationError("参考图上传失败")

    monkeypatch.setattr(provider, "generate", fail_before_submit)
    task = app.comic_story_operation(cid, "video", request)
    run = wait_for(app, task["run_id"], ("failed",))
    assert run.status.value == "failed"
    reservation = budget.get_reservation(request["request_id"])
    assert reservation.status == "released" and reservation.provider_job_id is None


def test_video_credentials_stay_in_process_and_never_enter_story_view(
    story_app, monkeypatch, tmp_path,
):
    app = story_app
    _, cid, _, _ = prepared(app, monkeypatch, tmp_path)
    settings = VideoSettings(enabled=True, provider="xiaoyunque", pricing_confirmed=True,
        model=XiaoyunqueVideoProvider.req_key, tos_bucket="linjiuri")
    provider = XiaoyunqueVideoProvider(settings, store=app.runtime_store)
    app.comic_videos.capability = VideoService(app.runtime_store, provider, settings)
    secret = "test-private-secret"
    result = app.configure_video_credentials({"access_key": "test-access-id", "secret_key": secret})
    assert result == {"configured": True}
    assert provider.readiness() is None
    view = app.comic_videos.view(cid)
    assert view["session_credentials_configured"] is True
    assert secret not in json.dumps(view, ensure_ascii=False)
    with pytest.raises(ToolError):
        app.configure_video_credentials({"access_key": "another-access", "secret_key": secret})


class OfflineVideoProtocol:
    provider_id = "xiaoyunque"

    def __init__(self, path):
        self.path = path
        self.submissions = []
        self.queries = []
        self.complete = False
        self.unknown = False

    def readiness(self):
        return None

    def preflight(self, request):
        assert request.source_images and request.input_sha256

    def generate(self, request):
        self.submissions.append(request)
        if self.unknown:
            raise ToolError("Offline submission result unknown")
        return VideoGenerationResult(status="pending", provider_job_id="offline-video-id")

    def query(self, request, task_id):
        self.queries.append(task_id)
        if not self.complete:
            return VideoGenerationResult(status="pending", provider_job_id=task_id)
        atomic_write_bytes(self.path, b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 12)
        return VideoGenerationResult(status="succeeded", provider_job_id=task_id,
                                     location=str(self.path))


def wait_for(app, run_id, states=("waiting", "completed", "failed")):
    for _ in range(300):
        future = app._media_futures.get(run_id)
        if future:
            future.result(timeout=10)
        run = app.runtime_store.get_run(run_id)
        if run.status.value in states:
            return run
        time.sleep(.01)
    pytest.fail("Offline video worker did not finish")


def prepared(app, monkeypatch, tmp_path):
    pid, cid, image = _setup(app)
    _save(app, pid, cid, _draft(image))
    _confirm(app, pid, cid, monkeypatch)
    provider = OfflineVideoProtocol(tmp_path / "offline.mp4")
    settings = VideoSettings(enabled=True, provider="xiaoyunque", estimated_fen=1, max_fen=10)
    app.comic_videos.capability = VideoService(app.runtime_store, provider, settings)
    monkeypatch.setattr(budget, "get_settings", lambda: web_studio.get_settings())
    story = app.get_comic_story(cid)["story"]
    request = {**_request(app, pid, cid, str(uuid4())), "prompt": "Track the movement.",
               "scope": "shot", "shot_id": "story-shot-1",
               "content_sha256": story["content_sha256"]}
    return pid, cid, provider, request


def test_confirmed_story_snapshot_submit_query_and_original_story_are_independent(
    story_app, monkeypatch, tmp_path,
):
    app = story_app
    pid, cid, provider, request = prepared(app, monkeypatch, tmp_path)
    before = app.get_comic_story(cid)
    result = app.comic_story_operation(cid, "video", request)
    run = wait_for(app, result["run_id"])
    assert run.status.value == "waiting" and run.workflow == VIDEO_WORKFLOW
    assert app.get_comic_story(cid) == before
    view = app.comic_videos.view(cid)
    assert view["tasks"][0]["phase"] == "generating" and len(provider.submissions) == 1
    assert view["tasks"][0]["snapshot"]["shots"] == before["manifest"]["shots"]
    assert app.comic_story_operation(cid, "video", request)["run_id"] == run.id
    changed = _draft(before["story"]["fixed_images"][0], title="A later user edit")
    _save(app, pid, cid, changed, "later-story")
    provider.complete = True
    app.comic_story_operation(cid, "video-query", {
        "anchor_project_id": pid, "run_id": run.id})
    final = wait_for(app, run.id, ("completed", "failed"))
    assert final.status.value == "completed"
    assert len(provider.submissions) == 1 and provider.queries == ["offline-video-id"]
    assert final.state["snapshot"]["version"] == before["story"]["version"]
    assert app.get_comic_story(cid)["story"]["title"] == changed["title"]
    task = app.comic_videos.view(cid)["tasks"][0]
    assert task["phase"] == "succeeded" and task["artifact_id"]
    assert task["actual_fen"] is None  # 未知实际费用没有伪装成零。
    assert app.get_core_artifact_content(task["artifact_id"]).is_file()


@pytest.mark.parametrize("invalid", ["unconfirmed", "wrong_hash", "wrong_shot", "wrong_owner"])
def test_video_rejects_stale_or_unowned_input_before_submission(
    story_app, monkeypatch, tmp_path, invalid,
):
    app = story_app
    pid, cid, provider, request = prepared(app, monkeypatch, tmp_path)
    if invalid == "unconfirmed":
        draft = _draft(app.get_comic_story(cid)["story"]["fixed_images"][0])
        draft["summary"] = "New manual revision"
        _save(app, pid, cid, draft, "unconfirmed-new-revision")
        request.update(_request(app, pid, cid, request["request_id"]))
        request["content_sha256"] = app.get_comic_story(cid)["story"]["content_sha256"]
    elif invalid == "wrong_hash":
        request["content_sha256"] = "0" * 64
    elif invalid == "wrong_shot":
        request["shot_id"] = "not-in-the-story"
    else:
        request["conversation_id"] = "another-conversation"
    with pytest.raises(ToolError):
        app.comic_story_operation(cid, "video", request)
    assert not provider.submissions
    assert not [run for run in app.runtime_store.list_runs(conversation_id=cid)
                if run.workflow == VIDEO_WORKFLOW]


def test_video_duplicate_clicks_share_one_run_and_one_provider_submission(
    story_app, monkeypatch, tmp_path,
):
    app = story_app
    _, cid, provider, request = prepared(app, monkeypatch, tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda _: app.comic_story_operation(cid, "video", request), [1, 2]))
    assert results[0]["run_id"] == results[1]["run_id"]
    wait_for(app, results[0]["run_id"])
    assert len(provider.submissions) == 1
    with pytest.raises(ToolError, match="已有视频任务"):
        app.comic_story_operation(cid, "video", {**request, "request_id": str(uuid4())})


def test_full_story_reference_images_follow_first_shot_use_not_storage_identity(
    story_app, monkeypatch, tmp_path,
):
    app = story_app
    pid, cid, provider, request = prepared(app, monkeypatch, tmp_path)
    first = app.get_comic_story(cid)["story"]["fixed_images"][0]
    second = _upload(app, pid, cid, content=_png(52))
    _select(app, cid, second)
    ordered = sorted([first, second], key=lambda image: image["image_id"], reverse=True)
    draft = _draft(*ordered)
    draft["shots"].append({**draft["shots"][0], "shot_id": "repeat-first-image"})
    _save(app, pid, cid, draft, "reverse-order")
    _confirm(app, pid, cid, monkeypatch, key="confirm-reverse")
    story = app.get_comic_story(cid)["story"]
    request.update(_request(app, pid, cid, "full-story-video"), scope="story", shot_id=None,
                   content_sha256=story["content_sha256"])
    result = app.comic_story_operation(cid, "video", request)
    wait_for(app, result["run_id"])
    assert [image["image_id"] for image in provider.submissions[0].source_images] == [
        image["image_id"] for image in ordered]
    assert len(provider.submissions[0].source_images) == 2
    submitted = provider.submissions[0]
    assert story["summary"] in submitted.prompt
    assert story["instruction"] in submitted.prompt
    assert request["prompt"] in submitted.prompt
    assert "一条完整" in submitted.prompt
    for shot in app.get_comic_story(cid)["manifest"]["shots"]:
        for field in ("action", "camera", "transition", "filename"):
            assert shot[field] in submitted.prompt
    assert submitted.config["duration_seconds"] == 12
    assert app.comic_videos.project(app.runtime_store.get_run(result["run_id"]))[
        "submitted_prompt"] == submitted.prompt


def test_large_story_video_stops_before_provider_or_budget_claim(story_app, monkeypatch, tmp_path):
    app = story_app
    pid, cid, provider, request = prepared(app, monkeypatch, tmp_path)
    images = [app.get_comic_story(cid)["story"]["fixed_images"][0]]
    for color in range(1, 9):
        image = _upload(app, pid, cid, content=_png(color))
        _select(app, cid, image)
        images.append(image)
    _save(app, pid, cid, _draft(*images), "save-nine-video")
    _confirm(app, pid, cid, monkeypatch, key="confirm-nine-video")
    story = app.get_comic_story(cid)["story"]
    request.update(_request(app, pid, cid, "nine-image-video"), scope="story", shot_id=None,
                   content_sha256=story["content_sha256"])
    with pytest.raises(ToolError, match="最多使用 8"):
        app.comic_story_operation(cid, "video", request)
    assert not provider.submissions
    assert budget.get_reservation(request["request_id"]) is None


@pytest.mark.parametrize("addition", ["", "Use music that follows the story."])
def test_default_video_scope_generates_one_whole_story_and_deduplicates_exact_export(
    story_app, monkeypatch, tmp_path, addition,
):
    app = story_app
    pid, cid, provider, request = prepared(app, monkeypatch, tmp_path)
    before = app.get_comic_story(cid)
    request.pop("scope")
    request["shot_id"] = None
    request["prompt"] = before["export_text"] + addition
    context = app.comic_videos.view(cid)["story_context"]
    if addition:
        context += "\n成片要求：\n" + addition
    result = app.comic_story_operation(cid, "video", request)
    run = wait_for(app, result["run_id"])
    assert run.state["request"]["scope"] == "story"
    assert provider.submissions[0].prompt == context
    assert provider.submissions[0].prompt.count(before["story"]["summary"]) == 1
    assert run.state["snapshot"]["generation_prompt"] == context
    _save(app, pid, cid, _draft(before["story"]["fixed_images"][0], title="Later story"),
          "later-full-story")
    provider.complete = True
    app.comic_story_operation(cid, "video-query", {"anchor_project_id": pid, "run_id": run.id})
    assert wait_for(app, run.id, ("completed", "failed")).status.value == "completed"
    assert len(provider.submissions) == 1
    final = app.comic_videos.project(app.runtime_store.get_run(run.id))
    assert final["submitted_prompt"] == context


@pytest.mark.parametrize("long_source", [False, True])
def test_full_story_total_prompt_limit_stops_before_any_run_or_paid_claim(
    story_app, monkeypatch, tmp_path, long_source,
):
    app = story_app
    pid, cid, provider, request = prepared(app, monkeypatch, tmp_path)
    if long_source:
        draft = _draft(app.get_comic_story(cid)["story"]["fixed_images"][0])
        draft["shots"][0]["camera"] = "缓慢推进" * 500
        _save(app, pid, cid, draft, "long-video-source")
        _confirm(app, pid, cid, monkeypatch, key="confirm-long-source")
    before = app.get_comic_story(cid)
    request.update(_request(app, pid, cid, "long-full-video"), scope="story", shot_id=None,
                   content_sha256=before["story"]["content_sha256"],
                   prompt="完整作品" if long_source else "补充" * 950)
    with pytest.raises(ToolError, match="2000"):
        app.comic_story_operation(cid, "video", request)
    assert not provider.submissions and budget.get_reservation(request["request_id"]) is None
    assert not app.runtime_store.list_runs(workflow=VIDEO_WORKFLOW, conversation_id=cid)
    assert app.get_comic_story(cid) == before


def test_video_unknown_submission_does_not_resubmit_on_replay_query_or_new_request(
    story_app, monkeypatch, tmp_path,
):
    app = story_app
    pid, cid, provider, request = prepared(app, monkeypatch, tmp_path)
    provider.unknown = True
    result = app.comic_story_operation(cid, "video", request)
    wait_for(app, result["run_id"])
    original = app.comic_story_operation(cid, "video", request)
    assert original["phase"] == "unknown" and not original["can_query"]
    with pytest.raises(ToolError):
        app.comic_story_operation(cid, "video-query", {"run_id": result["run_id"],
                                                       "anchor_project_id": pid})
    with pytest.raises(ToolError):
        app.resume_core_run(result["run_id"])
    with pytest.raises(ToolError, match="待核对"):
        app.comic_story_operation(cid, "video", {**request, "request_id": str(uuid4())})
    reservation = budget.get_reservation(request["request_id"])
    assert reservation.status == "unknown" and reservation.actual_fen is None
    assert len(provider.submissions) == 1


def test_unconfigured_video_does_not_break_story_reading_or_claim_a_paid_request(
    story_app, monkeypatch,
):
    app = story_app
    pid, cid, image = _setup(app)
    _save(app, pid, cid, _draft(image))
    _confirm(app, pid, cid, monkeypatch)
    view = app.comic_videos.view(cid)
    assert not view["ready"] and view["confirmed"] and len(view["manifest"]["shots"]) == 1
    request = {**_request(app, pid, cid, str(uuid4())), "prompt": "Move.", "scope": "shot",
               "shot_id": "story-shot-1", "content_sha256": view["content_sha256"]}
    with pytest.raises(ToolError, match="配置"):
        app.comic_story_operation(cid, "video", request)
    assert app.get_comic_story(cid)["confirmation"]["confirmed"]


def test_xiaoyunque_rejects_mismatched_public_reference_before_submit():
    import hashlib

    from kantoku.capabilities.video import VideoGenerationRequest

    digest = hashlib.sha256(b"original").hexdigest()
    settings = VideoSettings(enabled=True, provider="xiaoyunque", pricing_confirmed=True,
        model=XiaoyunqueVideoProvider.req_key,
        reference_urls={digest: "https://example.tos-cn-beijing.volces.com/frame.png"})
    provider = XiaoyunqueVideoProvider(settings, client=object(), read_url=lambda *_: b"wrong")
    request = VideoGenerationRequest(request_id="offline", run_id="offline", node_id="video",
        image_artifact_id="image", prompt="Move.", project="offline", shot_no=1,
        source_images=[{"artifact_id": "image", "sha256": digest, "run_id": "image-run"}])
    with pytest.raises(ToolError, match="不一致"):
        provider.preflight(request)


def test_xiaoyunque_official_submit_pending_query_and_local_result(tmp_path):
    import hashlib

    from kantoku.capabilities.video import VideoGenerationRequest

    original = b"offline-reference-bytes"
    digest = hashlib.sha256(original).hexdigest()
    reference_url = "https://test.tos-cn-beijing.volces.com/frame.png"
    video_url = "https://test.tos-cn-beijing.volces.com/video.mp4"
    content = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 12
    calls = []
    replies = iter([{"code": 10000, "data": {"task_id": "offline-task"}},
                    {"code": 10000, "data": {"status": "generating"}},
                    {"code": 10000, "data": {"status": "done", "video_url": video_url}}])

    class Client:
        api_info = {action: SimpleNamespace(query={}) for action in (
            "CVSync2AsyncSubmitTask", "CVSync2AsyncGetResult")}

        def common_json_handler(self, action, payload):
            calls.append((action, payload))
            return next(replies)

    settings = VideoSettings(enabled=True, provider="xiaoyunque", pricing_confirmed=True,
        model=XiaoyunqueVideoProvider.req_key, output_dir=tmp_path / "videos",
        reference_urls={digest: reference_url})
    provider = XiaoyunqueVideoProvider(settings, client=Client(),
        read_url=lambda url, _limit: original if url == reference_url else content)
    request = VideoGenerationRequest(request_id="offline", run_id="offline", node_id="video",
        image_artifact_id="image", prompt="A source-bound movement.", project="offline", shot_no=1,
        config={"duration_seconds": 4}, source_images=[
            {"artifact_id": "image", "sha256": digest, "run_id": "image-run"}])
    provider.preflight(request)
    submitted = provider.generate(request)
    assert submitted.status == "pending"
    assert provider.query(request, submitted.provider_job_id).status == "pending"
    result = provider.query(request, submitted.provider_job_id)
    assert result.status == "succeeded" and result.actual_fen is None
    assert not result.mock
    assert calls[0][1]["img_url_list"] == [reference_url]
    assert calls[0][1]["duration"] == "～15s"  # 供应商目标档位，不伪称精确四秒。
    assert [action for action, _ in calls].count("CVSync2AsyncSubmitTask") == 1
    from kantoku.config.runtime_paths import validate_runtime_path
    assert validate_runtime_path(Path(result.location)).read_bytes() == content


def test_existing_video_query_recovers_after_restart_without_requiring_source_file_again(
    story_app, monkeypatch, tmp_path,
):
    app = story_app
    pid, cid, provider, request = prepared(app, monkeypatch, tmp_path)
    result = app.comic_story_operation(cid, "video", request)
    run = wait_for(app, result["run_id"])
    assert run.status.value == "waiting"
    image_id = run.state["snapshot"]["images"][0]["artifact_id"]
    source = app.runtime_store.get_artifact(image_id)
    atomic_write_bytes(Path(source.location), b"externally altered offline source")
    restarted = web_studio.StudioApplication()
    restarted.comic_videos.capability = app.comic_videos.capability
    try:
        provider.complete = True
        replay = restarted.comic_story_operation(cid, "video", request)
        assert replay["run_id"] == run.id and replay["can_query"]
        restarted.comic_story_operation(cid, "video-query", {
            "anchor_project_id": pid, "run_id": run.id})
        assert wait_for(restarted, run.id, ("completed", "failed")).status.value == "completed"
        assert len(provider.submissions) == 1
    finally:
        restarted.runner.close()


def test_concurrent_video_downloads_cannot_replace_different_saved_bytes(tmp_path, monkeypatch):
    from threading import Barrier, Lock

    from kantoku.capabilities.video import VideoGenerationRequest, xiaoyunque

    barrier = Barrier(2)
    file_lock = Lock()
    write = xiaoyunque.atomic_write_bytes

    def concurrent_write(path, content, **kwargs):
        barrier.wait(timeout=5)
        with file_lock:
            return write(path, content, **kwargs)

    monkeypatch.setattr(xiaoyunque, "atomic_write_bytes", concurrent_write)
    request = VideoGenerationRequest(request_id="same-download", run_id="offline", node_id="video",
        image_artifact_id="image", prompt="Move.", project="offline", shot_no=1)

    class Client:
        api_info = {"CVSync2AsyncGetResult": SimpleNamespace(query={})}

        def common_json_handler(self, *_args):
            return {"code": 10000, "data": {"status": "done", "video_url":
                    "https://test.tos-cn-beijing.volces.com/video.mp4"}}

    def download(value):
        content = b"\x00\x00\x00\x18ftypmp42" + value * 12
        provider = XiaoyunqueVideoProvider(VideoSettings(output_dir=tmp_path / "videos"),
            client=Client(), read_url=lambda *_: content)
        try:
            return provider.query(request, "same-task")
        except ToolError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(download, [b"a", b"b"]))
    assert sum(isinstance(result, ToolError) for result in results) == 1
    success = next(result for result in results if isinstance(result, VideoGenerationResult))
    assert Path(success.location).read_bytes() in {
        b"\x00\x00\x00\x18ftypmp42" + value * 12 for value in (b"a", b"b")}


def test_workflow_filter_happens_before_limit_and_does_not_change_default_run_listing(story_app):
    store = story_app.runtime_store
    original = store.create_run("studio", "generic.target", {"conversation_id": "one"}, "start")
    others = [store.create_run("studio", "generic.other", {"conversation_id": "one"}, "start")
              for _ in range(5)]
    assert store.list_runs(limit=1, workflow="generic.target")[0].id == original.id
    assert store.list_runs(limit=1)[0].id == others[-1].id


def test_video_artifact_save_failure_recovers_with_query_and_not_another_submit(
    story_app, monkeypatch, tmp_path,
):
    app = story_app
    pid, cid, provider, request = prepared(app, monkeypatch, tmp_path)
    result = app.comic_story_operation(cid, "video", request)
    wait_for(app, result["run_id"])
    provider.complete = True
    original = app.runtime_store.create_artifact
    def fail(**_kwargs):
        raise OSError("offline artifact persistence interrupted")
    monkeypatch.setattr(app.runtime_store, "create_artifact", fail)
    body = {"anchor_project_id": pid, "run_id": result["run_id"]}
    app.comic_story_operation(cid, "video-query", body)
    wait_for(app, result["run_id"])
    saved = app.comic_videos.view(cid)["tasks"][0]
    assert saved["phase"] == "unknown" and saved["can_query"] and saved["artifact_id"] is None
    monkeypatch.setattr(app.runtime_store, "create_artifact", original)
    app.comic_story_operation(cid, "video-query", body)
    assert wait_for(app, result["run_id"], ("completed", "failed")).status.value == "completed"
    assert len(provider.submissions) == 1


def test_video_api_authentication_and_work_scoping(story_app, monkeypatch, tmp_path):
    import json
    import threading
    from http.client import HTTPConnection

    app = story_app
    _, cid, _, request = prepared(app, monkeypatch, tmp_path)
    work = app.comic_workspace.create({"request_id": str(uuid4()), "title": "Offline video work"})
    server = web_studio.make_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    headers = {"X-Studio-Token": app.token, "Content-Type": "application/json"}
    try:
        path = f"/api/comic/conversations/{cid}/story/video"
        connection.request("GET", path)
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        assert response.status == 200 and json.loads(response.read())["confirmed"]
        policy = response.getheader("Content-Security-Policy")
        assert "media-src 'self' blob:" in policy and "script-src 'self'" in policy
        assert "unsafe-inline" not in policy
        connection.request("POST", path, json.dumps(request), headers)
        response = connection.getresponse()
        assert response.status == 200 and json.loads(response.read())["run_id"]
        modern = f"/api/comic/works/{work['work_id']}/story/video"
        connection.request("GET", modern, headers=headers)
        response = connection.getresponse()
        assert response.status == 200 and not json.loads(response.read())["confirmed"]
        connection.request("POST", modern, json.dumps(request), headers)
        response = connection.getresponse()
        assert response.status == 400
        assert json.loads(response.read())["error_id"]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def test_video_credentials_api_requires_session_and_does_not_echo_secrets(story_app):
    import threading
    from http.client import HTTPConnection

    app = story_app
    settings = VideoSettings(enabled=True, provider="xiaoyunque", pricing_confirmed=False,
        model=XiaoyunqueVideoProvider.req_key, tos_bucket="test-bucket")
    provider = XiaoyunqueVideoProvider(settings, store=app.runtime_store)
    app.comic_videos.capability = VideoService(app.runtime_store, provider, settings)
    server = web_studio.make_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    body = json.dumps({"access_key": "test-access-id", "secret_key": "test-private-secret"})
    try:
        connection.request("POST", "/api/video/credentials", body,
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        assert provider._credentials is None
        connection.request("POST", "/api/video/credentials", body,
            {"Content-Type": "application/json", "X-Studio-Token": app.token})
        response = connection.getresponse()
        assert response.status == 200
        assert response.read() == b'{"configured": true}'
        assert provider._credentials == ("test-access-id", "test-private-secret")
        assert provider.readiness() == "请先配置小云雀费用预估和单次上限"
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
