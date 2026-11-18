"""Real subprocess crash injection for the generic GraphRuntime; no paid services."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from kantoku.config.runtime_paths import isolated_runtime
from kantoku.config.settings import ROOT
from kantoku.core.runtime.graph import END, START, GraphRuntime, WorkflowDefinition, WorkflowNode
from kantoku.core.runtime.models import ExecutionStatus
from kantoku.core.runtime.store import RuntimeStore
from kantoku.core.state import RunState

CRASH_POINTS = (
    "after_prepare_checkpoint",
    "before_external",
    "after_external_commit",
    "after_external_checkpoint",
    "before_archive",
    "after_archive_commit",
    "after_archive_checkpoint",
)
CRASH_EXIT = 73


class RecoveryState(RunState):
    value: int = 0
    archived: bool = False


def _die() -> None:
    os._exit(CRASH_EXIT)


def _attempt(database: Path, node: str) -> None:
    with sqlite3.connect(database, timeout=30) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO reliability_ledger(node,attempts,effect_count) VALUES (?,0,0) "
            "ON CONFLICT(node) DO NOTHING",
            (node,),
        )
        connection.execute(
            "UPDATE reliability_ledger SET attempts=attempts+1 WHERE node=?", (node,)
        )


def _external(database: Path, crash_point: str | None) -> dict:
    _attempt(database, "external")
    if crash_point == "before_external":
        _die()
    with sqlite3.connect(database, timeout=30) as connection:
        connection.execute("BEGIN IMMEDIATE")
        # Simulated provider idempotency record: a replay cannot duplicate the effect.
        connection.execute("UPDATE reliability_ledger SET effect_count=1 WHERE node='external'")
    if crash_point == "after_external_commit":
        _die()
    return {"value": 10}


def _archive(database: Path, crash_point: str | None) -> dict:
    _attempt(database, "archive")
    if crash_point == "before_archive":
        _die()
    with sqlite3.connect(database, timeout=30) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("UPDATE reliability_ledger SET effect_count=1 WHERE node='archive'")
    if crash_point == "after_archive_commit":
        _die()
    return {"archived": True}


def _runtime(store: RuntimeStore, crash_point: str | None) -> GraphRuntime:
    database = store.path
    workflow = WorkflowDefinition(
        id="qa.recovery.v1",
        domain="qa",
        state_type=RecoveryState,
        nodes={
            "prepare": WorkflowNode(
                "prepare", lambda _s, _c: (_attempt(database, "prepare"), {})[1]
            ),
            "external": WorkflowNode("external", lambda _s, _c: _external(database, crash_point)),
            "archive": WorkflowNode("archive", lambda _s, _c: _archive(database, crash_point)),
        },
        edges={START: "prepare", "prepare": "external", "external": "archive", "archive": END},
    )
    runtime = GraphRuntime(store)
    runtime.register(workflow)
    if crash_point and crash_point.endswith("_checkpoint"):
        node = crash_point.removeprefix("after_").removesuffix("_checkpoint")
        original = store.save_checkpoint

        def crash_after_commit(run_id, node_id, next_node, status, state):
            checkpoint = original(run_id, node_id, next_node, status, state)
            if node_id == node:
                _die()
            return checkpoint

        store.save_checkpoint = crash_after_commit
    return runtime


def _prepare_database(database: Path) -> RuntimeStore:
    store = RuntimeStore(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS reliability_ledger ("
            "node TEXT PRIMARY KEY, attempts INTEGER NOT NULL, effect_count INTEGER NOT NULL)"
        )
    return store


def _worker(args) -> int:
    store = _prepare_database(args.database)
    runtime = _runtime(store, args.crash_point)
    run = runtime.create("qa.recovery.v1", RecoveryState())
    args.run_id_file.write_text(run.id, encoding="utf-8")
    runtime.resume(run.id)
    return 0


def _resume(args) -> int:
    store = _prepare_database(args.database)
    run = _runtime(store, None).resume(args.run_id)
    with sqlite3.connect(store.path) as connection:
        connection.row_factory = sqlite3.Row
        ledger = {
            row["node"]: dict(row) for row in connection.execute("SELECT * FROM reliability_ledger")
        }
        checkpoints = connection.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE run_id=?", (args.run_id,)
        ).fetchone()[0]
        event_sequences = [
            row[0]
            for row in connection.execute(
                "SELECT sequence FROM run_events WHERE run_id=?", (args.run_id,)
            )
        ]
    nodes = store.list_nodes(run.id)
    completed = [node.node_id for node in nodes if node.status is ExecutionStatus.COMPLETED]
    passed = (
        run.status is ExecutionStatus.COMPLETED
        and run.state == {"value": 10, "archived": True}
        and completed == ["prepare", "external", "archive"]
        and ledger["prepare"]["attempts"] == 1
        and all(ledger[node]["effect_count"] == 1 for node in ("external", "archive"))
        and len(event_sequences) == len(set(event_sequences))
        and checkpoints >= 3
    )
    result = {
        "run_id": run.id,
        "status": run.status.value,
        "state": run.state,
        "completed_nodes": completed,
        "ledger": ledger,
        "checkpoint_count": checkpoints,
        "event_count": len(event_sequences),
        "passed": passed,
    }
    args.result.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if passed else 1


def run_case(directory: Path, crash_point: str, index: int) -> dict:
    directory.mkdir(parents=True, exist_ok=False)
    database = directory / "runtime.db"
    run_id_file = directory / "run-id.txt"
    result_file = directory / "result.json"
    command = [sys.executable, str(Path(__file__).resolve())]
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    crashed = subprocess.run(
        [
            *command,
            "--worker",
            "--database",
            str(database),
            "--run-id-file",
            str(run_id_file),
            "--crash-point",
            crash_point,
        ],
        cwd=ROOT,
        env=environment,
        timeout=30,
        check=False,
    )
    record = {"case": index, "crash_point": crash_point, "crash_exit_code": crashed.returncode}
    if not run_id_file.is_file():
        return {**record, "passed": False, "error": "run id was not persisted"}
    run_id = run_id_file.read_text(encoding="utf-8").strip()
    resumed = subprocess.run(
        [
            *command,
            "--resume",
            "--database",
            str(database),
            "--run-id",
            run_id,
            "--result",
            str(result_file),
        ],
        cwd=ROOT,
        env=environment,
        timeout=30,
        check=False,
    )
    result = json.loads(result_file.read_text(encoding="utf-8")) if result_file.is_file() else {}
    return {
        **record,
        **result,
        "resume_exit_code": resumed.returncode,
        "passed": bool(
            crashed.returncode == CRASH_EXIT and resumed.returncode == 0 and result.get("passed")
        ),
    }


def _implementation_hashes() -> dict[str, str]:
    files = [
        Path(__file__),
        ROOT / "src/kantoku/core/runtime/graph.py",
        ROOT / "src/kantoku/core/runtime/store.py",
    ]
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--run-id-file", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--crash-point", choices=CRASH_POINTS)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.worker:
        return _worker(args)
    if args.resume:
        return _resume(args)
    if not args.output or args.runs <= 0:
        parser.error("Parent mode requires --output and a positive --runs")
    with isolated_runtime(prefix="kantoku-recovery-") as root:
        cases = [
            run_case(
                root / "tmp" / f"case-{index:02d}",
                CRASH_POINTS[(index - 1) % len(CRASH_POINTS)],
                index,
            )
            for index in range(1, args.runs + 1)
        ]
    by_point = {}
    for point in CRASH_POINTS:
        selected = [case for case in cases if case["crash_point"] == point]
        by_point[point] = {
            "passed": sum(case["passed"] for case in selected),
            "total": len(selected),
        }
    report = {
        "contract": "kantoku-subprocess-recovery-v1",
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "implementation_sha256": _implementation_hashes(),
        "summary": {"passed": sum(case["passed"] for case in cases), "total": len(cases)},
        "by_crash_point": by_point,
        "cases": cases,
        "limitations": [
            "Synthetic three-node workflow with real os._exit subprocess interruption "
            "and a fresh GraphRuntime process",
            "External side effect is a SQLite idempotency simulation, not a paid provider "
            "or exactly-once network guarantee",
            "Does not represent host power loss, multi-node deployment or real model/image quality",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"summary": report["summary"], "by_crash_point": by_point}, ensure_ascii=False, indent=2
        )
    )
    return 0 if all(case["passed"] for case in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
