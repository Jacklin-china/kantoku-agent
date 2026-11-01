"""固定评测集、自动指标与报告测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from kantoku.config import ToolError
from kantoku.config.settings import ROOT
from kantoku.evaluation import (
    ConsistencyEvalCase,
    EvalCase,
    audit_image_workbench_suite,
    evaluate_case,
    evaluate_consistency_case,
    image_workbench_input_hash,
    image_workbench_quality_metrics,
    load_consistency_suite,
    load_suite,
    render_consistency_report,
    render_report,
    run_consistency_suite,
    run_suite,
    write_report,
)
from kantoku.memory import Persona
from kantoku.schemas.storyboard import Shot, Storyboard


def _case(*, required: list[str] | None = None) -> EvalCase:
    return EvalCase.model_validate(
        {
            "id": "case-1",
            "input": "测试剧本",
            "expect": {"shot_count": 20, "required_characters": required or []},
            "rubric": ["人物连续"],
        }
    )


def test_workbench_template_cannot_claim_real_quality_improvement():
    report = audit_image_workbench_suite(ROOT / "evals" / "image-workbench.json")
    assert report["status"] == "pending" and not report["quality_improvement_verified"]
    assert report["eligible_counts"] == {"images": 0, "manual_requests": 0, "edit_pairs": 0}
    assert report["paid_calls"] == 0 and not report["baseline_ready"]


def test_workbench_metrics_require_real_rework_reduction_and_safe_ambiguity():
    images = [{"candidate": {"correct_suggestions": 9, "incorrect_suggestions": 1}}] * 24
    manual = [{"clarity": "clear", "candidate": {"understood_correctly": index < 19}}
        for index in range(20)] + [{"clarity": "ambiguous", "candidate": {
            "unauthorized_expansion": False, "would_clarify": True}} for _ in range(10)]
    pairs = [{"baseline": {"first_fix_success": False, "non_target_damage": False,
                           "rounds_to_usable": 2},
              "candidate": {"first_fix_success": True, "non_target_damage": False,
                            "rounds_to_usable": 1}} for _ in range(12)]
    assert image_workbench_quality_metrics(images, manual, pairs)["thresholds_met"]
    manual[-1]["candidate"]["unauthorized_expansion"] = True
    assert not image_workbench_quality_metrics(images, manual, pairs)["thresholds_met"]
    manual[-1]["candidate"]["unauthorized_expansion"] = False
    for pair in pairs:
        pair["candidate"] = dict(pair["baseline"])
    assert not image_workbench_quality_metrics(images, manual, pairs)["thresholds_met"]


def test_workbench_receipts_bind_actual_inputs_and_reject_fake_sources(tmp_path):
    from PIL import Image

    photo = tmp_path / "fixture.png"
    Image.new("RGB", (2, 2)).save(photo)
    row = {"id": "offline-fixture", "subject": "测试", "style": "测试", "issue_type": "测试",
        "image_path": str(photo), "sha256": hashlib.sha256(photo.read_bytes()).hexdigest(),
        "task": "检查边缘", "constraints": [], "references": [], "expected_issues": [],
        "preserve": ["轮廓"], "human_verified": True, "reviewer": "offline-test"}
    row["input_sha256"] = image_workbench_input_hash(row, "images")
    # Receipt labels exercise validation only; this one synthetic fixture never meets the suite.
    row["baseline"] = row["candidate"] = {"input_sha256": row["input_sha256"],
        "model": "test", "result_source": "real_model", "correct_suggestions": 1,
        "incorrect_suggestions": 0}
    suite = {"contract": "image-workbench-eval-1", "images": [row]}
    path = tmp_path / "suite.json"
    def audit():
        path.write_text(json.dumps(suite), encoding="utf-8")
        result = audit_image_workbench_suite(path)
        assert not result["quality_improvement_verified"] and result["paid_calls"] == 0
        return result["eligible_counts"]["images"]
    assert audit() == 1
    row["task"] = "改变了输入"
    assert audit() == 0
    row["task"] = "检查边缘"
    row["candidate"] = {**row["baseline"], "result_source": "mock"}
    assert audit() == 0


def _board(characters: list[str] | None = None) -> Storyboard:
    return Storyboard(
        episode="测试",
        shots=[
            Shot(
                shot_no=number,
                desc=f"画面 {number}",
                dialogue="",
                camera="中景",
                duration_s=3,
                characters=characters or [],
            )
            for number in range(1, 21)
        ],
    )


def test_committed_suite_has_ten_unique_cases() -> None:
    cases = load_suite(ROOT / "evals" / "storyboard.jsonl")

    assert len(cases) == 10
    assert len({case.id for case in cases}) == 10
    assert all(case.expect.shot_count == 20 for case in cases)


def test_evaluate_case_passes_complete_expected_characters() -> None:
    result = evaluate_case(_case(required=["阿青"]), lambda _: _board(["阿青"]))

    assert result.passed is True
    assert result.field_completeness == 1.0
    assert result.missing_characters == []


def test_evaluate_case_fails_missing_character() -> None:
    result = evaluate_case(_case(required=["阿青"]), lambda _: _board(["别人"]))

    assert result.passed is False
    assert result.missing_characters == ["阿青"]


def test_evaluate_case_hides_unexpected_error_text() -> None:
    def explode(_: str) -> Storyboard:
        raise RuntimeError("private script")

    result = evaluate_case(_case(), explode)

    assert result.error == "UnexpectedError: RuntimeError"
    assert "private script" not in result.model_dump_json()


def test_run_suite_and_report_are_consistent(tmp_path: Path) -> None:
    cases = [_case()]
    results = run_suite(cases, lambda _: _board())
    report = render_report(cases=cases, results=results, model_name="test-model")
    output = tmp_path / "reports" / "report.md"

    write_report(output, report)

    saved = output.read_text(encoding="utf-8")
    assert "1/1（100.0%）" in saved
    assert "平均字段完整率：100.0%" in saved
    assert "test-model" in saved
    assert not output.with_suffix(".md.tmp").exists()


def test_load_suite_reports_line_number_without_raw_content(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"private":"不要泄漏"}', encoding="utf-8")

    with pytest.raises(ToolError, match="评测案例格式错误") as caught:
        load_suite(path)

    assert caught.value.detail is not None
    assert "line=1" in caught.value.detail
    assert "不要泄漏" not in str(caught.value)


def _consistency_case() -> ConsistencyEvalCase:
    return ConsistencyEvalCase.model_validate(
        {
            "id": "CTX-TEST-01",
            "input": {
                "shot": _board(["阿青"]).shots[0].model_dump(),
                "personas": [
                    Persona(
                        name="阿青",
                        appearance="黑色短发",
                        outfit="蓝色外套",
                        style_tokens=["日系动画"],
                    ).model_dump()
                ],
            },
            "expect": {
                "must_include": ["阿青", "黑色短发", "蓝色外套"],
                "must_exclude": ["阿白"],
            },
            "rubric": ["人物外观一致"],
        }
    )


def test_consistency_suite_has_twenty_cases_and_passes_anchors() -> None:
    cases = load_consistency_suite(ROOT / "evals" / "consistency.jsonl")
    results = run_consistency_suite(cases)

    assert len(cases) == 20
    assert len({case.id for case in cases}) == 20
    assert all(result.passed for result in results)


def test_consistency_case_reports_missing_and_forbidden_tokens() -> None:
    case_data = _consistency_case().model_dump()
    case_data["expect"] = {
        "must_include": ["不存在的锚点"],
        "must_exclude": ["蓝色外套"],
    }
    validated = ConsistencyEvalCase.model_validate(case_data)

    result = evaluate_consistency_case(validated)

    assert result.passed is False
    assert result.missing == ["不存在的锚点"]
    assert result.forbidden == ["蓝色外套"]


def test_consistency_report_does_not_fake_human_acceptance() -> None:
    case = _consistency_case()
    results = run_consistency_suite([case])
    report = render_consistency_report(cases=[case], results=results)

    assert "自动锚点通过：1/1" in report
    assert "人工五镜通过率：待人工看图后填写" in report
