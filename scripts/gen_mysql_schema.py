"""从 PRAGMA 元数据生成 MySQL 8 DDL（不靠正则改 SQLite 原文）。

原则：
- 所有标识符加反引号，避免 `desc` / `status` 之类的保留字冲突；
- 被主键 / 唯一键 / 外键 / 索引引用的文本列降为 VARCHAR(191)，MySQL 不允许 LONGTEXT 直接做键；
- `*_at` 时间列用 VARCHAR(64) 原样保留 ISO 字符串，不做时区转换，保证镜像零语义损失；
- 其余文本列保持 LONGTEXT；不迁移 SQLite 的 CHECK 约束（MySQL 侧靠应用校验）。

用法：PYTHONPATH= .venv/Scripts/python.exe scripts/gen_mysql_schema.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path("data/kantoku.db")
OUT = Path("backend/db/mysql/schema.sql")
KEY_LEN = 191


def q(name: str) -> str:
    return f"`{name}`"


def column_ddl(table: str, column: tuple, key_columns: set[str]) -> str:
    _cid, name, decl_type, notnull, default, pk = column
    upper = (decl_type or "").upper()
    indexed = name in key_columns or bool(pk)
    if "INT" in upper:
        base = "BIGINT"
    elif "REAL" in upper or "FLOA" in upper or "DOUB" in upper:
        base = "DOUBLE"
    elif "BLOB" in upper or not upper:
        base = "LONGBLOB"
    elif "NUMERIC" in upper or "DECIMAL" in upper:
        base = "DECIMAL(20,6)"
    else:
        if indexed:
            base = f"VARCHAR({KEY_LEN})"
        elif name.endswith("_at"):
            base = "VARCHAR(64)"
        else:
            base = "LONGTEXT"

    parts = [q(name), base]
    if pk or notnull:
        parts.append("NOT NULL")
    if default is not None and "CURRENT_TIMESTAMP" not in str(default).upper():
        rendered = str(default)
        if rendered.upper() == "NULL":
            parts.append("DEFAULT NULL")
        elif base.startswith(("BIGINT", "DOUBLE", "DECIMAL")):
            parts.append(f"DEFAULT {rendered}")
        else:
            # PRAGMA 给的默认值已自带引号（如 '{}'），去掉再按 MySQL 规则重加
            literal = rendered
            if len(literal) >= 2 and literal[0] == "'" and literal[-1] == "'":
                literal = literal[1:-1].replace("''", "'")
            literal = literal.replace("'", "''")
            # MySQL 8.0.13+ 允许 TEXT/BLOB 用表达式默认值，必须带括号
            parts.append(f"DEFAULT ('{literal}')" if base == "LONGTEXT" else f"DEFAULT '{literal}'")
    return " ".join(parts)


def main() -> None:
    connection = sqlite3.connect(DB)
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]

    blocks: list[str] = []
    summary: list[tuple[str, int, int, int]] = []

    for table in tables:
        info = connection.execute(f"PRAGMA table_info({table})").fetchall()
        fks = connection.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        indexes = connection.execute(f"PRAGMA index_list({table})").fetchall()

        key_columns: set[str] = set()
        unique_groups: list[list[str]] = []
        plain_indexes: list[tuple[str, list[str]]] = []

        for index in indexes:
            index_name, unique = index[1], index[2]
            columns = [row[2] for row in connection.execute(f"PRAGMA index_info({index_name})")]
            key_columns.update(columns)
            if unique:
                unique_groups.append(columns)
            else:
                plain_indexes.append((index_name, columns))

        # 外键列即使没有显式索引也必须可做键，否则 MySQL 报 1170
        key_columns.update(fk[3] for fk in fks)

        lines = [f"DROP TABLE IF EXISTS {q(table)};", f"CREATE TABLE {q(table)} ("]
        column_lines = [column_ddl(table, tuple(row), key_columns) for row in info]
        pk_columns = [row[1] for row in info if row[5]]
        if len(pk_columns) == 1:
            column_lines[[row[1] for row in info].index(pk_columns[0])] += " PRIMARY KEY"
        elif pk_columns:
            column_lines.append("PRIMARY KEY (" + ", ".join(q(c) for c in pk_columns) + ")")
        for columns in unique_groups:
            if columns == pk_columns:
                continue
            column_lines.append("UNIQUE KEY (" + ", ".join(q(c) for c in columns) + ")")
        for fk in fks:
            column_lines.append(
                "FOREIGN KEY (" + q(fk[3]) + ") REFERENCES " + q(fk[2]) + " (" + q(fk[4]) + ")"
            )
        lines.append("  " + ",\n  ".join(column_lines))
        lines.append(") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;")
        for index_name, columns in plain_indexes:
            lines.append(
                f"CREATE INDEX {q(index_name)} ON {q(table)} ("
                + ", ".join(q(c) for c in columns) + ");"
            )
        blocks.append("\n".join(lines))
        summary.append((table, len(info), len(fks), len(indexes)))

    header = [
        "-- Kantoku · MySQL 8 镜像库 schema",
        "-- 由 data/kantoku.db 的 PRAGMA 元数据生成，共 "
        f"{len(tables)} 张表",
        "-- 生成脚本：scripts/gen_mysql_schema.py",
        "-- 注意：文本列按 UTF8MB4 存储；被键引用的文本列降为 VARCHAR(191)；",
        "--       SQLite 的 CHECK 约束未迁移，业务校验仍由应用侧完成。",
        "",
        "CREATE DATABASE IF NOT EXISTS `kantoku`",
        "  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;",
        "USE `kantoku`;",
        "SET FOREIGN_KEY_CHECKS = 0;",
        "",
    ]
    footer = ["", "SET FOREIGN_KEY_CHECKS = 1;", ""]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(header) + "\n".join(blocks) + "\n" + "\n".join(footer)
    OUT.write_text(body, encoding="utf-8")

    print(f"表 {len(tables)} 张 → {OUT}")
    for table, columns, fk_count, index_count in summary:
        print(f"  {table:34s} {columns:2d} 列  {fk_count} 外键  {index_count} 索引")


if __name__ == "__main__":
    main()
