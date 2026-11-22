# MySQL 镜像库

`kantoku` 库是 `data/kantoku.db` 的**只读镜像**，用于在数据库客户端里查看与做分析查询。

**它不是运行时真源。** Kantoku 运行时仍使用 SQLite：`core` 持久层依赖 WAL、`PRAGMA` 与
`sqlite3.Connection.backup()`，换驱动等于重写持久层（见 [project-layout](../../docs/engineering/project-layout.md) §9）。

## 生成与导入

```bash
# 1. 由现有 SQLite 结构生成 DDL（结构变了就重跑）
PYTHONPATH= .venv/Scripts/python.exe scripts/gen_mysql_schema.py

# 2. 建库建表 + 导入数据（pymysql 装在项目外，不进项目依赖）
PYTHONPATH="C:/Users/lin/.kantoku-tools/pylibs" \
  .venv/Scripts/python.exe scripts/export_to_mysql.py \
    --host 127.0.0.1 --port 3306 --user root --password <密码> --database kantoku

# 只建表不导数据
... scripts/export_to_mysql.py --schema-only
```

连接信息不写进仓库，可用环境变量 `KANTOKU_MYSQL_HOST/PORT/USER/PASSWORD/DATABASE` 提供。

也可以在 DBeaver 里直接打开 [schema.sql](schema.sql) 全选执行，手动建表。

## 转换规则

| 规则 | 说明 |
|---|---|
| 标识符 | 全部加反引号，避免 `desc`、`status` 等保留字冲突 |
| 被键引用的文本列 | 降为 `VARCHAR(191)`，MySQL 不允许 `LONGTEXT` 直接做键（否则报 1170） |
| 外键列 | 即使没有显式索引也按键处理，同样降为 `VARCHAR(191)` |
| `*_at` 时间列 | `VARCHAR(64)` 原样保留 ISO 字符串，不做时区转换，镜像零语义损失 |
| 其余文本 | `LONGTEXT`；默认值写成表达式 `DEFAULT ('{}')`（MySQL 8.0.13+） |
| `INTEGER` / `REAL` / `BLOB` | `BIGINT` / `DOUBLE` / `LONGBLOB` |
| CHECK 约束 | **未迁移**，业务校验仍由应用侧完成 |

## 35 张表

| 组 | 张数 | 表 |
|---|---|---|
| Core 运行时 | 11 | `runs` `checkpoints` `run_events` `node_executions` `approvals` `artifacts` `archive_asset` `ledger` `trace` `batches` `batch_runs` |
| 技能执行 | 1 | `skill_executions` |
| 文件生命周期 | 3 | `runtime_files` `runtime_file_leases` `media_jobs` |
| 会话 | 3 | `conversations` `conversation_messages` `conversation_generation_requests` |
| 漫剧域 | 7 | `comic_projects` `comic_assets` `comic_entity_versions` `comic_shots` `comic_storyboards` `comic_prompts` `comic_schema_migrations` |
| 旧内容域 | 7 | `shot` `persona` `recipe` `image_result` `qc_review` `qc_prediction` `rework_queue` |
| 账号 | 2 | `auth_owner` `auth_sessions` |
| 迁移记录 | 2 | `core_schema_migrations` `comic_schema_migrations` |

## 2026-11-22 首次导入结果

35 张表全部写入，共 **27,719 行**，逐表 SQLite 源行数与 MySQL 落地行数一致。
数据量集中在 `comic_entity_versions`（6,203 行 / 54.6 MB）、`runs`（1,934 行 / 52.5 MB）、
`run_events`（12,381 行 / 8.5 MB）。

---

# 项目文件入仓

结构化数据走上面 35 张表；**项目运行产生的文件**（`data/` `artifacts/` `logs/` 下的非数据库文件）
另外进两张表，用分片避免撞 `max_allowed_packet`。

```bash
PYTHONPATH="C:/Users/lin/.kantoku-tools/pylibs" \
  .venv/Scripts/python.exe scripts/export_files_to_mysql.py \
    --password <密码>
```

| 表 | 作用 |
|---|---|
| `project_assets` | 每个文件一行：`root`（data/artifacts/logs）、`rel_path`、`kind`、`size_bytes`、`sha256`、`modified_at`、`chunk_count` |
| `project_asset_chunks` | 文件正文，按 4 MB 分片存 `LONGBLOB`，主键 `(asset_id, seq)` |

**不导入** `*.db` / `*.db-wal` / `*.db-shm`——它们就是镜像的来源本体，重复存一份没有意义。

## 2026-11-22 文件导入结果

| 来源 | 文件数 | 体积 |
|---|---|---|
| `artifacts/` | 169 | 1019.7 MB |
| `data/` | 4,944 | 100.6 MB |
| `logs/` | 1 | 0.5 MB |
| **合计** | **5,114** | **1,120.8 MB**（5,336 个分片） |

`data/` 的 4,944 个文件里 4,930 个来自 `data/quick-domain-delivery/checkout/`——那是历史交付快照，
内含一份完整项目副本（含 `frontend/node_modules`）。

完整性抽查：`artifacts/comic-stories/nezha-v10-project-storyboard.html`（74,209,675 字节 / 18 片）
按 `seq` 逐片取回重建后 sha256 与磁盘原件一致。

## 还原单个文件

```sql
SELECT seq, data FROM project_asset_chunks
WHERE asset_id = (SELECT asset_id FROM project_assets WHERE rel_path = 'artifacts/...')
ORDER BY seq;
```

按 `seq` 顺序拼接写入磁盘即可（`GROUP_CONCAT` 会因 `group_concat_max_len` 截断，不要用它校验大文件）。
