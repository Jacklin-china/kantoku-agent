"""分镜评测：读取固定案例、执行生成器并输出可追溯 Markdown 报告。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from kantoku.config import KantokuError, ToolError
from kantoku.config.runtime_paths import (
    atomic_write_text,
)
from kantoku.memory import Persona
from kantoku.schemas.storyboard import Shot, Storyboard
from kantoku.tools.prompt_factory import PROMPT_VERSION, build_prompt


def image_workbench_quality_metrics(images, manual, pairs):
    """Compute supplied review numbers; provenance/material checks happen separately."""
    correct = sum(item["candidate"]["correct_suggestions"] for item in images)
    incorrect = sum(item["candidate"]["incorrect_suggestions"] for item in images)
    clear = [item for item in manual if item["clarity"] == "clear"]
    ambiguous = [item for item in manual if item["clarity"] == "ambiguous"]
    precision = correct / (correct + incorrect) if correct + incorrect else None
    clear_correct = sum(item["candidate"]["understood_correctly"] for item in clear)
    ambiguity_safe = all(not item["candidate"]["unauthorized_expansion"]
                         and item["candidate"]["would_clarify"] for item in ambiguous)
    comparison = {}
    for label in ("baseline", "candidate"):
        comparison[label] = {"first_fix_success_rate": sum(
            item[label]["first_fix_success"] for item in pairs) / len(pairs) if pairs else None,
            "non_target_damage_rate": sum(item[label]["non_target_damage"] for item in pairs)
                / len(pairs) if pairs else None,
            "mean_rounds_to_usable": sum(item[label]["rounds_to_usable"] for item in pairs)
                / len(pairs) if pairs else None}
    old, new = comparison["baseline"], comparison["candidate"]
    reduced_rework = bool(pairs and new["mean_rounds_to_usable"] < old["mean_rounds_to_usable"]
        and new["non_target_damage_rate"] <= old["non_target_damage_rate"]
        and new["first_fix_success_rate"] >= old["first_fix_success_rate"])
    return {"suggestion_precision": precision, "clear_understanding_correct": clear_correct,
        "ambiguous_without_expansion": ambiguity_safe, "actual_edits": comparison,
        "reduced_rework": reduced_rework,
        "thresholds_met": bool(len(images) >= 24 and precision is not None and precision >= .9
            and len(clear) >= 20 and clear_correct / len(clear) >= .95
            and len(ambiguous) >= 10 and ambiguity_safe and len(pairs) >= 12 and reduced_rework)}


def image_workbench_input_hash(item: dict, kind: str) -> str:
    """Bind comparison receipts to the image, request, scope and reference inputs."""
    fields = {
        "images": ("sha256", "task", "constraints", "references"),
        "manual_requests": ("image_sha256", "text", "context", "edit_scope",
                            "constraints", "references"),
        "edit_pairs": ("before_sha256", "instruction", "constraints", "references"),
    }
    payload = {key: item.get(key) for key in fields[kind]}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def audit_image_workbench_suite(path: Path) -> dict:
    """Read-only audit of real, human-reviewed baseline/candidate results; no model calls."""
    try:
        suite = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise ToolError("图片工作台验收集无法读取") from None
    if not isinstance(suite, dict) or suite.get("contract") != "image-workbench-eval-1":
        raise ToolError("图片工作台验收集契约不匹配")
    pending, ready = [], {"images": [], "manual_requests": [], "edit_pairs": []}

    def valid_image(filename, sha256):
        if not isinstance(filename, str) or not isinstance(sha256, str) or len(sha256) != 64:
            return False
        target = Path(filename)
        if not target.is_absolute():
            target = path.parent / target
        try:
            return (target.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                and 0 < target.stat().st_size <= 5 * 1024 * 1024
                and hashlib.sha256(target.read_bytes()).hexdigest() == sha256)
        except OSError:
            return False

    def valid_receipts(item, kind):
        old, new = item.get("baseline") or {}, item.get("candidate") or {}
        if not isinstance(old, dict) or not isinstance(new, dict):
            return False
        expected = item.get("input_sha256")
        source = "real_external" if kind == "edit_pairs" else "real_model"
        if not (item.get("human_verified") is True and item.get("reviewer")
                and expected and old.get("input_sha256") == new.get("input_sha256") == expected
                and expected == image_workbench_input_hash(item, kind)
                and old.get("model") and old.get("model") == new.get("model")
                and all(receipt.get("result_source") == source for receipt in (old, new))):
            return False
        refs = item.get("references", [])
        if not isinstance(refs, list) or any(not isinstance(ref, dict)
                or not ref.get("usage") or not valid_image(ref.get("image_path"), ref.get("sha256"))
                for ref in refs):
            return False
        if kind == "images":
            return (valid_image(item.get("image_path"), item.get("sha256"))
                and all(isinstance(item.get(key), str) and item[key].strip()
                    for key in ("subject", "style", "issue_type", "task"))
                and isinstance(item.get("expected_issues"), list)
                and isinstance(item.get("preserve"), list) and bool(item.get("preserve"))
                and all(type(receipt.get(name)) is int and receipt[name] >= 0
                    for receipt in (old, new)
                    for name in ("correct_suggestions", "incorrect_suggestions")))
        if kind == "manual_requests":
            return (valid_image(item.get("image_path"), item.get("image_sha256"))
                and isinstance(item.get("text"), str) and bool(item["text"].strip())
                and item.get("clarity") in {"clear", "ambiguous"} and item.get("expected")
                and all(type(receipt.get(name)) is bool for receipt in (old, new)
                    for name in (
                        "understood_correctly", "unauthorized_expansion", "would_clarify")))
        return (valid_image(item.get("before_path"), item.get("before_sha256"))
            and all(valid_image(receipt.get("after_path"), receipt.get("after_sha256"))
                and type(receipt.get("first_fix_success")) is bool
                and type(receipt.get("non_target_damage")) is bool
                and type(receipt.get("rounds_to_usable")) is int
                and 1 <= receipt["rounds_to_usable"] <= 100
                and receipt["first_fix_success"] == (receipt["rounds_to_usable"] == 1)
                for receipt in (old, new)))

    for kind in ready:
        if not isinstance(suite.get(kind, []), list) or any(
                not isinstance(item, dict) for item in suite.get(kind, [])):
            raise ToolError("图片工作台验收集条目格式无效")
        seen = set()
        for item in suite.get(kind, []):
            identity = item.get("sha256") if kind == "images" else item.get("input_sha256")
            if isinstance(identity, str) and identity and identity not in seen and valid_receipts(
                    item, kind):
                seen.add(identity)
                ready[kind].append(item)
            else:
                pending.append({"kind": kind, "id": item.get("id"),
                    "reason": "缺少独立真实素材、人工标注或同输入同模型的真实对照结果"})
    images = ready["images"]
    diversity = (len({item.get("subject") for item in images}) >= 4
        and len({item.get("style") for item in images}) >= 3
        and len({item.get("issue_type") for item in images}) >= 3
        and any(item.get("expected_issues") == [] for item in images))
    baseline = suite.get("baseline_capture") or {}
    if not isinstance(baseline, dict):
        raise ToolError("图片工作台基线记录格式无效")
    baseline_ready = baseline.get("captured_before_changes") is True and bool(
        baseline.get("implementation_sha256"))
    metrics = image_workbench_quality_metrics(images, ready["manual_requests"], ready["edit_pairs"])
    enough = (len(images) >= 24 and len(ready["manual_requests"]) >= 30
              and len(ready["edit_pairs"]) >= 12 and diversity and baseline_ready)
    return {"contract": suite["contract"], "status": "pending" if not enough else
            "passed" if metrics["thresholds_met"] else "failed",
        "quality_improvement_verified": bool(enough and metrics["thresholds_met"]),
        "eligible_counts": {kind: len(rows) for kind, rows in ready.items()},
        "baseline_ready": baseline_ready, "diversity_ready": diversity,
        "metrics": metrics, "pending": pending,
        "paid_calls": 0, "note": "此工具只核验已采集结果，不补造人工标注或绕过付费账本。"}


class EvalExpectation(BaseModel):
    """可自动判定的分镜期望。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    shot_count: int = Field(gt=0)
    required_characters: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_character_names(self) -> Self:
        if any(not name.strip() for name in self.required_characters):
            raise ValueError("required_characters 不允许空名称")
        return self


