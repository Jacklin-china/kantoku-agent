"""Exercise the real storyboard tool with valid and invalid deterministic model outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from kantoku.capabilities import storyboard as storyboard_tool
from kantoku.config import SchemaError
from kantoku.config.runtime_paths import isolated_runtime
from kantoku.config.settings import ROOT
from kantoku.schemas.storyboard import Shot, Storyboard


def _board(identifier: int) -> dict:
    return Storyboard(
        episode=f"离线压力用例-{identifier:03d}",
        shots=[
            Shot(
                shot_no=number,
                desc=f"用例{identifier}镜头{number}",
                dialogue="",
                camera="中景",
                duration_s=3,
                characters=["阿青"],
            )
            for number in range(1, 21)
        ],
    ).model_dump(mode="json")


def _invalid_payloads() -> list[tuple[str, str, str]]:
    mutations = {
        "missing_episode": lambda payload: payload.pop("episode"),
        "nineteen_shots": lambda payload: payload.update(shots=payload["shots"][:19]),
        "duplicate_shot_no": lambda payload: payload["shots"][1].update(shot_no=1),
        "blank_description": lambda payload: payload["shots"][0].update(desc=" "),
        "zero_duration": lambda payload: payload["shots"][0].update(duration_s=0),
        "float_duration": lambda payload: payload["shots"][0].update(duration_s=3.5),
        "characters_string": lambda payload: payload["shots"][0].update(characters="阿青"),
        "extra_top_level": lambda payload: payload.update(unexpected=True),
        "extra_shot_field": lambda payload: payload["shots"][0].update(unexpected=True),
        "noncontiguous_shot_no": lambda payload: payload["shots"][19].update(shot_no=22),
    }
    cases = []
    for mutation, apply in mutations.items():
        for variant in range(10):
            payload = _board(variant)
            apply(payload)
            cases.append((f"{mutation}-{variant:02d}", mutation, json.dumps(payload, ensure_ascii=False)))
    return cases


def _invoke(raw: str, database: Path):
    settings = SimpleNamespace(
        storage=SimpleNamespace(sqlite_path=database),
        llm=SimpleNamespace(structured_temperature=0.2),
    )
    storyboard_tool.get_settings = lambda: settings
    storyboard_tool.chat = lambda *_args, **_kwargs: SimpleNamespace(content=raw)
    return storyboard_tool.generate_storyboard("离线结构化输出压力测试")


def run(root: Path) -> list[dict]:
    original_settings = storyboard_tool.get_settings
    original_chat = storyboard_tool.chat
    results = []
    try:
        for index in range(20):
            board = _board(index)
            database = root / "tmp" / f"valid-{index:02d}" / "kantoku.db"
            identifier = f"valid-{index:02d}"
            try:
                parsed = _invoke(json.dumps(board, ensure_ascii=False), database)
                passed = database.is_file() and len(parsed.shots) == 20
                error = None
            except Exception as exc:  # Report the actual failure instead of hiding it.
                passed, error = False, f"Unexpected:{type(exc).__name__}"
            results.append({"id": identifier, "expected": "persisted", "passed": passed, "error": error})
        for identifier, mutation, raw in _invalid_payloads():
            database = root / "tmp" / identifier / "kantoku.db"
            try:
                _invoke(raw, database)
                passed, error = False, "invalid output was persisted"
            except SchemaError as exc:
                passed, error = not database.exists(), f"{type(exc).__name__}: {exc.message}"
            except Exception as exc:
                passed, error = False, f"Unexpected:{type(exc).__name__}"
            results.append({
                "id": identifier,
                "mutation": mutation,
                "expected": "rejected_before_database_creation",
                "database_created": database.exists(),
                "passed": passed,
                "error": error,
            })
    finally:
        storyboard_tool.get_settings = original_settings
        storyboard_tool.chat = original_chat
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    with isolated_runtime(prefix="kantoku-structured-stress-") as root:
        results = run(root)
    valid = [case for case in results if case["expected"] == "persisted"]
    invalid = [case for case in results if case["expected"] != "persisted"]
    summary = {
        "passed": sum(case["passed"] for case in results),
        "total": len(results),
        "valid_persisted": sum(case["passed"] for case in valid),
        "valid_total": len(valid),
        "invalid_rejected_before_database_creation": sum(case["passed"] for case in invalid),
        "invalid_total": len(invalid),
    }
    paths = [
        Path(__file__),
        ROOT / "backend" / "src/kantoku/capabilities/storyboard.py",
        ROOT / "src/kantoku/schemas/storyboard.py",
    ]
    report = {
        "contract": "kantoku-structured-stress-v1",
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "model": "deterministic outputs injected at the existing chat boundary",
        },
        "implementation_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths
        },
        "summary": summary,
        "cases": results,
        "limitations": [
            "Synthetic mutations test schema and persistence fail-closed behavior, not model violation frequency",
            "Every invalid case uses the real generate_storyboard tool and an isolated SQLite path",
            "No real model generation, semantic constraint quality or image quality is inferred",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
