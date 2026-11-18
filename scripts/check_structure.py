"""Small repository boundary guard; no runtime dependency or file mutation."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_TOP_LEVEL = {
    ".git", ".github", ".idea", ".pytest_cache", ".ruff_cache", ".run", ".uv-cache",
    ".venv", ".vscode", ".workbuddy", "assets", "config", "data", "db", "docs",
    "logs", "cache", "artifacts", "tmp",
    "evals", "frontend", "scripts", "skills", "src", "tests",
}
PARALLEL_NAME = re.compile(r"_(?:v2|new|fixed|backup)$", re.IGNORECASE)
SENSITIVE_LOG = re.compile(
    r"\blogger\.(?:debug|info|warning|error|critical)\([^\n]*"
    r"(?:api_key|secret_key|access_key|password|authorization|cookie)",
    re.IGNORECASE,
)
FORBIDDEN_IMPORTS = {
    "config": {"core", "capabilities", "adapters", "domains", "shells", "tools"},
    "core": {"adapters", "domains", "shells"},
    "domains": {"adapters", "shells"},
}
# Standalone crash-injection harnesses keep raw connections on purpose: the child
# process is killed with os._exit, and DatabaseManager's anchors, WAL setup and
# path allow-list would change the behaviour being measured. Their numbers live in
# docs/evals/*.json and must stay reproducible by the script that produced them.
RAW_SQLITE_HARNESSES = {
    "scripts/recovery_cost_benchmark.py",
    "scripts/reliability_benchmark.py",
}


def _imports(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def violations() -> list[str]:
    """Return actionable structure problems without touching generated/runtime data."""
    problems = [
        f"unauthorized top-level directory: {path.name}"
        for path in ROOT.iterdir() if path.is_dir() and path.name not in ALLOWED_TOP_LEVEL
    ]
    sources = [*((ROOT / "src" / "kantoku").rglob("*.py")),
               *((ROOT / "tests").rglob("*.py")),
               *((ROOT / "scripts").glob("*.py")),
               *((ROOT / "frontend" / "src").rglob("*.ts")),
               *((ROOT / "frontend" / "src").rglob("*.vue"))]
    for path in sources:
        relative = path.relative_to(ROOT)
        if PARALLEL_NAME.search(path.stem):
            problems.append(f"parallel implementation name: {relative}")
        if path.suffix != ".py":
            continue
        content = path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(path))
        database_factory = relative.as_posix() == "src/kantoku/core/database.py"
        storage_factory = relative.as_posix() == "src/kantoku/config/runtime_paths.py"
        raw_sqlite_harness = relative.as_posix() in RAW_SQLITE_HARNESSES
        application_source = relative.parts[0] == "src"
        sqlite_aliases = {"sqlite3"}
        direct_factories = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                sqlite_aliases.update(alias.asname or alias.name for alias in node.names
                                      if alias.name == "sqlite3")
            elif isinstance(node, ast.ImportFrom) and node.module == "sqlite3":
                direct_factories.update(alias.asname or alias.name for alias in node.names
                                        if alias.name == "connect")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            direct_sqlite = (
                isinstance(function, ast.Attribute) and function.attr == "connect"
                and isinstance(function.value, ast.Name)
                and function.value.id in sqlite_aliases
            ) or (isinstance(function, ast.Name) and function.id in direct_factories)
            if direct_sqlite and not database_factory and not raw_sqlite_harness:
                problems.append(f"SQLite must use DatabaseManager: {relative}:{node.lineno}")
            if (isinstance(function, ast.Attribute)
                and function.attr in {"mkdir", "write_bytes", "write_text"}
                and application_source and not storage_factory):
                problems.append(f"file writes must use RuntimeManager: {relative}:{node.lineno}")
        if application_source and not storage_factory and any(
            name == "tempfile" or name.startswith("tempfile.") for name in _imports(tree)
        ):
            problems.append(f"temporary files must use runtime_paths: {relative}")
        if SENSITIVE_LOG.search(content):
            problems.append(f"possible raw secret in logger call: {relative}")
        layer = relative.parts[2] if len(relative.parts) > 2 else ""
        forbidden = FORBIDDEN_IMPORTS.get(layer, set())
        if forbidden:
            for name in _imports(tree):
                parts = name.split(".")
                if len(parts) >= 2 and parts[0] == "kantoku" and parts[1] in forbidden:
                    problems.append(f"illegal dependency {relative}: {name}")
    return sorted(problems)


def main() -> int:
    problems = violations()
    for problem in problems:
        print(problem)
    if not problems:
        print("Structure guard: PASS")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
