"""Same-crash A/B: checkpoint resume vs full restart, with real subprocess crashes.

The six provider nodes are deterministic test doubles. Invocation counts are real;
provider prices and semantic output quality are intentionally not simulated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from kantoku.config.runtime_paths import isolated_runtime
from kantoku.config.settings import ROOT
from kantoku.core.database import database_manager
from kantoku.core.runtime.graph import END, START, GraphRuntime, WorkflowDefinition, WorkflowNode
from kantoku.core.runtime.models import ExecutionStatus
from kantoku.core.runtime.store import RuntimeStore
from kantoku.core.state import RunState

NODES = tuple(f"model_{index}" for index in range(1, 7))
CRASH_EXIT = 73


class RecoveryCostState(RunState):
    value: int = 0


def _ledger(database: Path) -> dict[str, dict]:
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        return {
            row["node"]: dict(row)
            for row in connection.execute("SELECT * FROM recovery_cost_ledger ORDER BY node")
        }


def _prepare(database: Path) -> RuntimeStore:
    store = RuntimeStore(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS recovery_cost_ledger ("
            "node TEXT PRIMARY KEY, attempts INTEGER NOT NULL, effect_count INTEGER NOT NULL)"
        )
    return store


def _invoke_provider(database: Path, node: str, crash_node: str | None) -> None:
    with closing(sqlite3.connect(database, timeout=30)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO recovery_cost_ledger(node,attempts,effect_count) VALUES (?,0,0) "
            "ON CONFLICT(node) DO NOTHING",
            (node,),
        )
        connection.execute(
            "UPDATE recovery_cost_ledger SET attempts=attempts+1,effect_count=1 WHERE node=?",
            (node,),
        )
    if crash_node == node:
        # The simulated provider commit exists, but the runtime checkpoint does not.
        os._exit(CRASH_EXIT)


def _runtime(database: Path, crash_node: str | None = None) -> GraphRuntime:
    nodes = {}
    edges = {START: NODES[0], NODES[-1]: END}
    for index, node in enumerate(NODES, start=1):
        nodes[node] = WorkflowNode(
            node,
            lambda _state, _context, database=database, node=node: (
                _invoke_provider(database, node, crash_node),
                {"value": int(node.rsplit("_", 1)[1])},
            )[1],
        )
        if index < len(NODES):
            edges[node] = NODES[index]
    runtime = GraphRuntime(_prepare(database))
    runtime.register(
        WorkflowDefinition(
            id="qa.recovery-cost.v1",
            domain="qa",
            state_type=RecoveryCostState,
            nodes=nodes,
            edges=edges,
        )
    )
    return runtime


def _worker(args) -> int:
    database = args.database
    runtime = _runtime(database, args.crash_node)
    run = runtime.create("qa.recovery-cost.v1", RecoveryCostState())
    args.run_id_file.write_text(run.id, encoding="utf-8")
    runtime.resume(run.id)
    return 0


def _complete(run, database: Path) -> bool:
    ledger = _ledger(database)
    return (
        run.status is ExecutionStatus.COMPLETED
        and run.state == {"value": 6}
        and set(ledger) == set(NODES)
        and all(row["effect_count"] == 1 for row in ledger.values())
    )


def run_case(root: Path, crash_node: str, index: int) -> dict:
    case_dir = root / "tmp" / f"case-{index:02d}"
    case_dir.mkdir(parents=True)
    crashed_db = case_dir / "crashed.db"
    run_id_file = case_dir / "run-id.txt"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--database",
        str(crashed_db),
        "--run-id-file",
        str(run_id_file),
        "--crash-node",
        crash_node,
    ]
    crashed = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=30,
        check=False,
    )
    record = {"case": index, "crash_node": crash_node, "crash_exit_code": crashed.returncode}
    if crashed.returncode != CRASH_EXIT or not run_id_file.is_file():
        return {**record, "passed": False, "error": "crash injection did not reach its checkpoint gap"}

    before = _ledger(crashed_db)
    run_id = run_id_file.read_text(encoding="utf-8").strip()
    outcomes = {}
    for strategy in ("resume", "restart"):
        strategy_db = case_dir / f"{strategy}.db"
        # SQLite backup includes committed WAL pages; a plain file copy does not.
        with closing(sqlite3.connect(crashed_db)) as source, closing(sqlite3.connect(strategy_db)) as target:
            source.backup(target)
        runtime = _runtime(strategy_db)
        selected_run_id = run_id
        if strategy == "restart":
            selected_run_id = runtime.create("qa.recovery-cost.v1", RecoveryCostState()).id
        run = runtime.resume(selected_run_id)
        after = _ledger(strategy_db)
        recovery_calls = sum(
            after[node]["attempts"] - before.get(node, {"attempts": 0})["attempts"]
            for node in NODES
        )
        outcomes[strategy] = {
            "completed": _complete(run, strategy_db),
            "recovery_provider_calls": recovery_calls,
            "total_provider_calls_including_crashed_attempt": sum(
                row["attempts"] for row in after.values()
            ),
            "effect_counts": {node: after[node]["effect_count"] for node in NODES},
        }
    expected_resume_calls = len(NODES) - int(crash_node.rsplit("_", 1)[1]) + 1
    passed = (
        all(outcome["completed"] for outcome in outcomes.values())
        and outcomes["restart"]["recovery_provider_calls"] == len(NODES)
        and outcomes["resume"]["recovery_provider_calls"] == expected_resume_calls
    )
    return {**record, "before_crash": before, **outcomes, "passed": passed}


def _hashes() -> dict[str, str]:
    paths = [
        Path(__file__),
        ROOT / "src/kantoku/core/runtime/graph.py",
        ROOT / "src/kantoku/core/runtime/store.py",
    ]
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _average(values: list[int]) -> float:
    return round(sum(values) / len(values), 3)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--run-id-file", type=Path)
    parser.add_argument("--crash-node", choices=NODES)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.worker:
        return _worker(args)
    if not args.output or args.repeats <= 0:
        parser.error("Parent mode requires --output and a positive --repeats")
    with isolated_runtime(prefix="kantoku-recovery-cost-") as root:
        try:
            cases = [
                run_case(root, node, index)
                for index, node in enumerate(NODES * args.repeats, start=1)
            ]
        finally:
            shutdown = database_manager.checkpoint_all(within=root)
            blocked = [f"{item.path}: {item.status}" for item in shutdown if item.status != "READY"]
            if blocked:
                raise RuntimeError("database cleanup blocked: " + "; ".join(blocked))
    resume_recovery = [case["resume"]["recovery_provider_calls"] for case in cases if case["passed"]]
    restart_recovery = [case["restart"]["recovery_provider_calls"] for case in cases if case["passed"]]
    resume_total = [
        case["resume"]["total_provider_calls_including_crashed_attempt"]
        for case in cases
        if case["passed"]
    ]
    restart_total = [
        case["restart"]["total_provider_calls_including_crashed_attempt"]
        for case in cases
        if case["passed"]
    ]
    summary = {
        "passed": sum(case["passed"] for case in cases),
        "total": len(cases),
        "crash_positions": len(NODES),
        "provider_nodes_per_run": len(NODES),
        "resume_recovery_calls_average": _average(resume_recovery),
        "restart_recovery_calls_average": _average(restart_recovery),
        "recovery_call_reduction_percent": round(
            100 * (1 - sum(resume_recovery) / sum(restart_recovery)), 2
        ),
        "resume_total_calls_average": _average(resume_total),
        "restart_total_calls_average": _average(restart_total),
        "total_call_reduction_percent": round(100 * (1 - sum(resume_total) / sum(restart_total)), 2),
        "duplicate_external_effects": sum(
            effect_count != 1
            for case in cases
            for strategy in ("resume", "restart")
            for effect_count in case[strategy]["effect_counts"].values()
        ) if all(case["passed"] for case in cases) else None,
    }
    report = {
        "contract": "kantoku-recovery-cost-ab-v1",
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "implementation_sha256": _hashes(),
        "summary": summary,
        "cases": cases,
        "limitations": [
            "Six deterministic provider test doubles count real invocations; no paid model was called",
            "Crash occurs after each simulated provider commit and before its runtime checkpoint",
            "Resume and restart start from byte-identical copies of the same crashed SQLite database",
            "Effect idempotency is simulated by a SQLite ledger, not a real provider guarantee",
            "Call reduction is not a currency cost or production availability measurement",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
