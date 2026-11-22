# Kantoku · AI 漫剧副导演

本机单用户的 AI 漫剧创作工作台：**创意 → 导演方案 → 故事与分镜 → 图片生成 / 外部改图 → 缺陷诊断与修改 → 版本比较 → 导出**，全过程由统一 Agent Runtime 编排，可中断、可恢复、可追溯。

技术栈：Python 3.11–3.12 · FastAPI · SQLite（WAL） · Vue 3 + Vite + TypeScript。

> 当前阶段：**能力较多，需要收口**。导演、故事、资产、版本、恢复、账号已闭环；局部改图的"执行"环节仍在外部工具完成，是本阶段 P0 缺口。详见[实施计划书](docs/design/next-stage-design.md)与[目录规则](AGENTS.md)。

---

## 现在能做什么

| 能力 | 状态 | 入口 |
|---|---|---|
| 创意 Brief → 导演方案（草案 / 确认 / 编辑 / 版本） | 已具备 | 创作会话、导演工作台 |
| 故事编排与镜头控制（镜头、动作、运镜、时长） | 已具备 | 故事编排、故事校验 |
| 外部资产导入（校验、来源、哈希、作品关联） | 已具备 | 资产库 |
| 图片版本树（父子、分支保护、Prompt 快照、比较） | 已具备 | 图片工作台 |
| 缺陷诊断 → 坐标定位 → 框选 → 改图指令 | 已具备 | 图片报告 |
| **改图指令 → 真实执行 → 新版本落库** | **部分具备**（主流程仍需外部工具改图） | — |
| HITL 中断与恢复（Run / Checkpoint / 审批） | 已具备 | 任意长任务 |
| 本机单账号登录 | 已具备 | 工作台首页 |

**明确不做**（计划书 §1.3）：多用户与权限、支付订阅、分享社区、微服务 / K8s / Kafka / Redis、用 LangGraph 替换现有显式状态机、自研扩散模型。

---

## 快速开始

