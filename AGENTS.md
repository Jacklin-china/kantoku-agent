# AGENTS.md · Kantoku 工程规则

> 本文件是**唯一的** AI/人类协作入口规则。任何改动代码的会话，先读完本文件再动手。
> 依据：《Kantoku 后端完善与产品成熟化实施计划书》（2026-11-22）与当前源码实况。
> 与旧版差异：不再按"十大条注意事项"罗列，改为**可执行的边界 + 可验证的闸门**。

---

## 1. 项目定位（先明确"不做什么"）

Kantoku 是**本机单用户**的 AI 漫剧创作工作台：创意 → 导演方案 → 故事与分镜 → 图片生成/外部改图 → 诊断修改 → 版本比较 → 导出。

技术栈固定为 **Python 3.11–3.12 + FastAPI + SQLite（WAL）+ Vue 3 + Vite + TypeScript**。

计划书第 1.3 节明确排除，**不要主动引入**：

| 排除项 | 原因 |
|---|---|
| 多用户、角色权限、组织空间、账号封禁后台 | 当前是单账号本地体系，扩这一层不服务任务书闭环 |
| 支付订阅、公开分享社区、协作评论、插件市场 | 同上 |
| 微服务、Kubernetes、Kafka、Redis | 单机单体，加中间件只增风险 |
| 把显式状态机换成 LangGraph | 现有 Runtime 已有 Run/Checkpoint/审批/恢复，换框架属重写 |
| 自研扩散模型训练、像素级缺陷真值标注 | 不在任务书范围 |

需要判断某个新需求该不该做时，先问：**它是否让"创意到导出"这条主旅程更可证明、更可恢复、更可交付？** 不是就砍。

---

## 2. 改动前的固定顺序

1. 读本文件
2. 读 [AI 编码规则](docs/engineering/AI_CODE_RULES.md)
3. 读 [产品原则](docs/product/product-principles.md)
4. 读 [工程手册](docs/engineering/README.md) 与 [运行存储规范](docs/engineering/runtime-policy.md)
5. 涉历史边界时读 [架构例外清单](docs/architecture/exceptions.md)——**例外只允许兼容维护，不得作为新增违规代码的依据**
6. 读 [目录细则](docs/engineering/project-layout.md)

然后回答三个问题，答不上来就不要写代码：

- 这个功能**已经存在**吗？（先搜 Service / 路由 / 组件 / 测试）
- 它属于哪一层？（前端 UI / 后端接口 / Core Runtime / Domain 业务 / Capability / Provider）
- 改完之后，**哪条测试**能证明它没坏？

---

## 3. 架构分层与依赖方向

```
frontend/  Vue 3（只通过 HTTP 调后端，不复制后端业务规则）
    │  HTTP / JSON
backend/src/kantoku/
shells/    接口层：FastAPI 路由、请求校验、错误映射（web_studio / fastapi_studio）
    │
    ├── domains/       业务：comic（漫剧全流程）；commerce 仅存历史记录的 domain 字符串
    ├── capabilities/  通用 AI 能力：image / video / text / vision（视觉质检）/
    │                  生图编排（image_gen, image_batch）与 Prompt 组装、归档、分镜、创作任务
    ├── providers/     外部协议适配：contracts（ImageProvider 协议）/ jimeng / openai_image /
    │                  deepseek / tencent_sms / volcengine
    ├── core/          领域无关骨架：Run / Workflow / Node / Approval / Artifact / Memory /
    │                  Runtime Event / Checkpoint / 数据库与文件生命周期
    ├── agent/         统一 Agent 循环：loop / context / tools_registry（唯一执行体）
    ├── config/        配置与运行时路径（runtime_paths）
    └── schemas/ / memory/  数据模型与记忆人格
```

`agent/` 是「统一 Agent」的落点：它只做意图、上下文装配与有限步工具调用，不持有领域规则；业务分支上抛给 `domains/`，通用能力下沉到 `capabilities/`。

**硬性依赖禁令**（由 `scripts/check_structure.py` 机械校验，改不动就说明你放错了层）：

| 层 | 禁止导入 |
|---|---|
| `config` | `core` `capabilities` `providers` `domains` `shells` `tools` |
| `core` | `providers` `domains` `shells` |
| `domains` | `providers` `shells` |

`core/` 里出现业务词（漫剧、分镜、商品、SKU、Ozon 等）即视为污染，结构检查会失败。

**只有一个 Agent。** 禁止创建 Comic Agent / Commerce Agent / Video Agent 之类的平行执行体；业务差异用 Domain + Skill + Capability 表达，共用同一个 Runtime、同一套日志、同一套 Run 身份。

---

## 4. 目录与命名

