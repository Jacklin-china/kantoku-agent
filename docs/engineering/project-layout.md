# 项目目录与文件命名规范

- 适用范围：`kantoku-agent` 仓库全部内容，包括代码、文档、素材、运行时数据与本地工作区。
- 效力：本文件是 [AGENTS.md](../../AGENTS.md) 第三节「项目目录规则」的落地细则。**两者冲突时以 AGENTS.md 为准**；本文件负责把规则说清楚、把现状与差距列明。
- 维护要求：新增顶层目录、移动已有目录、或改变某类文件的存放位置，都必须先改本文件，再动文件。

---

## 1. 顶层布局

仓库根只放"项目自身"的东西。运行期产生的数据一律视为运行目录，不进版本库、不进交付压缩包。

顶层按**前后端对称**划分：后端一切归 `backend/`，前端一切归 `frontend/`，其余是文档、脚本与运行目录。

| 路径 | 职责 | 进 git |
|---|---|---|
| `backend/` | **后端全部内容**（源码、测试、配置、建库脚本、Skill、评测用例） | 是 |
| `frontend/` | **前端全部内容**（源码、测试、构建配置、静态资源） | 是 |
| `scripts/` | 仓库级运维与验证脚本（跨前后端，不属任何一侧） | 是 |
| `docs/` | 文档、设计资料、评价材料 | 部分（见 §4） |
| `assets/` | 演示素材；`reference/` 为大体积参考图 | 部分 |
| `data/` `artifacts/` `cache/` `logs/` `tmp/` | 运行时目录 | 否 |
| `.workbuddy/` | 本地 Agent 工作区 | 否 |

### 1.1 后端内部（`backend/`）

| 路径 | 职责 |
|---|---|
| `backend/src/kantoku/` | 后端全部源码（包名保持 `kantoku`，导入路径不变） |
| `backend/tests/` | 后端测试（77 个文件） |
| `backend/config/` | 配置模板；`settings.yaml` 为本地私有 |
| `backend/db/` | **SQLite** 建库脚本与迁移（运行时真源）；`backend/db/mysql/` 是 MySQL 只读镜像 DDL 与导入说明 |
| `backend/skills/` | Agent Skill 实现 |
| `backend/evals/` | 评测用例与结果 |

`pyproject.toml` 的 `packages`、`testpaths`、`[tool.ruff] src` 均已指向 `backend/`；`frontend/vite.config.ts` 的 `outDir` 指向 `backend/src/kantoku/shells/web`（静态产物是后端交付物的一部分，不留在前端目录）。

**前端一切归 `frontend/`，后端一切归 `backend/`，两侧根目录不再出现对方资产**；跨侧共享的只有顶层 `docs/`、`scripts/`、`assets/` 与运行目录。

**根目录不允许出现单个文件**（除 `README.md`、`LICENSE`、`AGENTS.md`、`CONTRIBUTING.md`、`pyproject.toml`、`uv.lock`、`.gitignore`、`.gitattributes`、`.env`、`start-studio.cmd` 这些必备项）。临时文件、个人资料、截图、一次性脚本都必须落到对应目录。

---

## 2. 后端分层：规范与现状

AGENTS.md 规定后端分四层：`core` / `domains` / `capabilities` / `providers`。下表路径均相对 `backend/src/kantoku/`。实际结构如下。

| 实际目录 | 内容 | 与规范的差距 |
|---|---|---|
| `core/` | `approval` `artifacts` `runtime` `skills` `state` `budget.py` `database.py` `llm.py` 等 | 一致。域无关的运行、工作流、产物、审批、状态抽象都在这里 |
| `domains/` | `comic` | 一致。`commerce` 已无代码模块，仅作为历史记录的 domain 字符串保留 |
| `capabilities/` | `image.py` `image_gen.py` `image_batch.py` `prompt_factory.py` `archive.py` `storyboard.py` `studio.py` `creative.py` `web_search.py` `video/` `vision/` | 一致（R3 已落地：原 `tools/` 的通用能力全部归入） |
| `providers/` | `contracts.py` `jimeng.py` `openai_image.py` `deepseek_balance.py` `volcengine_balance.py` `tencent_sms.py` | 一致（R1 改名、R2 移除空壳、R3 收进两个供应商 SDK） |
| `agent/` | `loop.py` `context.py` `tools_registry.py` | 一致（R6 已登记为「统一 Agent 循环」层） |
| `shells/` | `fastapi_studio.py` `studio.py` `cli.py` `conversation_router.py` `qc_cli.py` `image_cli.py` `web/` | 一致。表现层与传输层入口都在这 |
| `schemas/` | `media.py` `qc.py` `storyboard.py` | 与 `core/state` 存在模型定义重叠风险，新增类型前先查两处 |
| `memory/` `config/` `evaluation/` | 记忆人格、配置与运行时路径、结构化评测 | 一致 |

