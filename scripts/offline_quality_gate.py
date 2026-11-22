"""Run only offline gates and label paid/unavailable suites as not run or pending."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from datetime import UTC, datetime
from pathlib import Path

from kantoku.config.settings import ROOT
from kantoku.evaluation import (
    audit_image_workbench_suite,
    load_consistency_suite,
    load_suite,
    run_consistency_suite,
)
from kantoku.evaluation.structured import structured_output_contract


def _count_jsonl(path: Path) -> int:
    return sum(bool(line.strip()) for line in path.read_text(encoding="utf-8").splitlines())


def _hashes(paths: list[Path]) -> dict[str, str]:
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
    }


def run() -> dict:
    consistency_cases = load_consistency_suite(ROOT / "backend" / "evals/consistency.jsonl")
    consistency_results = run_consistency_suite(consistency_cases)
    structured = structured_output_contract()
    storyboard_count = len(load_suite(ROOT / "backend" / "evals/storyboard.jsonl"))
    image = audit_image_workbench_suite(ROOT / "backend" / "evals/image-workbench.json")
    consistency_passed = sum(result.passed for result in consistency_results)
    structured_passed = sum(result["passed"] for result in structured)
    executed_total = len(consistency_results) + len(structured)
    executed_passed = consistency_passed + structured_passed
    return {
        "contract": "kantoku-offline-quality-gate-v1",
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "model_calls": 0,
        },
        "implementation_sha256": _hashes(
            [
                ROOT / "backend" / "evals/consistency.jsonl",
                ROOT / "backend" / "evals/storyboard.jsonl",
                ROOT / "backend" / "evals/director.jsonl",
                ROOT / "backend" / "evals/image-workbench.json",
                ROOT / "backend" / "src" / "kantoku/evaluation/__init__.py",
                ROOT / "backend" / "src" / "kantoku/evaluation/structured.py",
                ROOT / "backend" / "src" / "kantoku/tools/storyboard.py",
            ]
        ),
        "suites": {
            "consistency": {
                "status": "passed" if consistency_passed == len(consistency_results) else "failed",
                "passed": consistency_passed,
                "total": len(consistency_results),
            },
            "structured_output": {
                "status": "passed" if structured_passed == len(structured) else "failed",
                "passed": structured_passed,
                "total": len(structured),
                "cases": structured,
            },
            "storyboard_generation": {
                "status": "not_run",
                "cases": storyboard_count,
                "reason": "Requires an explicitly authorized real model run; no paid call was made",
            },
            "director_generation": {
                "status": "not_run",
                "cases": _count_jsonl(ROOT / "backend" / "evals/director.jsonl"),
                "reason": (
                    "Requires real model outputs and human review; "
                    "not inferred from offline fixtures"
                ),
            },
            "image_workbench": {
                "status": image["status"],
                "quality_improvement_verified": image["quality_improvement_verified"],
                "eligible_counts": image["eligible_counts"],
                "pending": image["pending"],
            },
        },
        "summary": {
            "executed_passed": executed_passed,
            "executed_total": executed_total,
            "quality_release_ready": False,
        },
        "limitations": [
            "Offline checks cover deterministic prompt anchors and schema rejection; "
            "not real model/image quality",
            "not_run and pending suites are excluded from the offline pass count "
            "and block quality-release claims",
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "summary": report["summary"],
                "suites": {
                    key: {k: v for k, v in value.items() if k not in {"cases", "pending"}}
                    for key, value in report["suites"].items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["summary"]["executed_passed"] == report["summary"]["executed_total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