- 顶层只允许：`backend` `frontend` `docs` `scripts` `assets`（其余运行目录由 `RuntimePaths` 在运行时创建，不进 git）。
- **前后端对称**：后端一切进 `backend/`（`src` `tests` `config` `db` `skills` `evals`），前端一切进 `frontend/`；两侧不互相存放对方资产，详见 [目录细则](docs/engineering/project-layout.md) §1。
- 文档：`kebab-case.md`，不加 `01-` 这类序号前缀；长篇设计进 `docs/architecture/`，长期约束进 `docs/engineering/`，一次性记录进 `docs/evals/`，过时材料进 `docs/archive/`。
- **禁止平行实现**：`*_new` `*_old` `*_backup` `*_final` `*_v2` 一律不允许存在；历史交给 Git。
- **禁止用 `.gitignore` 掩盖放错位置的文件**。先判断"它该不该在这里"，再决定是否忽略。
- 后端分层固定为 `core` / `domains` / `capabilities` / `providers`（+ `agent` / `shells` / `schemas` / `memory` / `config` / `evaluation`）。**不再存在 `tools/` `perception/` `adapters/`**：供应商实现进 `providers/`，通用能力进 `capabilities/`。
- 前端 `components/` 根目录不放 `.vue`，一律按域归组（`comic` `chat` `layout` `ui` `workspace` `artifacts` `approval` `conversation` `brand`）。
- 历史整改已全部完成（`docs/engineering/project-layout.md` §9 的 R1–R8）：不要再照旧路径新建文件，也不要重新引入被移除的目录。

---

## 5. 数据与运行时

- 数据库唯一入口是 `core/database.py` 的 `DatabaseManager`；文件生命周期唯一入口是 `core/runtime/storage.py` 的 `FileCatalog`。**`scripts/` 下的原始 `sqlite3` 只允许出现在 `check_structure.py` 的白名单里。**
- 运行时目录由 `RuntimePaths` 管理，可用 `KANTOKU_RUNTIME_ROOT` 整体外置；`tmp/` 不可整目录删，`logs/` 删除必须走 `FileCatalog.remove_file(path, "log")`。
- **禁止**删除用户数据、清空数据库、覆盖历史记录。任何破坏性迁移必须：先备份 → 有回滚路径 → 逐条核对。
- 台账（`runtime_files`）与磁盘必须一致：手工删了已登记文件，就要把对应行置为 `deleted`，否则体积核算与配额判断会失真。
- 绝不把 `unknown` 计费当 0，绝不伪造 Critic 通过，绝不用默认值填充导演结果。

---

## 6. 验证闸门（改完必须全跑）

```bash
# 后端
PYTHONPATH= .venv/Scripts/python.exe -m pytest tests -q --no-header
# 结构 + 分层
PYTHONPATH= .venv/Scripts/python.exe scripts/check_structure.py
# 规范
.venv/Scripts/python.exe -m ruff check src tests scripts
# 前端
cd frontend && npm test && npx vue-tsc --noEmit
```

- `check_structure.py` 必须是 `PASS`；`pytest` 的已知既有失败单独列出，不得以"历史就是这样"为由新增失败。
- 前端测试是交互回归基线，**不能替代**关键浏览器路径验收。
- 真实付费调用由用户显式触发；未配置的 Provider 标为"未测试"，不是"通过"或"失败"。UNKNOWN 状态先查原 `request_id`，**不得自动重试**。

---

## 7. 交付优先级（来自计划书，按此排序）

| 级别 | 工作包 | 含义 |
|---|---|---|
| P0 | B01 | 局部改图执行闭环（诊断 → 框选 → 指令 → **执行** → 新版本落库） |
| P0 | B02 | 离线回归 + 真实 HTTP + 关键页面端到端与恢复证据 |
| P0 | B03 | 版本、事务、文件失败与分支保护证据 |
| P0 | B04 | 效果与效率评估，产出真实样本，**结论允许是不提升** |
| P1 | B05 | lint、类型、局部模块整理、文档与图表同步（**不大规模重写**） |
| P1 | P01–P03 | 首次配置检查、失败恢复入口、作品交付中心 |
| P1 | P04–P05 | 本机 ZIP 备份、本地示例与短帮助 |
| P2 | P06 | 版本树可视化（可直接裁减） |

发布闸门：G0 基线问题明确 + Provider 编辑能力确定 → G1 B01/B03 通过 → G2 真实端到端与评测齐全 → G3 单用户主旅程可用。**G0 之前不写编辑业务代码，G2 之前不投 P06。**

---

## 8. 提交前清单

```
□ 没有 *_new / *_old / *_backup / *_final 文件
□ 没有新增无用代码、没有新增垃圾文件（含临时脚本、截图、调试 JSON）
□ 没有新增依赖（新增必须回答：为什么、现有能力为何不够、删掉会怎样）
□ check_structure.py = PASS
□ pytest 无新增失败
□ npm test 与 vue-tsc 通过
□ 后端 `python -m kantoku serve` 能起来，前端 `npm run dev` 能起来
□ 文档与代码一致（路径、模块名、表名、命令）
```

不确定的时候，选**风险最低**的方案：改现有文件 > 优化现有结构 > 新增文件。大规模重写永远是最后选项，且必须单独立项。