class EvalCase(BaseModel):
    """一条 JSONL 评测案例。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1)
    input: str = Field(min_length=1)
    expect: EvalExpectation
    rubric: list[str] = Field(min_length=1)


class EvalResult(BaseModel):
    """单案例的自动评测结果。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    passed: bool
    shot_count: int
    field_completeness: float = Field(ge=0, le=1)
    missing_characters: list[str]
    error: str | None


def load_suite(path: Path) -> list[EvalCase]:
    """逐行读取 UTF-8 JSONL，并用行号报告安全错误。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ToolError(
            "无法读取评测集", detail=f"path={path}；error={type(error).__name__}"
        ) from error

    cases: list[EvalCase] = []
    ids: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            case = EvalCase.model_validate_json(line)
        except ValidationError as error:
            raise ToolError(
                "评测案例格式错误",
                detail=f"line={line_number}；error={type(error).__name__}",
            ) from error
        if case.id in ids:
            raise ToolError("评测案例 ID 重复", detail=f"line={line_number}；id={case.id}")
        ids.add(case.id)
        cases.append(case)
    if not cases:
        raise ToolError("评测集不能为空", detail=f"path={path}")
    return cases


def _field_completeness(storyboard: Storyboard) -> float:
    """计算六个下游字段满足数据契约的比例。"""
    if not storyboard.shots:
        return 0.0
    completed = 0
    for shot in storyboard.shots:
        completed += int(shot.shot_no > 0)
        completed += int(bool(shot.desc.strip()))
        completed += int(isinstance(shot.dialogue, str))
        completed += int(bool(shot.camera.strip()))
        completed += int(shot.duration_s > 0)
        completed += int(isinstance(shot.characters, list))
    return completed / (len(storyboard.shots) * 6)


def evaluate_case(
    case: EvalCase,
    generator: Callable[[str], Storyboard],
) -> EvalResult:
    """运行一例，只暴露项目异常的安全消息或未知异常类型。"""
    try:
        storyboard = generator(case.input)
    except KantokuError as error:
        return EvalResult(
            id=case.id,
            passed=False,
            shot_count=0,
            field_completeness=0.0,
            missing_characters=case.expect.required_characters,
            error=f"{type(error).__name__}: {error.message}",
        )
    except Exception as error:
        return EvalResult(
            id=case.id,
            passed=False,
            shot_count=0,
            field_completeness=0.0,
            missing_characters=case.expect.required_characters,
            error=f"UnexpectedError: {type(error).__name__}",
        )

    observed_characters = {
        character for shot in storyboard.shots for character in shot.characters
    }
    missing = sorted(set(case.expect.required_characters) - observed_characters)
    completeness = _field_completeness(storyboard)
    passed = (
        len(storyboard.shots) == case.expect.shot_count
        and completeness == 1.0
        and not missing
    )
    return EvalResult(
        id=case.id,
        passed=passed,
        shot_count=len(storyboard.shots),
        field_completeness=completeness,
        missing_characters=missing,
        error=None,
    )


def run_suite(
    cases: Sequence[EvalCase],
    generator: Callable[[str], Storyboard],
) -> list[EvalResult]:
    """串行运行评测，避免并发扩大花费或触发供应商限流。"""
    return [evaluate_case(case, generator) for case in cases]


def render_report(
    *,
    cases: Sequence[EvalCase],
    results: Sequence[EvalResult],
    model_name: str,
) -> str:
    """生成包含口径、汇总和逐例证据的 Markdown。"""
    if len(cases) != len(results):
        raise ToolError("评测案例与结果数量不一致")
    passed = sum(result.passed for result in results)
    rate = passed / len(results) if results else 0.0
    average_completeness = (
        sum(result.field_completeness for result in results) / len(results) if results else 0.0
    )
    lines = [
        "# M1 分镜生成评测报告",
        "",
        f"- 运行时间（UTC）：{datetime.now(UTC).isoformat()}",
        f"- 模型：`{model_name}`",
        f"- 案例：{len(results)} 条",
        f"- 通过：{passed}/{len(results)}（{rate:.1%}）",
        f"- 平均字段完整率：{average_completeness:.1%}",
        "- 自动口径：目标镜数、六字段数据契约、指定人物名称全部满足才通过。",
        "- 人工口径：叙事节奏、画面可执行性和连续性仍需逐例复核，不由自动分数替代。",
        "",
        "| 案例 | 结果 | 镜数 | 字段完整率 | 缺失人物 | 错误 |",
        "|---|---:|---:|---:|---|---|",
    ]
    for result in results:
        lines.append(
            "| {id} | {status} | {count} | {completeness:.1%} | {missing} | {error} |".format(
                id=result.id,
                status="通过" if result.passed else "失败",
                count=result.shot_count,
                completeness=result.field_completeness,
                missing="、".join(result.missing_characters) or "—",
                error=(result.error or "—").replace("|", "\\|"),
            )
        )
    lines.extend(["", "## 人工复核 Rubric", ""])
    for case in cases:
        lines.append(f"- `{case.id}`：{'；'.join(case.rubric)}")
    return "\n".join(lines) + "\n"


def write_report(path: Path, report: str) -> None:
    """原子保存报告，避免中断时留下半份验收记录。"""
    try:
        atomic_write_text(path, report)
    except (OSError, UnicodeError) as error:
        raise ToolError(
            "无法保存评测报告", detail=f"path={path}；error={type(error).__name__}"
        ) from error


class ConsistencyInput(BaseModel):
    """一致性案例的镜头和相关人设。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    shot: Shot
    personas: list[Persona]