需要 Python 3.11–3.12、[uv](https://docs.astral.sh/uv/)、Node.js 20.19+ 或 22.12+（Vite 7 要求）。

首次运行前复制配置模板（已存在则保留，不要覆盖密钥）：

```bash
cp backend/config/settings.example.yaml backend/config/settings.yaml
cp backend/config/.env.example .env
```

没有供应商密钥也能启动，但依赖模型的功能不可用；真实调用会产生费用。

```bash
# 后端：API + SSE + 工作台静态页
uv sync
uv run python -m kantoku serve                 # http://127.0.0.1:8000
uv run python -m kantoku serve --no-browser --port 8000
uv run python -m kantoku doctor                # 本机配置自检（不联网）
uv run python -m kantoku doctor --live         # 附加供应商连通性探测

# 前端开发服务器
cd frontend
npm install
npm run dev                                    # http://127.0.0.1:5173
```

- PyCharm 打开项目根目录，解释器指向 `.venv`，运行共享配置 `Kantoku Backend`。
- VS Code 单独打开 `frontend/`，`npm run dev`；默认连 `http://127.0.0.1:8000`，需同时启动后端。
- `http://127.0.0.1:8000/docs` 是 FastAPI 自带的接口文档页。
- `frontend/src/` 是唯一前端源码；`backend/src/kantoku/shells/web/` 是它的构建产物，**不要手改**。

`backend/config/settings.yaml` 与 `.env` 已忽略。运行期数据 `data/` `logs/` `tmp/` `cache/` `artifacts/` 全部忽略，不进版本库；这些目录可用 `KANTOKU_RUNTIME_ROOT` 整体外置到项目之外。

### 本机账号

首次打开工作台可创建用户名和密码。只支持**一个本机账号**，不提供手机号 / 微信 / 邮箱登录，也不做多人数据隔离。用户名 3–32 位（中文、字母、数字、下划线、短横线），密码 8–128 位。登录状态最长保留 30 天，正常重启后端不要求重新登录；**没有找回密码功能**。服务器只监听回环地址，不是公网方案。

---

## 架构

```
backend/                 后端全部内容（源码 + 测试 + 配置 + 建库 + Skill + 评测）
  src/kantoku/
    shells/              接口层：FastAPI 路由、请求校验、错误映射
    domains/             业务：comic（漫剧全流程）、commerce（电商旧域）
    capabilities/        通用 AI 能力：image / video / text / vision（视觉质检）/
                         生图编排（image_gen, image_batch）、Prompt 组装、归档、分镜、创作任务
    providers/           外部协议适配：contracts（ImageProvider 协议）/ jimeng / openai_image /
                         deepseek / tencent_sms / volcengine
    core/                领域无关骨架：Run / Workflow / Node / Approval / Artifact /
                         Memory / Runtime Event / Checkpoint / 数据库与文件生命周期
    config/              配置与运行时路径
    agent/               统一 Agent 循环（loop / context / tools_registry）
    schemas/ memory/     数据模型与记忆人格
  tests/ config/ db/ skills/ evals/       测试、配置、建库、Skill、评测

frontend/                Vue 3 单页应用（只经 HTTP 调后端）
     │ HTTP / SSE
backend/src/kantoku/shells/   ↑ 前端只调这一层
```

分层依赖禁令由 `scripts/check_structure.py` 机械校验：`config` 不依赖上层，`core` 不碰 `providers`/`domains`/`shells`，`domains` 不碰 `providers`/`shells`。`core/` 出现业务词即判失败。

**关键入口**

| 位置 | 作用 |
|---|---|
| [agent/loop.py](backend/src/kantoku/agent/loop.py) | Agent 工具调用循环 |
| [core/runtime/graph.py](backend/src/kantoku/core/runtime/graph.py) | 工作流节点、执行状态与恢复 |
| [domains/comic/coordinator.py](backend/src/kantoku/domains/comic/coordinator.py) | 导演阶段编排与业务门禁 |
| [domains/comic/prompts.py](backend/src/kantoku/domains/comic/prompts.py) | 提示词编译与约束核验 |
| [shells/fastapi_studio.py](backend/src/kantoku/shells/fastapi_studio.py) | Web API |
| [frontend/src/](frontend/src/) | 工作台交互 |

---

## 目录导航

| 目录 | 内容 |
|---|---|
| `backend/src/kantoku/` | 后端源码（唯一） |
| `backend/tests/` | 后端测试 |
| `backend/config/` | 配置模板与运行时路径 |
| `backend/db/` | SQLite schema 与迁移（运行时真源）；`mysql/` 是只读镜像 DDL |
| `backend/skills/` | 领域 Skill 定义（运行时加载） |
| `backend/evals/` | 离线评测用例（`.jsonl` / `.json`） |
| `frontend/` | 前端源码（唯一），`src/` 下按 `components` `views` `domains` `services` `styles` 分类 |
| `scripts/` | 验收、基准、结构检查、MySQL 镜像 DDL 生成 |
| `backend/db/mysql/` | MySQL 镜像库 DDL 与导入说明（只读镜像，不参与运行） |
| `assets/` | 示例素材（已入库，README 引用） |
| `docs/` | 文档，入口见 [docs/README.md](docs/README.md) |

---

## 验证

```bash
uv run pytest                                     # 后端离线测试
PYTHONPATH= .venv/Scripts/python.exe scripts/check_structure.py   # 结构 + 分层 + 垃圾文件
uv run ruff check backend/src backend/tests scripts   # 规范
cd frontend && npm test && npx vue-tsc --noEmit   # 前端交互测试 + 类型检查
```

离线测试用隔离数据与替身，不调用付费模型。前端测试是交互回归基线，**不能替代**关键浏览器路径验收。

---

## 已知边界（不要当成已完成）

- **局部改图**：诊断、坐标、框选、指令合成、外部结果导入、版本比较都已实现；但"内部自动执行改图"尚未接入主流程，框选也没有进入供应商真实的 mask / region 参数。这是 P0 缺口，报告里不能写成"已实现自动改图"。
- **视频、参考图**：非核心场景，单独统计，不混入改图通过率。
- **评测**：`backend/evals/` 有模板与历史结果，当前版本的重绘前后对比与效率对照**尚无当前结果**，结论允许是"不提升"。
- **改图以外的真实供应商验收**：需要显式触发并计费，未配置的 Provider 标为"未测试"。

---

## 文档

| 想知道 | 读 |
|---|---|
| 能不能改、怎么改、改完怎么验 | [AGENTS.md](AGENTS.md) |
| 工程边界与分层细则 | [docs/engineering/](docs/engineering/) |
| 架构决策与例外 | [docs/architecture/](docs/architecture/) |
| 产品取舍 | [docs/product/product-principles.md](docs/product/product-principles.md) |
| 历史验收与评测原始数据 | [docs/enterprise-validation.md](docs/enterprise-validation.md) · [docs/evals/](docs/evals/) |
| 目录放哪里、怎么命名 | [docs/engineering/project-layout.md](docs/engineering/project-layout.md) |

## License

见 [LICENSE](LICENSE)。