`tools/` **已不存在**。它的 8 个模块按职责拆进三层：`jimeng` / `openai_image` 是供应商协议实现 → `providers/`；`image_gen` / `image_batch` / `prompt_factory` / `archive` / `storyboard` / `studio` 是通用能力 → `capabilities/`。`providers/contracts.py` 承载 `ImageProvider` 协议与 `ProviderAccessResult`，让能力层与 Provider 都只向下依赖它，避免 Provider 反向依赖能力层。

### 命名约定（后端）

- 模块名一律 `snake_case`，一个模块一个职责；`image_gen.py` 与 `image_batch.py` 这种前缀相同的，说明它们同属一个能力，不要再拆第三个。
- 禁止 `xxx_new.py` / `xxx_old.py` / `xxx_backup.py` / `xxx_final.py` / `xxx_v2.py`。历史交给 Git。
- 测试文件名 `test_<被测模块>.py`，与 `backend/src/kantoku/<层>/<模块>.py` 同名，便于对应。
- 脚本放 `scripts/`，命名 `verify_*.py`（验证类）、`*_benchmark.py`（基准类）、`run_*.py`（启动类），不要出现 `test_*.py`（会与 pytest 收集冲突）。

---

## 3. 前端布局

```
frontend/
  src/
    components/
      approval/  artifacts/  brand/  chat/  comic/  conversation/
      layout/    ui/         workspace/
    domains/{comic,commerce}   前端领域逻辑（presentation / workbench）
    services/     接口调用
    views/        页面级组件
    styles/  assets/
  tests/          node:test 用例
  public/
```

`components/` **根目录不允许直接放 `.vue`**：一律按功能域或通用性归组。

- `comic/`：漫剧业务组件（`DirectorWorkspace` `DirectorNodeView` `ComicShotGeneration` `ComicFlowStage` `ImageOptimizeStage` `VideoStage` `WorkSettings` `ComicImageImport` `LegacyWorkImport`）
- `layout/`：外壳与路由级构件（`AuthGate` `AppConsole` `AppHud` `SidebarNav` `PixelSky` `CommandPalette` `HistoryDrawer`）
- `ui/`：无业务语义的通用件（`EmptyState` `StatusBadge` `UiDropdown` `ImageViewport`）
- `workspace/`：通用工作台骨架（`WorkspaceShell` `WorkspacePage` `WorkspaceStageNav` `WorkspaceInspectorDrawer`）与 `stageTypes.ts`
- `chat/` `artifacts/` `approval/` `conversation/` `brand/`：按消息流、产物渲染、审批卡片、会话时间线、品牌标识划分

约定：

- 组件按**功能域**分目录，与后端 `domains/` 同名（`comic` 对 `comic`，`commerce` 对 `commerce`）。
- 通用无业务组件放 `components/ui/`；带业务的放对应域目录，不要塞进 `ui/`。
- 接口调用只写 `services/`，组件里不直接写 `fetch`。
- 前端设计规范页 `frontend-design-spec.html` 已移至 [`docs/design/`](../design/)，代码注释中的 `design-spec §N` 指的就是它。

---

## 4. 文档与设计资料

| 目录 | 放什么 |
|---|---|
| `docs/engineering/` | 工程规则、运行存储规范、本文件 |
| `docs/architecture/` | 架构说明与例外清单 |
| `docs/product/` | 产品原则与需求 |
| `docs/design/` | 设计方案、原型、前端设计规范页 |
| `docs/design/kaiti/` | 开题报告相关物：正文源文件、功能模块图、生成脚本 |
| `docs/reports/` | 实验与评测报告 |
| `docs/evals/` | 评测口径 |
| `docs/images/` | 需要被文档引用的图片 |
| `docs/archive/` | 已失效的历史文档 |
| `docs/archive/personal/` | **与项目无关的个人资料**（简历、照片），不进 git |

规则：