class ConsistencyExpectation(BaseModel):
    """Prompt 中必须出现和不得出现的稳定锚点。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    must_include: list[str] = Field(min_length=1)
    must_exclude: list[str] = Field(default_factory=list)


class ConsistencyEvalCase(BaseModel):
    """一条无需调用模型的一致性评测案例。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1)
    input: ConsistencyInput
    expect: ConsistencyExpectation
    rubric: list[str] = Field(min_length=1)


class ConsistencyEvalResult(BaseModel):
    """一条 Prompt 锚点评测结果。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    passed: bool
    prompt_version: str
    missing: list[str]
    forbidden: list[str]
    error: str | None


def load_consistency_suite(path: Path) -> list[ConsistencyEvalCase]:
    """读取一致性 JSONL，并拒绝坏行和重复 ID。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ToolError(
            "无法读取一致性评测集",
            detail=f"path={path}；error={type(error).__name__}",
        ) from error

    cases: list[ConsistencyEvalCase] = []
    ids: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            case = ConsistencyEvalCase.model_validate_json(line)
        except ValidationError as error:
            raise ToolError(
                "一致性评测案例格式错误",
                detail=f"line={line_number}；error={type(error).__name__}",
            ) from error
        if case.id in ids:
            raise ToolError("一致性评测案例 ID 重复", detail=case.id)
        ids.add(case.id)
        cases.append(case)
    if not cases:
        raise ToolError("一致性评测集不能为空", detail=f"path={path}")
    return cases


