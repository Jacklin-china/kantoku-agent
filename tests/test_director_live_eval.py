"""真实验收工具的安全门禁离线回归；绝不发起付费请求。"""

from pathlib import Path
from runpy import run_path

import pytest
from test_llm import _settings


@pytest.mark.parametrize("arguments", [[], ["--allow-paid", "--max-cny", "3"],
                                       ["--allow-paid", "--max-cny", "0"]])
def test_director_live_eval_requires_explicit_bounded_authority(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str],
) -> None:
    namespace = run_path(str(Path(__file__).parents[1] / "scripts/verify_director_pipeline.py"))
    runner = namespace["run"]
    monkeypatch.setitem(runner.__globals__, "get_settings",
                        lambda: pytest.fail("无授权不得读取凭据或调用模型"))
    monkeypatch.setattr("sys.argv", ["verify_director_pipeline.py", *arguments])
    with pytest.raises(SystemExit) as error:
        runner()
    assert error.value.code == 2


def test_director_live_eval_rejects_unpriced_model_before_app_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = run_path(str(Path(__file__).parents[1] / "scripts/verify_director_pipeline.py"))
    runner = namespace["run"]
    monkeypatch.setitem(runner.__globals__, "get_settings", _settings)
    monkeypatch.setitem(runner.__globals__, "StudioApplication",
                        lambda: pytest.fail("未知模型价格不得启动实测"))
    monkeypatch.setattr("sys.argv", ["verify_director_pipeline.py", "--allow-paid"])
    with pytest.raises(SystemExit) as error:
        runner()
    assert error.value.code == 2


@pytest.mark.parametrize("arguments", [
    ["--allow-paid", "--max-cny", "3", "--additional-cny", "1"],
    ["--allow-paid", "--additional-cny", "2"],
    ["--allow-paid", "--suite", "story-export", "--budget-id",
     "comic-backend-three-modules-20261012", "--max-cny", "4.01", "--additional-cny", "2"],
    ["--allow-paid", "--suite", "story-export", "--budget-id",
     "comic-backend-three-modules-20261012", "--additional-cny", "3.01"],
    ["--allow-paid", "--suite", "story-export", "--budget-id",
     "comic-backend-three-modules-20261012", "--max-cny", "5.01", "--additional-cny", "3"],
    ["--allow-paid", "--additional-cny", "NaN"],
    ["--allow-paid", "--max-cny", "NaN"],
])
def test_paid_story_additional_allowance_is_not_a_general_budget_bypass(monkeypatch, arguments):
    namespace = run_path(str(Path(__file__).parents[1] / "scripts/verify_director_pipeline.py"))
    runner = namespace["run"]
    monkeypatch.setitem(runner.__globals__, "get_settings",
                        lambda: pytest.fail("未通过追加额度边界不得读取凭据"))
    monkeypatch.setattr("sys.argv", ["verify_director_pipeline.py", *arguments])
    with pytest.raises(SystemExit) as error:
        runner()
    assert error.value.code == 2


@pytest.mark.parametrize("maximum,additional", [("3", "1"), ("4", "2"), ("5", "3")])
def test_paid_story_explicit_extra_allowance_keeps_the_original_identity(
    monkeypatch, maximum, additional,
):
    namespace = run_path(str(Path(__file__).parents[1] / "scripts/verify_director_pipeline.py"))
    runner = namespace["run"]
    def after_authority():
        raise RuntimeError("Validated authority; no credentials or model used")
    monkeypatch.setitem(runner.__globals__, "get_settings", after_authority)
    monkeypatch.setattr("sys.argv", ["verify_director_pipeline.py", "--allow-paid",
        "--suite", "story-export", "--budget-id", "comic-backend-three-modules-20261012",
        "--max-cny", maximum, "--additional-cny", additional])
    with pytest.raises(RuntimeError, match="Validated authority"):
        runner()


@pytest.mark.parametrize("official", [True, False])
def test_paid_input_upper_bound_uses_official_image_tokens_only_for_known_endpoint(official):
    namespace = run_path(str(Path(__file__).parents[1] / "scripts/verify_director_pipeline.py"))
    upper = namespace["acceptance_input_upper"]
    text = [{"role": "user", "content": [{"type": "text", "text": "保持原故事来源。"}]}]
    messages = [{"role": "user", "content": [*text[0]["content"],
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 1000000}}]}]
    base = "https://api.deepseek.com" if official else "https://unknown.example"
    counted = upper(messages, model="deepseek-flash", base_url=base)
    if official:
        assert counted == upper(text, model="deepseek-flash", base_url=base) + 1024
    else:
        assert counted > 1000000