- 一个文档只在一个地方。已移入 `archive/` 的文档不再更新，需要复用时先复制回 `design/` 再改。
- 图片若被文档引用，放 `docs/images/` 或用相对路径放在文档同级目录，不要散落到仓库根。
- 论文/开题报告类产物统一放 `docs/design/kaiti/`，配套的生成脚本一并放在那里，保证「图能重画」。

---

## 5. 运行时目录

这些目录由程序写入，**不进 git、不进交付包**，但也不在项目内长期堆积。

| 目录 | 现状体积（2026-11-22 清理后） | 说明 |
|---|---|---|
| `data/` | 约 108 M | 作品、资产、SQLite 库 |
| `artifacts/` | 约 1021 M | 生成产物快照（`comic-stories/` 占 917 M，是真实作品，**不清理**） |
| `cache/` | 0（可重建） | 工具缓存 uv / npm / vite，删了下次构建重下 |
| `logs/` | 约 0.5 M | 有 10MB 轮换 + 256MiB 配额 + 7 天 TTL |
| `tmp/` | 约 70 K | 暂存与临时会话，**不可整目录删** |

清理边界（与项目长期记忆一致）：

- `cache/`、`artifacts/` 可清，清了只是重算。
- `logs/` 必须走 `FileCatalog.remove_file(path, "log")`，**不能 `rm`**，否则台账留下幽灵行、配额统计失真。
- `tmp/` 是正式运行时目录，只清其子目录（如 `tmp/wb` 的浏览器 profile），不可整目录删除。
- `data/` 属用户数据，禁止删除、禁止清空（AGENTS.md 第九条）。
- `data/backups/` 已有上限（R7，2026-11-22）：`backup` 类型仍**不在** `EPHEMERAL` 里，不能按 TTL 清（过期时刻可能正好是唯一需要它的时刻）；改为 `cleanup()` 按份数保留最近的 `BACKUP_RETAIN`（默认 **3**）份，超出的旧快照直接删并计入 `released_bytes`。每次作品导入、方案恢复、认证迁移仍会整库拷贝（当前库约 96 M），但**不再无限累积**。需要长期留存的快照必须自己复制到 `data/` 之外，因为它迟早会被新快照挤掉。
- 手工删 `data/` 下任何已登记文件后，必须同步台账：把对应行 `status` 置为 `deleted`（`FileCatalog.mark_deleted(file_id)`），否则 `active` 但磁盘缺失的幽灵行会让体积核算与配额判断失真。

---

## 6. 本地工作区 `.workbuddy/`

`.workbuddy/` 是 Agent 的本地工作区，整体不进 git。目录职责固定如下：

| 子目录 | 放什么 | 生命周期 |
|---|---|---|
| `memory/` | 项目长期记忆与每日日志 | 永久，定期归档 |
| `prompts/` | 下一轮规划 | 永久 |
| `design/` | 原型 HTML 与设计截图 | 长期，可瘦身 |
| `scratch/` | **一次性中间文件**：导出的 JSON、临时 HTML、草稿脚本 | **任务结束即清空** |
| `verify-ui/` | UI 验证截图（证据） | 保留；其下浏览器 profile 目录（`chrome/`）随时可删 |

规则：

- 一次性中间文件**必须**落在 `scratch/`，不允许落在 `.workbuddy/` 根目录。
- 任务结束前清空 `scratch/`。判断标准：删掉之后能不能重新生成？能，就删。
- 需要留档的产物（如论文插图与其生成脚本）不留在 `.workbuddy/`，移到 `docs/` 对应目录。
- 浏览器 profile（Chromium/Playwright 的 user-data-dir）体积可达上百 MB，用完即删，不留在仓库里。

---

## 7. 文件命名总则

| 类型 | 规则 | 例 |
|---|---|---|
| Python 模块 | `snake_case.py` | `image_gen.py` |
| Python 类 | `PascalCase` | `ComicPromptStore` |
| 测试 | `test_<模块>.py` | `test_budget.py` |
| 脚本 | 动词开头，见 §2 | `verify_director_pipeline.py` |
| TS/Vue 组件 | `PascalCase.vue` | `DirectorWorkspace.vue` |
| 工具函数 | `camelCase.ts` | `artifactPresentation.ts` |
| 文档 | 小写连字符 `.md` | `project-layout.md` |
| 图片 | 小写连字符，语义在前 | `module-figure.png` |
| 日期型文件 | `YYYY-MM-DD` 前缀 | `2026-11-22.md` |

禁止：中文文件名（`风格方向对比.html` 属历史遗留，不再新增）、空格、版本后缀（`-v2`、`-final`、`-new`）、`temp`/`tmp`/`backup`/`old` 作为正式文件名。