def evaluate_consistency_case(case: ConsistencyEvalCase) -> ConsistencyEvalResult:
    """构建 Prompt 并检查角色锚点包含/排除规则。"""
    try:
        prompt = build_prompt(case.input.shot, case.input.personas)
    except KantokuError as error:
        return ConsistencyEvalResult(
            id=case.id,
            passed=False,
            prompt_version=PROMPT_VERSION,
            missing=case.expect.must_include,
            forbidden=[],
            error=f"{type(error).__name__}: {error.message}",
        )
    missing = [token for token in case.expect.must_include if token not in prompt]
    forbidden = [token for token in case.expect.must_exclude if token in prompt]
    return ConsistencyEvalResult(
        id=case.id,
        passed=not missing and not forbidden,
        prompt_version=PROMPT_VERSION,
        missing=missing,
        forbidden=forbidden,
        error=None,
    )


def run_consistency_suite(
    cases: Sequence[ConsistencyEvalCase],
) -> list[ConsistencyEvalResult]:
    """确定性运行全部 Prompt 一致性案例。"""
    return [evaluate_consistency_case(case) for case in cases]


def render_consistency_report(
    *,
    cases: Sequence[ConsistencyEvalCase],
    results: Sequence[ConsistencyEvalResult],
) -> str:
    """输出自动锚点结果，并保留人工五镜验收栏位。"""
    if len(cases) != len(results):
        raise ToolError("一致性案例与结果数量不一致")
    passed = sum(result.passed for result in results)
    rate = passed / len(results) if results else 0.0
    lines = [
        "# M2 Prompt 一致性评测报告",
        "",
        f"- 运行时间（UTC）：{datetime.now(UTC).isoformat()}",
        f"- Prompt 版本：`{PROMPT_VERSION}`",
        f"- 自动锚点通过：{passed}/{len(results)}（{rate:.1%}）",
        "- 人工五镜通过率：待人工看图后填写；不得用字符串命中率代替画面质量。",
        "",
        "| 案例 | 结果 | 缺失锚点 | 混入锚点 | 错误 |",
        "|---|---:|---|---|---|",
    ]
    for result in results:
        lines.append(
            "| {id} | {status} | {missing} | {forbidden} | {error} |".format(
                id=result.id,
                status="通过" if result.passed else "失败",
                missing="、".join(result.missing) or "—",
                forbidden="、".join(result.forbidden) or "—",
                error=(result.error or "—").replace("|", "\\|"),
            )
        )
    lines.extend(["", "## 人工复核 Rubric", ""])
    for case in cases:
        lines.append(f"- `{case.id}`：{'；'.join(case.rubric)}")
    return "\n".join(lines) + "\n"
