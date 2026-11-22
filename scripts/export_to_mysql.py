"""把 data/kantoku.db 的数据导入 MySQL 镜像库。

镜像库的定位：**只读分析/展示用途**，不是运行时真源。Kantoku 运行时仍然使用 SQLite，
因为 core 持久层依赖 WAL、`PRAGMA` 与 `sqlite3.Connection.backup()`，换成 MySQL 属于重写持久层。

用法（pymysql 装在项目外的工具目录，不进项目依赖）：

    PYTHONPATH="C:/Users/lin/.kantoku-tools/pylibs" \\
    .venv/Scripts/python.exe scripts/export_to_mysql.py \\
        --host 127.0.0.1 --port 3306 --user root --password 1234 --database kantoku

参数也可以走环境变量：KANTOKU_MYSQL_HOST / PORT / USER / PASSWORD / DATABASE。
连接信息不写入仓库。
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQLITE = ROOT / "data" / "kantoku.db"
SCHEMA = ROOT / "backend" / "db" / "mysql" / "schema.sql"
BATCH = 500


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SQLite → MySQL 镜像库导入")
    parser.add_argument("--host", default=os.environ.get("KANTOKU_MYSQL_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("KANTOKU_MYSQL_PORT", 3306)))
    parser.add_argument("--user", default=os.environ.get("KANTOKU_MYSQL_USER", "root"))
    parser.add_argument("--password", default=os.environ.get("KANTOKU_MYSQL_PASSWORD", ""))
    parser.add_argument("--database", default=os.environ.get("KANTOKU_MYSQL_DATABASE", "kantoku"))
    parser.add_argument("--schema-only", action="store_true", help="只建库建表，不导数据")
    return parser.parse_args()


def statements() -> list[str]:
    text = SCHEMA.read_text(encoding="utf-8")
    # 注释行先剥掉，避免被当成语句开头
    lines = [line for line in text.splitlines() if not line.strip().startswith("--")]
    body = "\n".join(lines)
    return [item.strip() for item in body.split(";") if item.strip()]


def apply_schema(connection, database: str) -> None:
    with connection.cursor() as cursor:
        for statement in statements():
            cursor.execute(statement)
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]
    print(f"建表完成：{database} 下 {len(tables)} 张表")


def table_order(connection: sqlite3.Connection) -> list[str]:
    """按外键依赖做拓扑排序，保证被引用的表先写。"""
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    parents = {table: set() for table in tables}
    for table in tables:
        for fk in connection.execute(f"PRAGMA foreign_key_list({table})"):
            target = fk[2]
            if target in parents and target != table:
                parents[table].add(target)
    ordered: list[str] = []
    remaining = dict(parents)
    while remaining:
        ready = [table for table, deps in remaining.items() if deps <= set(ordered)]
        if not ready:
            # 存在环（或自引用），按名字兜底，MySQL 侧已关闭外键检查
            ordered.extend(sorted(remaining))
            break
        ordered.extend(sorted(ready))
        for table in ready:
            remaining.pop(table)
    return ordered


def copy_table(source: sqlite3.Connection, target, table: str) -> tuple[int, int]:
    columns = [row[1] for row in source.execute(f"PRAGMA table_info({table})")]
    quoted = ", ".join(f"`{name}`" for name in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    select = source.execute(f"SELECT {quoted.replace('`', '')} FROM {table}")
    rows = select.fetchall()
    if not rows:
        return 0, 0
    with target.cursor() as cursor:
        cursor.execute(f"DELETE FROM `{table}`")
        cursor.executemany(
            f"INSERT INTO `{table}` ({quoted}) VALUES ({placeholders})",
            [tuple(row) for row in rows],
        )
    return len(rows), len(columns)


def main() -> int:
    args = parse_args()
    try:
        import pymysql
    except ImportError:
        print("缺少 pymysql：pymysql 装在项目外工具目录，见本文件顶部用法", file=sys.stderr)
        return 2
    if not SQLITE.exists():
        print(f"找不到 {SQLITE}", file=sys.stderr)
        return 2

    target = pymysql.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        apply_schema(target, args.database)
        if args.schema_only:
            print("--schema-only：跳过数据导入")
            return 0

        target.select_db(args.database)
        with target.cursor() as cursor:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0")

        source = sqlite3.connect(SQLITE)
        total_rows = 0
        print(f"\n{'表':34s} {'SQLite':>8s} {'MySQL':>8s}")
        print("-" * 54)
        for table in table_order(source):
            copied, _ = copy_table(source, target, table)
            with target.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
                landed = cursor.fetchone()[0]
            flag = "" if copied == landed else "  ← 不一致"
            total_rows += landed
            print(f"{table:34s} {copied:8d} {landed:8d}{flag}")
        with target.cursor() as cursor:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        print(f"\n共 {total_rows} 行写入 {args.database}")
        return 0
    finally:
        target.close()


if __name__ == "__main__":
    raise SystemExit(main())