---

## 8. 禁止清单

与 AGENTS.md 第四节一致，补充可执行判断：

- `__pycache__`、`*.pyc`、`.venv`、`node_modules`、`dist` 不进版本库（已由 `.gitignore` 覆盖）。
- `logs`、`outputs`、`generated`、`uploads`、`runtime`、`*.db`、`*.sqlite` 不进交付包。
- 仓库根不出现散落文件（见 §1）。
- 不用 `.gitignore` 掩盖放错位置的文件。**如果某个文件需要被忽略，先问它该不该在这个目录**；确认该留，再忽略。

---

## 9. 整改项登记（全部已落地）

以下差距曾确认存在，均属会动导入路径的改动，按单项执行并单独回归。**截至 2026-11-22 全部完成**，保留记录以便追溯原因与影响面。

| # | 事项 | 影响面 | 风险 |
|---|---|---|---|
| ~~R1~~ | ~~`adapters/` 更名为 `providers/`~~ **已于 2026-11-22 完成** | 全仓 import、测试、脚本 | 已执行：`git mv` + 全局替换 + `check_structure.py` 的分层禁令同步改为 `providers` |
| ~~R2~~ | ~~`providers/commerce/` 移出到 `domains/commerce/`~~ **已于 2026-11-22 完成** | 后端 | 已执行：核查发现 `providers/commerce/{marketplace,source}` 与 `domains/commerce/ports` **均为空目录、零代码、零导入**，直接移除；`commerce` 仅作为历史 Run/Artifact 的 domain 字符串保留 |
| ~~R3~~ | ~~`tools/` 拆分~~ **已于 2026-11-22 完成** | 后端 | 已执行：`jimeng` `openai_image` → `providers/`；`image_gen` `image_batch` `prompt_factory` `archive` `storyboard` `studio` → `capabilities/`；`ImageProvider` 协议与 `ProviderAccessResult` 抽到 `providers/contracts.py` 以避免 Provider 反向依赖能力层；`tools_registry` 按字符串名注册，未受影响 |
| ~~R4~~ | ~~`perception/` 归入 `capabilities/vision/`~~ **已于 2026-11-22 完成** | 后端 + 前端接口 | 已执行：5 个模块整体迁入 `capabilities/vision/`，13 处 `kantoku.perception` 改 `kantoku.capabilities.vision`，对外 HTTP 路径未变 |
| ~~R5~~ | ~~两个 evaluation 模块合成包~~ **已于 2026-11-22 完成** | 后端 | 已执行：`evaluation/__init__.py` + `evaluation/structured.py`，导入改为 `kantoku.evaluation.structured` |
| ~~R6~~ | ~~把 `agent/` 补进 AGENTS.md 第三节~~ **已于 2026-11-22 完成** | 文档 | 已执行：列为独立层并写明职责边界 |
| ~~R7~~ | ~~`backup` 类型纳入容量上限~~ **已于 2026-11-22 完成** | 后端 + 数据 | 已执行：`storage.BACKUP_RETAIN = 3`，`cleanup()` 按份数保留最近 3 份，超额旧快照删除并计入 `released_bytes`；新增回归 `test_backup_retention_keeps_only_newest_snapshots` |
| ~~R8~~ | ~~`frontend/src/components/` 根目录散落组件~~ **已于 2026-11-22 完成** | 前端 | 已执行：6 个组件按域归组（`comic/` 4 个、`layout/` 1 个、`ui/` 2 个），相对导入用解析器统一重算；删除零引用的 `ApprovalPanel` `QcPanel` `WorkflowProgress` 及其在 `style.css` 的 29 条失效规则（−3.2 KB）；测试按源码路径的断言同步改到新路径 |

R1–R8 **全部落地（2026-11-22）**。后续新增代码直接按新结构放，不要再出现 `tools/`、`perception/`、`adapters/` 这类历史目录。

---

## 10. 提交前检查

- [ ] 仓库根没有新增散落文件
- [ ] 一次性中间文件在 `.workbuddy/scratch/`，且已清空
- [ ] 新增图片有明确归属（`docs/images/` 或文档同级）
- [ ] 没有 `*_new` / `*_v2` / `*_final` 之类的平行文件
- [ ] `.gitignore` 没有为放错位置的文件新增例外
- [ ] 运行时目录体积没有异常增长（`data/` `artifacts/` `cache/`）
