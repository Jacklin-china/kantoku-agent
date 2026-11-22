from __future__ import annotations

from pathlib import Path

import pytest
from scripts import context_ab_benchmark
from scripts.reliability_benchmark import run_case

from kantoku.evaluation.structured import structured_output_contract


def test_structured_output_failure_injection_contract():
    results = structured_output_contract()
    assert len(results) == 25
    assert all(result["passed"] for result in results), results


@pytest.mark.parametrize("crash_point", ["after_external_commit", "after_archive_checkpoint"])
def test_real_subprocess_recovery_representative_points(tmp_path: Path, crash_point: str):
    result = run_case(tmp_path / crash_point, crash_point, 1)
    assert result["passed"], result
    assert result["status"] == "completed"
    assert result["ledger"]["external"]["effect_count"] == 1
    assert result["ledger"]["archive"]["effect_count"] == 1


def test_context_ab_early_fact_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(context_ab_benchmark, "SCENARIOS", (context_ab_benchmark.SCENARIOS[0],))
    report = context_ab_benchmark.run_benchmark(tmp_path)
    assert report["summary"]["passed"] == report["summary"]["total"] == 1
    case = report["cases"][0]
    assert case["managed_final_turn_tokens"] < case["full_history_tokens"]
    assert case["total_managed_model_input_tokens"] > 0
