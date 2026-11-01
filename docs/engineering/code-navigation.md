# 代码导航：从功能找到实现

本文是当前代码的入口索引，不替代 [AI 开发规则](AI_CODE_RULES.md)、[工程手册](README.md)或[架构例外清单](../architecture/exceptions.md)。修改前仍需核对调用方、实际实现和工作区状态。历史文档中的设计不一定等于当前行为。

## 从页面进入

| 要找什么 | 从哪里开始 | 往下看 |
| --- | --- | --- |
| 首页对话和快速创作 | `frontend/src/views/HomeView.vue` | `frontend/src/services/core.ts` 的流式会话请求；后端 `shells/conversation_router.py` 和 `shells/web_studio.py` |
| 漫剧作品聊天与导演方案 | `frontend/src/components/DirectorWorkspace.vue` 的 `sendInput`、`executeTaskTurn` | `services/core.ts` 的 `sendComicWorkTurn` → `shells/fastapi_studio.py` 的作品 turns 路由 → `web_studio.py` 的 `comic_work_turn` |
| 聊天气泡及制作步骤投影 | `frontend/src/domains/comic/directorPresentation.ts` | `buildDirectorChatTurns` 合并持久化消息和 Run；`directorProductionSteps` 根据现有事实生成显示状态 |
| 分镜与外部图片 Prompt | `frontend/src/domains/comic/shotGeneration.ts` | 后端 `domains/comic/storyboards.py`、`domains/comic/prompts.py`；旧命令行分镜工具具有不同契约 |
| 图片分析、改图指令和版本 | `frontend/src/components/comic/ImageOptimizeStage.vue` | `services/core.ts` 的 Comic 图片接口 → `domains/comic/image_review.py`、`media.py` |
| 故事编排与视频 | `frontend/src/domains/comic/storyWorkbench.ts`、`videoWorkbench.ts` | 后端 `domains/comic/stories.py`、`videos.py` 和 `capabilities/video/` |

页面负责交互和持有视图状态；后端接口与领域服务是持久化、版本及任务状态的依据。前端的异步结果需要检查请求所属作品和对话，不能在切换页面后覆盖新选择。

## 导演方案调用链

作品对话的创建请求在 `shells/web_studio.py` 的 `comic_work_turn`、`create_comic_director` 和 `_create_comic_director_mode` 中处理。该入口会结合会话上下文选择聊天、澄清、创建、修改或图片处理等分支；并非每条消息都运行导演阶段。

需要创建方案时，`ComicDirectorCoordinator.execute` 创建导演 Run，组织创意理解、视觉方向、摄影、评审和装配。前三个生成阶段通过注入的阶段执行器和共享文本出口调用模型；各阶段的 Skill manifest 声明契约及知识引用，`skills/comic/director/handler.py` 对协调器提供的结果做结构校验。评审和装配有各自的执行逻辑；实际调用次数受模式、复用、修订和降级影响。

`agent/loop.py` 是另一个有限步工具调用循环，有命令行调用方；不能据此假定网页对话全部经过这个循环。`core/runtime/graph.py` 是通用图运行时，导演协调器与它的历史职责重叠见 EX-09；不要为整理文件另建运行时。

## 状态、结果和排错

- 会话消息、Run、节点、事件和 Artifact：`core/runtime/store.py`、`core/runtime/models.py`；文件与 SQLite 生命周期分别经 `config/runtime_paths.py` 和 `core/database.py` 管理。
- 上下文及历史读取：`core/conversation_context.py`；导演的作品、资产和版本在 `domains/comic/projects.py`、`assets.py` 等领域服务中。
- 图片预算和请求恢复：`core/budget.py`、`tools/image_gen.py`、`capabilities/image.py`。超时不等于外部任务失败；排错时先查原请求和账本状态。
- 日志和错误：`config/observability.py`、`core/tracing.py`。不同入口的关联字段并不完全一致；Trace 缺失不能证明操作没有执行。
- 测试入口：后端 `tests/`，前端 `frontend/tests/`；导演工作区可先看 `director-workspace.test.mjs`、`comic-shot-generation.test.mjs`，会话和导演后端可先看 `test_conversation_context.py` 与 `test_comic_director_coordinator.py`。

## 后续拆分时的检查顺序

1. 确定现有文件属于 UI、Shell、Domain、Capability 还是 Core，并搜索实际调用方、动态加载与测试。
2. 先抽离边界清楚的纯展示或领域逻辑，复用已有模块；保存公共接口及原有错误、状态、费用和版本语义。
3. 针对消息顺序、请求归属、旧数据与失败恢复运行行为测试；前端改动另做类型检查和隔离构建。
4. 对 `web_studio.py`、导演协调器、Prompt 和 Core 的已登记例外，先按例外清单准备调用方及兼容方案，不能只因文件过长而整体搬迁。
