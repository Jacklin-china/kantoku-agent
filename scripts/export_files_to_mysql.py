"""把项目里产生的文件（data/ artifacts/ logs/）收进 MySQL 镜像库。

两类表：
  project_assets        每个文件一行：来源、相对路径、类型、大小、sha256、修改时间、分片数
  project_asset_chunks  文件正文，按 CHUNK_SIZE 分片存 LONGBLOB（避免撞 max_allowed_packet）

不导的：`*.db` / `*.db-wal` / `*.db-shm`——它们就是镜像库的来源本体，重复存一份没有意义。

用法（pymysql 装在项目外，不进项目依赖）：

    PYTHONPATH="C:/Users/lin/.kantoku-tools/pylibs" \\
    .venv/Scripts/python.exe scripts/export_files_to_mysql.py \\
        --host 127.0.0.1 --port 3306 --user root --password 1234 --database kantoku
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("data", "artifacts", "logs")
SKIP_SUFFIXES = {".db", ".db-wal", ".db-shm"}
CHUNK_SIZE = 4 * 1024 * 1024

DDL = [
    """
    CREATE TABLE IF NOT EXISTS `project_assets` (
      `asset_id` VARCHAR(191) NOT NULL,
      `root` VARCHAR(32) NOT NULL,
      `rel_path` VARCHAR(191) NOT NULL,
      `file_name` VARCHAR(191) NOT NULL,
      `kind` VARCHAR(32) NOT NULL,
      `size_bytes` BIGINT NOT NULL,
      `sha256` VARCHAR(64) NOT NULL,
      `modified_at` VARCHAR(64) NOT NULL,
      `chunk_count` INT NOT NULL,
      PRIMARY KEY (`asset_id`),
      UNIQUE KEY `uk_rel_path` (`rel_path`),
      KEY `idx_root_kind` (`root`, `kind`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS `project_asset_chunks` (
      `asset_id` VARCHAR(191) NOT NULL,
      `seq` INT NOT NULL,
      `data` LONGBLOB NOT NULL,
      PRIMARY KEY (`asset_id`, `seq`),
      FOREIGN KEY (`asset_id`) REFERENCES `project_assets` (`asset_id`)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="项目文件 → MySQL 镜像库")
    parser.add_argument("--host", default=os.environ.get("KANTOKU_MYSQL_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("KANTOKU_MYSQL_PORT", 3306)))
    parser.add_argument("--user", default=os.environ.get("KANTOKU_MYSQL_USER", "root"))
    parser.add_argument("--password", default=os.environ.get("KANTOKU_MYSQL_PASSWORD", ""))
    parser.add_argument("--database", default=os.environ.get("KANTOKU_MYSQL_DATABASE", "kantoku"))
    return parser.parse_args()


def collect() -> list[Path]:
    files: list[Path] = []
    for name in SOURCES:
        base = ROOT / name
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix not in SKIP_SUFFIXES:
                files.append(path)
    return sorted(files)


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def main() -> int:
    args = parse_args()
    try:
        import pymysql
    except ImportError:
        print("缺少 pymysql：见本文件顶部用法", file=sys.stderr)
        return 2

    files = collect()
    total_bytes = sum(path.stat().st_size for path in files)
    print(f"待导入 {len(files)} 个文件，共 {total_bytes / 1024 / 1024:.1f} MB")

    connection = pymysql.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
        charset="utf8mb4",
    )
    try:
        with connection.cursor() as cursor:
            for statement in DDL:
                cursor.execute(statement)
            cursor.execute("DELETE FROM `project_asset_chunks`")
            cursor.execute("DELETE FROM `project_assets`")
        connection.commit()

        started = time.time()
        written = 0
        for index, path in enumerate(files, start=1):
            relative = path.relative_to(ROOT).as_posix()
            payload = path.read_bytes()
            asset_id = uuid.uuid4().hex
            chunks = [
                payload[offset : offset + CHUNK_SIZE]
                for offset in range(0, len(payload), CHUNK_SIZE)
            ] or [b""]
            stat = path.stat()
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO `project_assets` "
                    "(`asset_id`,`root`,`rel_path`,`file_name`,`kind`,`size_bytes`,"
                    "`sha256`,`modified_at`,`chunk_count`) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        asset_id,
                        path.parts[len(ROOT.parts)],
                        relative,
                        path.name,
                        path.suffix.lstrip(".").lower() or "none",
                        stat.st_size,
                        hashlib.sha256(payload).hexdigest(),
                        datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                        len(chunks),
                    ),
                )
                cursor.executemany(
                    "INSERT INTO `project_asset_chunks` (`asset_id`,`seq`,`data`) "
                    "VALUES (%s,%s,%s)",
                    [(asset_id, seq, chunk) for seq, chunk in enumerate(chunks)],
                )
            connection.commit()
            written += len(payload)
            if index % 50 == 0 or index == len(files):
                print(
                    f"  {index}/{len(files)}  {written / 1024 / 1024:.1f} MB  "
                    f"用时 {time.time() - started:.1f}s"
                )

        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*), SUM(size_bytes) FROM `project_assets`")
            count, size = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM `project_asset_chunks`")
            chunks = cursor.fetchone()[0]
        print(f"\n完成：{count} 个文件 / {size / 1024 / 1024:.1f} MB / {chunks} 个分片")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
