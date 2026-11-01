# 工程边界与维护手册

根目录 `AGENTS.md` 是 AI 改动入口。本文件只记录实施细节；产品里程碑与配置约束仍见 `docs/01`–`04`。

查找当前页面、导演流程与核心服务的代码入口，可先看 [代码导航](code-navigation.md)。

## 后续 AI 修改的必读入口（治理 Module 0，2026-10-05）

每次修改前依次阅读：根目录 [AGENTS.md](../../AGENTS.md) → [AI_CODE_RULES.md](AI_CODE_RULES.md) → [product-principles.md](../product/product-principles.md) → 本手册；核对[架构例外清单](../architecture/exceptions.md)及当前实现，并阅读[运行存储规范](runtime-policy.md)。旧章节是阶段记录，不得据此复制旧架构例外或改动用户冻结的流程。

当前 Comic 新工作区以已验收 Module 2 为准：DirectorSpec → 原 PromptCompiler → Core Prompt Artifact → 外部复制/上传。导演上下文与审核状态的修正见[上下文锁与审核状态](../architecture/comic-context-continuity-feedback.md)，当前独立方案契约见下方 Conversation Workspace：首次生成立即保存六类设定，新创作消息生成独立方案；FIRST_GENERATION/NEW_CONTEXT 使用内部验证，只有主动修改方案的 USER_EDIT 使用 Context Diff 与公开 Director Review。规范阅读入口已接入；跨层检查和 Trace 统一仍待独立模块，不表示 CI 已覆盖全部规则。

危险维护脚本的引用、动态加载、启动和测试审计见 [Module 0 风险报告](../architecture/governance-module-0.md)。原 `_tail_errors.py` 已原样隔离为 `scripts/archive/_tail_errors.py.disabled`，禁止执行、导入、恢复后缀或自动扫描归档；没有运行对账或修改任何业务账单。

## 架构边界与改动顺序

改动前先读产品原则、本文件和实际代码，再确认问题属于哪一层。目标调用方向是 **Conversation → Unified Agent Orchestrator → Action / Plan → Direct Capability 或 Domain Workflow → Core Infrastructure → Provider**。这是收口方向，不代表当前已有独立的统一编排模块；禁止为了满足图示另起第二套运行时。

- Conversation 只管理会话、消息、持久化和实时事件；统一编排只做意图、上下文、交互策略和执行路径决策。不要把首页、Comic、Commerce 的判断继续堆进 `stream_conversation`，也不要为每个入口各建 Router / Planner。
- 会话原有 `interaction_mode=autonomous/guided` 是持久化兼容字段；对产品与客户端呈现为 `execution_mode=fast/professional`。同一个 `conversation_router` 决定 Action、Domain 与执行模式；首页复杂请求可推断 Domain，但不会因此跳入专业页面。不要另存一份互相竞争的模式状态。
- ImageService、VideoService 和 Prompt Enhancer 应是各自唯一的共享能力入口；Autonomous / Guided 差异由 mode、domain、policy 和 action 表达。简单请求直达共享能力，复杂请求才启动领域 Workflow，两者共用预算、Artifact、日志与幂等机制。
- Core 不理解领域词或业务流程；Domain 只定义专业 Schema、Policy、Workflow 和 Prompt，不复制 Core。Provider 只实现供应商协议，不决定用户交互、审批或业务流程。前端只调用应用 API，不直接访问 Provider。
- 禁止伪造 Provider 状态、进度与结果。修 Bug 补回归测试；先收口已有实现，再删除被替代代码，历史由 Git 保存。不得创建 `_new`、`_old`、`_backup`、`_final`、`_v2` 副本。

## 生图模型与协议配置

`image.model` 只从 `config/settings.yaml` 读取，Provider 不维护模型白名单，
也不按模型名猜测调用协议。`image.protocol` 声明协议：

- `openai-compatible`（旧配置缺省值）：POST `{base_url}/images/generations`。
- `dashscope-multimodal`：`base_url` 为 HTTPS `/api/v1` 根地址，
  POST `{base_url}/services/aigc/multimodal-generation/generation`，使用 messages/content。

同一协议下可配置 `qwen-image-3.0`、`qwen-image-2.0-pro` 或后续兼容模型，重启后生效。
端点、模型、密钥必须与供应商的实际服务匹配；通过配置校验不代表供应商权限已经验证。
`api_key_env` 可指定任意已配置的密钥变量名，不能在 YAML 存明文 Secret。
Alibaba 适配器只校验模型非空、端点/协议、认证配置及 Provider 身份；图片尺寸、格式与
单图生产安全限制在付费提交前校验。未知协议、无密钥和非法地址在提交前拒绝。
协议切换纳入请求身份；旧 OpenAI-compatible 指纹保持兼容。
两种协议复用原单次提交、结果保存、查询恢复与日志链路；错误不自动切换协议、不重提生成。

协议依据：[阿里云 Qwen Image API](https://www.alibabacloud.com/help/en/model-studio/qwen-image-api)。
离线协议测试不等同于真实生图验收，真实调用另需预算授权。

## 本轮架构审计（2026-09-25）

- `shells/web_studio.py` 的 `stream_conversation` 同时处理意图、首页生图/恢复、预算策略、Guided 任务构造及聊天流，职责明显过重。现有 `core/conversations.py::IntentPlanner`、`capabilities/creative.py::plan_creative_turn` 与 `shells/conversation_router.py::route_conversation` 是相连但分散的决策步骤；未来应在现有调用链中收口为一个编排入口，而非再加一套 Planner。
- `core/conversations.py::IntentPlanner` 包含 Ozon、SKU、Comic、Commerce 领域词和分支，与 Core 不理解领域的原则冲突。迁移时保留现有路由行为与回归测试，将领域分类知识移到编排/领域策略；本轮不搬迁。
- 首页图片由 `capabilities/image.py::ConversationImageService` 复用 `tools/image_gen.py::gen_image` 的预算/供应商提交，Artifact 落在共享 RuntimeStore；Comic 使用同一 `ImageProvider`/`gen_image` 底层，Commerce 通过 `ProductImageCapability` 协议并以现有适配器实现。`VideoService` 被 Comic Workflow 引用。没有证据表明这些应整套重写，但图片领域入口尚未完全收成单一 Service API。
- `shells/web_studio.py::_enhance_prompt` 与 `capabilities/creative.py::compile_image_prompt` 分别服务不同路径，是提示词处理分散的收口候选；不能仅凭同名或 IDE unused 删除。`src/kantoku/core/` 未发现直接导入 `domains/` 或 `adapters/`，但上述领域词属于语义泄漏。
- 对源码与测试文件名的 `_new/_old/_backup/_final/_v2` 审计未发现可据此删除的副本。`src/kantoku/shells/web/` 是前端构建输出，不是第二份源码；不得当废弃代码删除。清理任何动态加载、Skill、路由或 Schema 前仍须逐项核对引用。

## 漫剧制作台审计与演进边界（2026-09-27）

- 当前 `domains/comic/workflow.py` 的真实闭环是**单镜头任务准备 → 费用确认 → 生图 → VLM QC → 人工审核/返工 → 归档 → 可选视频**。`prepare` 只创建可恢复的 StudioTask，不生成导演分析或结构化分镜；前端不得把它标作已完成的「导演规划」。
- `tools/storyboard.py` 已能生成并持久化 20 镜分镜，`tools/prompt_factory.py` 已提供镜头与 Persona 的可追溯配方，`memory/` 保存角色记忆。这些能力目前未由上述 Comic Workflow 统一编排；不得在 UI 中伪装已有 Character/Scene/Style Bible 或已锁定资产。
- 制作台复用 Run、NodeExecution、Artifact、QC State 和原有专业对话。默认让 AI 导演对话占满主区；有真实 Run 后，才出现可拖动的对话/结果预览分区及顶部轻量阶段。左侧仅留资产图标入口，真实产物按需展开；右侧 Inspector 默认隐藏，只呈现当前阶段的真实信息，Prompt、模型、Seed、QC 与成本渐进展开。没有 Run 时不出现空镜头、假资产或假进度。Commerce/Studio 沿用原页面。
- 后续导演层应在 `domains/comic/` 中定义可持久化的 Creative Brief / Director Spec / Asset Bible / Storyboard，并区分用户硬约束、软偏好与可发挥空间；用 Stable Context + Relevant Memory + Current Task 编译不同模型的 Prompt。复用现有 Capability、Budget、Artifact、Provider、审批和 Runtime，不增加第二套生图/视频或独立聊天系统。QC 应区分通过、小修和重做，实际支持局部修复前不得在界面声称已有该能力。

交互参考：[Runway Agent 的对话式创作](https://help.runwayml.com/hc/en-us/articles/51601639579667-Creating-with-Runway-Agent)、[Figma 的可收起侧栏](https://help.figma.com/hc/en-us/articles/360039831974-Explore-the-navigation-bar-and-left-sidebar)、[Figma 的上下文属性面板](https://help.figma.com/hc/en-us/articles/360039832014-Design-prototype-and-explore-layer-properties-in-the-right-sidebar)、[InvokeAI 的 Gallery/Canvas](https://invoke.ai/features/gallery/)。拖动分区继续使用项目已有 `splitpanes`，简单高级信息使用原生折叠，不为同一用途重复引入组件库。

## 漫剧图片生产：Phase 1 架构契约与迁移计划（2026-09-27）

本阶段只完成审计、数据契约和迁移设计，**不把设计视为已上线能力**。交付目标限于图片生产；现有可选 Video Node 与 Mock Provider 保留兼容，但新作品流程不自动接入图生视频、时间轴或视频编辑。未来视频仍须复用共享 Video Capability，不把视频状态预塞进图片专用表。

### 现状与处置

| 现有模块 | 证据与边界 | 处置 |
| --- | --- | --- |
| `domains/comic/workflow.py`、`models.py` | `comic.production.v1` 是单镜头准备、费用审批、生图、QC、人工审核、返工、归档的固定图；`ComicState` 不是作品模型。 | **保留兼容**现有 Run/Checkpoint；未来在 Comic Domain 按任务类型组合领域 Workflow，不把旧图扩充成所有作品必经的巨型图。 |
| `domains/comic/services.py` 与 `tools/studio.py` | 已复用 `gen_image` 的预算、幂等和供应商查询，但服务直接持有 `ImageProvider`，StudioTask 另存 JSON 文件，按项目名与镜号扫描防重。 | **渐进重构**为共享 Image Capability 的领域调用端口；旧 request ID、账单与恢复路径保持有效，不能为了收口重新提交旧任务。 |
| `tools/storyboard.py`、`schemas/storyboard.py` | 有严格镜号校验和实际持久化；生成固定 20 镜，`save_storyboard` 按 episode 替换旧镜头。 | **复用校验，迁移存储**到作品级、可变镜数和追加版本；旧 CLI/Agent 调用保持兼容直到验证迁移。 |
| `memory/persona.py`、`tools/prompt_factory.py`、`agent/context.py` | 已有角色结构、Prompt 配方及最近镜头窗口；Persona 以名字全局覆盖，Recipe 以 episode/镜号定位，现有质量词是固定文本。 | **复用约束、溯源和上下文裁剪**；改为项目范围的资产版本与按模型编译，不再用全局角色名或固定质量词代替导演分析。 |
| `perception/qc.py`、`perception/review.py`、`tools/archive.py` | 已有视觉预筛、不可变人工审核、返工队列与归档；当前返工是新生成任务，不是已实现的局部编辑。 | **保留并扩展领域判定**；只有接入可验证的局部修改能力后，`MINOR_ERROR` 才能自动修补。 |
| `core/runtime/`、`core/budget.py`、Artifact、Approval、Image/Video Capability | 通用 Run、Node、Checkpoint、事件、预算台账和 Artifact 已共享；预算当前仍要求 project/episode/shot_no，属于待收口的通用身份约束。 | **复用而不复制**；预算身份泛化需另做兼容迁移，不在 Comic Domain 另建账本。 |
| `shells/web_studio.py` 的旧 Studio 操作与 Core Run API、Comic Skill manifest | 均有路由、动态加载或测试引用。 | **暂不删除**；替换后先验证旧入口、Skill、恢复和历史数据，再移除确实重复的领域适配代码。 |

目前**没有证据支持立即删除任何业务文件或历史表**。`db/schema.sql` 的 `shot`、`persona`、`recipe` 和 StudioTask 文件均视为历史业务记录，绝不靠清空或覆盖完成迁移。

### 作品级数据契约

稳定的 `project_id` 是作品身份；项目名称只用于展示，不能作为去重、预算或关联键。以下对象属于 `domains/comic/`，不是 Core 通用模型。所有可编辑对象使用稳定 `entity_id` 与从 1 开始递增的修订号；编辑追加新修订，不覆盖旧内容。删除分镜或资产写入墓碑修订，历史版本仍可恢复。

| 对象 | 领域数据与关系 | 版本边界 |
| --- | --- | --- |
| `CreativeProject` | `project_id`、标题、作品状态、创建/更新时间；关联 Conversation、Run 的 ID，不复制其记录。 | 项目元数据可更新；创作内容独立修订。 |
| `CreativeBrief` | `original_request`、`hard_constraints`、`soft_preferences`、`creative_freedom`，保留用户原始表述；模型推断与用户明示应标明来源。 | 每次创作理解或人工修改产生新修订；硬约束不能被导演或 Prompt 编译静默改写。 |
| `DirectorSpec` | 关联 Brief 修订，记录 `visual_direction`、`mood`、`color_language`、`lighting`、`camera_language`、`composition`，并说明与故事、角色、场景及镜头目的的关系。 | 动态分析，不建立“情绪词 → 固定摄影公式”；引用的 Brief 修订必须明确。 |
| `CharacterAsset`、`SceneAsset`、`StyleBible` | 项目范围内的角色外观/服装/特征、环境/时间/天气/光线、风格/色彩/材质/摄影语言；参考图片只存 Core `artifact_id`。 | 各资产独立修订，可锁定某一修订供多个镜头复用；不可通过重名覆盖其他项目。 |
| `Storyboard`、`Shot` | Storyboard 保存有序 Shot ID 列表，镜头数由需求决定；Shot 保存 `shot_id`、目的、主体、动作、环境、构图、摄影、引用的资产 ID 与修订、状态。顺序号仅用于展示，不作为镜头身份。 | 分镜和单镜头分别追加修订；重排、删除或返工不改变稳定 Shot ID。 |
| `PromptArtifact` | 使用 Core `ArtifactType.PROMPT`，在 metadata 记录所用 Brief/Director/Asset/Shot 修订、编译器版本、模型标识、Prompt 哈希及来源 Run/Node；`location` 指向持久化的 Prompt 正文。 | 新 Prompt 创建新 Artifact/版本，不覆写已提交生图任务的输入。 |
| `GenerationTask`、`QualityReport`、最终 `Artifact` | 生成沿用 Core Run/Node、StudioTask 或其兼容替代、`generation_request_id`、预算台账和 Image Capability；QC 沿用现有预测/人工审核并关联对应镜头及图片 Artifact。 | Retry 恢复原请求；Regenerate 创建新请求。图片、QC 报告及未来视频均由 Core Artifact 保存，不建立 Comic 专属媒体库。 |

领域 Shot 状态为 `draft → planned → generating → checking → approved`；`generating/checking` 可转 `failed`，`checking/failed` 可在明确修复方案后转 `repairing`，再进入新一次 `generating/checking`。这些是 **Shot 的领域状态**，不替代 Core Run/Node 状态；状态改变应与相应修订或生成请求建立可追溯关联。`PASS / MINOR_ERROR / MAJOR_ERROR` 是 QC 判定，不是供应商状态；不允许把“已提交”当成“已出图”。

### 存储与迁移顺序

1. Phase 2 在现有 SQLite 数据库中添加 **Comic Domain 自有**的项目与追加修订存储（项目表、带 `project_id/entity_type/entity_id/revision` 唯一约束的领域修订表）。Pydantic 领域 Schema 校验每一类 payload；一次写入使用事务和预期修订号，避免并发覆盖。迁移不得修改 Core `runs`、`artifacts` 或预算台账语义。
2. 新写路径先只服务新作品；旧 `comic.production.v1`、StudioTask、`shot/persona/recipe` 继续原样读取。为旧记录建立**显式、可重跑**的导入映射：先列出 episode、项目名、角色名和请求 ID，发现同名冲突时要求人工选择归属，不猜测合并；导入只追加新记录并保存 legacy ID 对照，不删除旧行或文件。
3. 通过对照测试核实镜头顺序、角色内容、Prompt 哈希、Run/Artifact/预算关联、未知供应商任务恢复与审批记录。确认新旧读取一致并完成备份/回滚演练后，才切换新项目读取入口；旧 API、CLI 与 Skill 在调用方迁移前保持兼容。
4. 仅当静态引用、路由、动态加载、Skill manifest、前端调用和回归测试均证明旧适配实现不再使用，才删除被替代**代码**。历史账单、作品、Artifact、StudioTask 文件及其迁移对照长期保留；表的删除不是本图片生产阶段目标。

### 后续阶段的交付门槛

- Phase 2：Project + CreativeBrief 的保存、编辑、恢复、并发修订与旧数据隔离测试；不调用生图。
- Phase 3：DirectorSpec 使用 Brief 修订、故事/角色/场景上下文；测试硬约束保持、修订溯源与无固定摄影公式。
- Phase 4：资产项目隔离、引用 Artifact、锁定版本与跨镜头复用测试。
- Phase 5：任意合法镜头数、稳定 Shot ID、重排/墓碑/恢复和状态转换测试；旧 20 镜接口继续兼容。
- Phase 6：先建立作品级 Prompt Compiler 与不可变 Prompt Artifact，不提交生图任务；图片能力、预算和供应商恢复验收顺延至下一图片生产阶段。
- Phase 7：复用现有视觉 QC 与审批，验证 PASS/MINOR/MAJOR 的真实证据、返工版本和费用；没有可用局部编辑 Provider 时明确标记 `MINOR_ERROR` 需要人工处理或获批重生，不伪称局部修复成功。

每阶段先运行相应回归测试，再运行全项目结构检查、ruff、pytest 与前端构建/类型检查；有真实供应商费用的验收另外确认预算。**不得在 Phase 1 一次实现 Phase 2–7，也不得在本图片阶段自动启用视频。**

### Phase 2 已实现的作品上下文边界

`domains/comic/projects.py` 在现有 Core SQLite 文件中追加 `comic_schema_migrations`、`comic_projects` 和 `comic_entity_versions`，后者保存不可变的 Project/CreativeBrief 修订。创建作品同时创建 Brief v1；更新 Brief 必须提交当前作品 `expected_version`，并原子追加 Brief 与 Project 修订。重复提交完全相同的内容不增加版本；并发不同修改不能静默覆盖。`GET` 可按作品版本读取旧 Brief，Context 响应带明确的来源版本。

HTTP 入口为 `POST /api/comic/projects`、`GET /api/comic/projects/{project_id}`、`PUT /api/comic/projects/{project_id}/brief`、`GET /api/comic/projects/{project_id}/context`，同时兼容请求中的 `/comic/projects` 写法；均沿用现有本机会话令牌、Trace 和错误处理。创建请求可直接提供结构化 Brief；若只提供标题，则仅把标题保存为原始需求，其他字段保持空列表，**不会凭空推断用户约束或偏好**。Phase 2 的 Context 最初只含 Project、Brief、可选当前任务与来源修订，`relevant_memory` 为空；Phase 4 才加入有界资产选择。Context 是可按版本重建的派生视图，不另存一套会漂移的内容表。

本次迁移只建新表，不导入、改写或删除旧 `shot/persona/recipe`、StudioTask、账单、Run、Artifact；旧入口保持原样。历史数据自动归属作品会出现同名冲突，因此必须等待显式映射与对照测试，不能按作品名猜测迁移。

### Phase 3 已实现的导演决策边界

`domains/comic/director.py` 使用 Phase 2 的 `ComicContextBuilder`，只把当前 Project、CreativeBrief、可验证的相关记忆与当前任务送往现有共享 `core.llm.chat`。Phase 4 起导演生成可通过任务名称/别名或显式资产 ID 选取已有资产；未匹配时 `relevant_memory` 仍为空，不能伪造资产。它不会把全量聊天、旧作品或旧 Prompt 偷渡给模型。模型返回的是结构化、可编辑的 DirectorSpec（叙事目标、视觉方向、镜头语言、构图、光影、色彩、情绪、角色重点、约束与创作选择原因），**不是生图 Prompt**。导演指令禁止固定情绪词到摄影公式的映射；用户硬约束由服务补齐并在存储层再次检查，人工编辑也不能遗漏。

DirectorSpec 与 `project_id`、`creative_brief_version` 绑定，在原有 `comic_entity_versions` 中追加不可变修订；迁移 v2 仅给 `comic_projects` 增加当前导演方案的 ID/版本指针，不改变旧业务表。Brief 实质更新会清除当前指针，但保留全部旧导演修订；旧 Brief 下的方案可查看，不可直接恢复为新版 Brief 的当前方案。恢复会复制旧内容成为**新修订**，不覆盖历史；提交需要当前作品版本以防并发覆盖。`source` 标明模型生成、人工编辑或历史恢复。

### Phase 7.2 第一阶段：DirectorSpec v2 Schema

DirectorSpec v2 在同一个领域实体上增加 `schema_version`、`creative_decision`、`director_plan`、`cinematography`、`critic_result` 与 `knowledge_refs`。这些字段保存公开、可编辑的导演决策，不保存模型私有思维链，也不包含供应商 Prompt 或生成参数。`schema_version=1` 继续表示现有扁平方案；读取没有版本字段的历史 JSON 时显式标记为 v1，分层字段保持空值，禁止凭空补全。只有完整提供创意理解、导演计划和摄影计划时才允许写入 v2。该阶段不改变表结构，仍使用 `comic_entity_versions` 的追加修订；Coordinator、Critic、Skill Registry 和 API 扩展在后续阶段接入。

### Phase 7.2.2：Director Skill Registry

导演 Skill 使用现有 Core `SkillRegistry`/`SkillLoader`，manifest 位于 `skills/comic/`。当前登记 `comic.creative_understanding`、`comic.visual_direction`、`comic.cinematography`、`comic.director_critic` 和 `comic.director_assemble`，每个 manifest 声明输入/输出 Schema、`execution_policy`、知识引用和契约测试。Skill 没有 `required_tools`，不绑定 Provider，也不修改 Asset；当前 handler 明确标记为 `contract_only`，只校验 Coordinator 提供的结构化结果。Fast 与 Professional 通过同一组 Skill 的 `allowed_execution_modes` 复用，不建立第二套 Skill 或 Runtime。

### Phase 7.2.3：ComicDirectorCoordinator

`domains/comic/coordinator.py` 使用现有 `SkillRegistry`、Core Run/Runtime Event/Trace 和作品版本存储。调用方须注入共享文本能力的阶段执行函数；Coordinator 本身不导入 Provider，也不会把契约 handler 冒充模型。Fast 顺序执行创意理解、视觉导演、摄影指导、组装；Professional 额外执行 Critic，逐阶段将公开结构化结果写入 Run state 与事件。专业模式可指定先前已完成 Run 和起始阶段，在输入 Brief/资产/分镜/镜头版本一致时复用前段结果并从该阶段重跑；重跑创建新的 Core Run，旧记录不覆盖。

两种模式均使用同一 `DirectorSpec v2` 结构；输入资产、Storyboard、Shot 版本写入导演方案。若传入旧方案且依赖版本发生变化，Run state 与 `director_spec_created` 事件标记 `storyboard/shot/prompt_artifact` 为 stale，供后续 UI/API 阻止沿用旧结果；当前不改写这些实体的历史修订。失败写入具体 `skill_id`、`trace_id`、`error_id` 和脱敏 traceback，Run 标为 failed，禁止默认填充导演结果；已完成、失败或等待审核的专业 Run 可在输入版本一致时从指定阶段重新执行。本阶段未新增 HTTP API，也未接入图片或视频。

### Phase 7.2.4：Director Critic 与一次修订

`domains/comic/critic.py` 用确定性规则检查用户硬约束、资产版本与上下文范围；使用现有共享 `core.llm.chat`（配置仍来自 settings）检查具体故事的视觉因果、资产语义一致性、模板化表达和公开导演理由。不存在情绪到镜头的固定映射。模型只能返回严格的公开证据 Schema；字段路径和逐字证据必须可核验，私有 reasoning/CoT、整份重写方案和无效结构均拒绝，原始非法响应不会带进 traceback。旧审核 JSON 的 category/message 和 field/reason 保留读取兼容；新结论增加 code、field_path、evidence、expected、suggested_action、allowed_patches、审核版本与方案指纹，无数据库迁移。

Professional Coordinator 先用 Engine 审核，再经现有 SkillRegistry 校验/记录结论。轻微 warning 只允许修改相关白名单字段：构图、色彩、主体环境关系及部分摄影光线字段；Patch 必须带预期原值，不能修改 CreativeDecision、硬约束、资产或来源版本。自动修订最多一次，然后重新审核一次。最终组装由已有协调层投影分层结果，不再另调用模型重写已审核方案。严重 error/blocked、无可用 Patch 或复审未通过时保留候选到原 Run state，标记 `needs_review`，Run 等待；不保存为当前 DirectorSpec，也不继续 Prompt 编译。重新进入 Critic 时重新审核，不能借用旧 pass 结论。

公开事件使用原 Core Runtime Event 的 `director_event` payload：`director_critic_started/completed`、`director_revision_requested`、`director_patch_applied`、`director_review_blocked`，带原 trace/project/Run/Skill 和审核次数。每次审核结论及 Patch 存在原 Run/Event 中，服务重启仍可查询；错误使用原 `public_error`，不新建日志或任务表。Prompt Compiler 与 Prompt Store 拦截没有实际 pass、内容修改后指纹不匹配或资产版本改变的 v2；v1 旧生产链保持兼容。

本轮没有修改 HTTP/UI 入口；Fast 暂时仍保留前阶段的轻量导演路径，未审核 v2 不能直接进入 PromptCompiler。下一阶段将 Fast/Professional 应用入口接到同一 Coordinator，Fast 可精简公开节点但仍保留约束检查与必要审核；Professional 展示公开结论、局部修订和人工决定。没有图片 Provider、Vision QC 或新 Runtime。本轮测试使用文本模型替身，不冒充真实付费模型验收。

### Phase 7.2.5：Creation Mode 应用接入

创建导演方案的原 POST 现在接受可选 `creation_mode=fast/professional`。该字段是客户端交互策略，进入 Coordinator 后映射到原 `execution_mode`，不增加另一份持久化模式或 Runtime。未传此字段的旧请求/人工 v1 编辑和读取接口仍兼容；显式模式任务全部经过同一个 `ComicDirectorCoordinator`、原 SkillRegistry、共享文本调用与实际 Critic（最多一次自动 Patch）。Fast 隐藏内部节点与审核细节，不绕过约束与质量门禁。严重冲突仍等待处理，不能以默认方案假装成功。

模式请求返回 `{run_id,status,director_spec,director_execution_summary,recovery_required}`；summary 含 `mode/current_stage/status_label/available_actions/stages`。Fast 只公开自然状态、结果摘要和必要错误；Professional 返回五个节点的真实状态、输入版本、结构化公开结果、摘要与审核结论。最终 DirectorSpec 保持同一 v2 契约并绑定审核指纹，可进入现有 Shot Prompt 编译入口；不自动创造 Storyboard/Shot，不提交图片或视频。已有 `/projects/{id}/tasks` 查询同一 Run Store，模式任务按上述用户可见投影返回，不把 Fast 的内部日志当专业节点呈现，也不出现在旧生产任务中心。

Professional 支持 `previous_run_id + rerun_from`，可用 `stage_edits={节点名:完整公开节点对象}` 修改创意/视觉/摄影节点；由原 Skill 契约验证，后续阶段重新执行并重新 Critic，追加新导演版本。审核结论不能由客户端编辑，修改用户硬约束会被拦截；真正修改原始需求应调用已有 Brief 编辑 API。`resume_run_id` 显式恢复失败/等待/旧实例中断的任务，校验作品、模式、任务、Brief、资产、分镜与镜头版本，复用已完成阶段，创建后继 Core Run 并保留来源 Run。仍在本实例执行的任务禁止重复启动；已经完成的任务直接返回原版本结果，不调用模型。服务重启仅显示可恢复，不自动重发未知文本调用；用户显式恢复时未完成阶段可能产生新的文本费用。

新增公开事件 `director_mode_selected/director_stage_visible/director_stage_completed` 使用已有 Runtime Event payload；原 Skill/审核事件仍保留用于后端追踪。创作域的「导演方案」工作区复用当前品牌、样式 Token 和 API 客户端，普通模式仅输入需求与查看摘要；专业模式按需展开节点/版本/编辑。旧制作对话、资产栏与 Inspector 保留可收起，不复制聊天或生产 Workflow。刷新后从现有作品与任务 API 恢复，浏览器只缓存上次作品 ID，不缓存真实任务结果。当前只有导演与 Prompt 基础能力，没有新图片、Vision QC 或视频链路。

HTTP 入口：`POST /api/comic/projects/{id}/director-spec`（仅 `expected_project_version`/可选 `task` 为模型生成；附 `draft` 为人工编辑）、`GET /api/comic/projects/{id}/director-spec`、`GET /api/comic/projects/{id}/director-spec/versions`、`POST /api/comic/projects/{id}/director-spec/restore`（`version` 与 `expected_project_version`）。沿用本机会话、Trace 与异常机制；API 不创建 Storyboard、Prompt Artifact，不调用图片、视频或 QC。共享文本模型可能产生费用，离线测试使用替身，不能声称已完成真实模型付费验收。下一阶段应在现有版本体系中加入项目范围的角色/场景/风格资产及真实相关记忆选择，仍不应把资产与 DirectorSpec 直接拼成供应商 Prompt。

### 漫剧任务追踪补充（Storyboard 前）

DirectorSpec 创建沿用 Core `runs` 与 `run_events`，每次请求有独立 `run_id`、`task_id`、`trace_id`、`project_id`；`state` 保存 `task_type`、`task_status`、`last_completed_step`、Brief/作品版本、选中资产 ID 与版本，失败时保存 `error_id`。领域任务状态词预留 `draft/planning/generating/checking/completed/failed`，目前只有 DirectorSpec 的实际步骤会推进这些状态；不把未来 Storyboard/Image/QC/Repair 冒充已实现。Run 的通用状态仍由 `ExecutionStatus` 管理。`GET /api/comic/projects/{id}/tasks` 从原有 Run Store 按作品读取；进程重启后可查询最后持久化状态，旧实例遗留的运行中任务标记 `recovery_required`，但**不会自动重发未确认的模型调用**。这些轻量追踪 Run 不进入生产任务中心，也不能通过 Graph Workflow resume 误启动。

`data/logs/kantoku.log` 的结构化记录包含公开的任务输入摘要（脱敏且最多 1000 字）、创建时间、Brief 版本、上下文范围、资产选取原因与版本、模型请求 ID/供应商响应 ID、token 用量（未知时明示 unknown）、耗时与重试次数。错误经现有 `public_error` 记录 `trace_id`、`error_id`、异常类型和脱敏 traceback；HTTP 仅返回安全错误。`conversation_id` 仅在调用方传入时关联，独立作品 API 不伪造会话 ID。资产引用缺失时日志包含项目与资产 ID；不写 Secret、私有思维链或完整模型上下文。未来 Storyboard/Shot 生图/QC/Repair 应沿用同一 Run/事件与上下文约定，并按真实步骤推进状态，不另建 ComicRuntime 或 ComicTaskManager。

### Phase 4：小范围资产研究与实现

只参考资产管理的三个案例，不照搬其产品流程：

| 案例 | 可借鉴结构 | 不适合 Kantoku 的部分 |
| --- | --- | --- |
| [Figma 组件变体与版本历史](https://help.figma.com/hc/en-us/articles/360056440594-Create-and-use-variants)、[恢复版本](https://help.figma.com/hc/en-us/articles/360038006754-View-a-file-s-version-history) | 稳定的资产身份、可控属性差异、非破坏性恢复。 | 全文件版本及变体组合不等于角色/场景逐项修订；大量变体会使资产库臃肿。 |
| [InvokeAI Gallery](https://invoke.ai/features/gallery/) | 区分生成结果与外部参考素材，保留可追溯的生成元数据。 | Board 是媒体整理视图，不应复制为第二个 Artifact 库或替代作品关系。 |
| [Mem0 检索](https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/memory-operations/search.mdx) | 先限定所属范围，再按相关性与数量选择记忆。 | 当前项目没有经验证的向量索引；不能把名称/别名匹配冒充语义召回，也不引入独立记忆服务。 |

Kantoku 的资产契约：`ComicAsset` 有稳定 `asset_id`、`project_id`、`kind`、名称/别名、`version`、`project_version`、状态和可选 `pinned_version`；`details` 按 `CharacterAsset`（外观、服装、特征）、`SceneAsset`（地点、时间、天气、光线、氛围、环境特点）或 `StyleBible`（艺术方向、色彩、材质、镜头语言、光影）严格校验。共通字段包括固定约束、标签、`reference_artifact_ids`。参考图只引用已有且就绪的 Core 图片 Artifact，不保存第二份媒体记录。资产描述由用户/现有调用方提供；Phase 4 不自行猜测角色外观或自动生成参考图。

迁移 v3 只增加 `comic_assets` 当前指针表，资产内容继续追加到已有 `comic_entity_versions`；旧 StudioTask、Persona、Shot、Run、Budget、Artifact 不迁移、不删除。编辑、锁定、解锁、删除和恢复均追加修订，作品版本同步递增并用预期版本阻止并发覆盖。锁定版本供后续多个镜头引用；编辑仍保存新修订，锁定指针不会暗中漂移。删除写墓碑，恢复写新修订，历史保留。过去某个 Project 版本的上下文只会读取当时已存在的资产修订，不混入未来资产。

Context Builder 现在可组合当前/指定作品版本的 Brief、与该 Brief 对齐的 DirectorSpec，以及有限的相关资产。显式 `asset_id` 优先；随后只用任务中的**名称/别名精确包含**选择角色和场景；若仅有一个有效 StyleBible 则自动带入。最多选择 8 个，返回每个资产的真实修订号和参考 Artifact ID；不发送全资产库，也不声称已有语义搜索。HTTP 入口为 `/api/comic/projects/{id}/assets`（POST 创建、GET 列表）、`/{asset_id}`（GET、PUT 编辑）、`/{asset_id}/versions`（GET）、`/{asset_id}/lock|restore|delete`（POST）；原 `/context` 可用 `task` 或重复的 `asset_id` 参数选择。尚未建设上传素材、资产自动提取、分镜关联、Prompt Compiler、生图、QC 或视频。

### Phase 5：作品级 Storyboard 与 Shot

迁移 v4 仅增 `comic_storyboards`、`comic_shots` 当前指针表；不可变内容继续写入已有 `comic_entity_versions`，作品版本在同一 SQLite 事务中递增。旧 `tools/storyboard.py` 的固定 20 镜/按剧集覆盖工具与历史 `shot` 表保持兼容，不导入、覆盖或删除旧数据；它的结构与新的可变镜数、资产版本引用不同，不能直接当新作品存储。

`ComicStoryboard` 固定 `project_id` 与创建时的 `director_spec_version`，保存标题、描述、状态及有序 Shot ID；镜头数不固定，单次规划上限 100 仅用于防止异常输出。`ComicShot` 有稳定 `shot_id`、序号、目的、主体、行动、环境、情绪与摄影建议，只引用明确的 Character/Scene/Style 资产 ID **及版本**，不复制完整外观或场景描述。编辑、重排、删除和恢复追加新修订；删除是墓碑，恢复生成新版本。历史 Storyboard 恢复若镜头集合已变，必须先恢复相应 Shot，不能悄悄丢弃镜头。Brief/Director 变更后旧 Storyboard 保留可读，但不能继续编辑为当前制作方案。Phase 5 手动 Shot 仅允许 `draft/planned`；`generating/checking/approved/failed/repairing` 预留给后续真实生产步骤，不允许通过编辑接口冒充执行。

创建分镜可提交手动草案，或明确设置 `generate=true`。AI 规划只接收当前 Project、CreativeBrief、DirectorSpec、最多 8 个相关资产与当前任务，调用现有共享文本模型；模型自行决定镜头数量和节奏。模型输出的资产引用必须出现在传入上下文，存储前还要核验项目、类型、状态与固定修订号。生成或写操作沿用 Core Run/Event 与现有 `trace_id/error_id` 日志，记录作品、分镜、镜头和版本；这些规划 Run 不进入生产任务中心。模型调用的供应商、请求 ID、token、耗时与重试由共享 LLM 日志记录。本阶段不调用图片/视频 Provider，也不创建 Prompt Artifact、预算预占或 QC 结果。

HTTP：`POST/GET /api/comic/projects/{id}/storyboards`；`GET/PUT /api/comic/storyboards/{id}`；`GET /api/comic/storyboards/{id}/versions`；`POST /api/comic/storyboards/{id}/restore`；`POST/GET /api/comic/storyboards/{id}/shots`；`GET/PUT /api/comic/shots/{id}`；`GET /api/comic/shots/{id}/versions`；`POST /api/comic/shots/{id}/delete|restore`。所有写操作需要预期作品版本及相应对象版本，避免并发静默覆盖。Phase 6 从当前 Storyboard/Shot 修订和固定资产版本编译 Prompt；不能让用户编辑 Shot 等同于已生成图片。

### Phase 6：Prompt Compiler 与 Prompt Artifact

`domains/comic/prompts.py` 只做作品级镜头提示词编译。它复用当前 Project/Brief/Director、当前 Storyboard/Shot 和 Shot **显式固定的**角色/场景/风格资产版本；不装入全聊天、其他镜头、旧 Prompt 或无关资产。共享文本模型依据镜头目的、导演方案和目标模型特点生成结构化正向/负向 Prompt，编译器及存储层均拒绝遗漏用户硬约束或资产固定约束的结果。模型标识由 `config/settings.yaml` 提供；编译适配接口可按目标模型扩展，不改图片 Provider。共享 LLM 日志记录模型调用、耗时和可用的 token 用量；领域日志记录版本、资产、编译器和输入输出长度，不写密钥或私有思维链。

迁移 v5 仅追加 `comic_prompts` 当前指针表；每次编译、人工编辑或恢复都在已有 `comic_entity_versions` 中追加 `prompt` 修订，并在**同一 SQLite 事务**写入 Core `ArtifactType.PROMPT`。Prompt 正文与溯源元数据随 Artifact 持久化，领域修订保留完整可比较快照；旧版本与旧 Artifact 不覆盖。每版保存 Brief/Director/Storyboard/Shot、资产 ID/版本、编译器版本、模型、来源 Run 和 Prompt 哈希。作品与 Prompt 的预期版本防止并发写入；镜头变更后须重新编译，旧来源不匹配时禁止直接恢复。复用 Core Run/Event/Trace，不建立第二套任务或媒体存储。当前版本**没有**图片生成、预算预占、视频或 QC。

HTTP：`POST /api/comic/shots/{id}/prompt/compile`（`expected_project_version`、`expected_shot_version`）；`GET /api/comic/shots/{id}/prompt[?version=n]`；`GET /api/comic/shots/{id}/prompt/versions`；`PUT /api/comic/shots/{id}/prompt`（人工编辑，含预期作品/Prompt 版本）；`POST /api/comic/shots/{id}/prompt/restore`（从兼容的旧版本追加新修订）。下一图片生产阶段才把已选定的 Prompt Artifact 作为不可变输入，调用共享 Image Capability，并沿用预算预占、幂等、Artifact、Provider Job 恢复和 QC 的既有基础设施。

## Phase 7.2.6：同一创作 Workspace 的前端投影

漫剧工作台只维护一份 `DirectorWorkspace`，复用现有 Splitpanes、MessageComposer、用户气泡、Markdown 与图片附件。普通/专业模式控制下一次导演接口的 `creation_mode` 以及节点可见深度，不创建第二套 Conversation、Coordinator 或工作流。左侧资产入口切换中央资产页面，不再使用覆盖对话的制作资产 Drawer；Inspector 默认关闭，只占上方节点区，不改变下方聊天宽度。节点编辑草稿按 Project/Run/Stage 隔离，切换页面不丢草稿；实际保存、重跑、恢复与 Prompt 编译调用已有 API，使用已有预期版本检查。

导演 Draft 的确认现在复用服务端 Core Approval，不再由浏览器确认指纹充当权限。`POST /api/comic/projects/{id}/director-spec/confirm` 接收 `version/expected_project_version`，只确认当前 Brief 下已经通过实际 Critic 的不可变导演修订，并检查绑定资产/镜头依赖。确认绑定 Project/Spec/Version/Brief，编辑或恢复产生新版本，不继承旧审批。Storyboard 创建和修改、Shot 操作、Prompt Store 在后端检查审核及确认；创建分镜在文本模型调用前拦截。旧 v1 生产契约保留兼容，新 v2 不能通过直接 API 绕过。确认记录使用同一 Core Run/Approval/Event，不增加数据库表。

当前导演 API 接收 `conversation_id` 并保存在 Core Run，但不追加 Conversation Message。前端为 Project 复用一个既有 Conversation ID，并由真实关联 Run 的用户任务、公开导演摘要与修改记录恢复连续对话展示；不调用旧 Guided 聊天路由去偷偷创建单镜头生产。**数据库 Conversation.messages 同步仍未接入**；用户确认已从共享 Runtime 持久化读取，不依赖当前浏览器。已有单镜头 Run/Artifact/审批/供应商查询保留兼容入口；新作品没有接入图片生产时必须明确说明，不伪装出图。UI 只呈现 v2 白名单公开字段、真实节点摘要和 trace/error ID，不输出旧字段或私有思考。

布局参考 [Figma 导航](https://help.figma.com/hc/en-us/articles/360039831974-Explore-the-navigation-bar-and-left-sidebar)、[Runway 对话式创作](https://help.runwayml.com/hc/en-us/articles/51601639579667-Creating-with-Runway-Agent)、[InvokeAI](https://github.com/invoke-ai/InvokeAI)、[Langflow](https://github.com/langflow-ai/langflow) 与 [ComfyUI Frontend](https://github.com/Comfy-Org/ComfyUI_frontend)。只提炼可收起导航、节点工作区、上下文详情与结果历史，不复制节点 Runtime 或引入另一套组件体系。离线 UI 验收可运行 `frontend/tests/workspace-preview.mjs`，使用隔离端口与内存测试数据；测试数据不写入业务数据库，不冒充真实模型验收。

左侧入口固定为对话、导演、分镜、Prompt、资产、历史。对话入口只是展开同一 Project Conversation，创意理解仍由导演节点和连续聊天共同呈现，不另建“原始创意”页面。已有生成结果作为资产页面的一部分展示，避免再出现覆盖聊天的 Drawer 或独立作品面板。

Director Critic 接受少量旧字段别名时，必须先标准化为 v2 叶子路径再执行白名单校验：`emotion → creative_decision.emotional_target`、`visual_direction → director_plan.visual_focus`、`camera_language → cinematography.camera_language`。标准化不扩展可写范围；硬约束、Project 身份、资产版本及 StyleBible 引用始终不可 Patch。兼容字段由分层方案投影，不能维护两份互相漂移的导演决策。

### Phase 7.3：字段容错、上下文审计与执行中交互

Critic 模型 findings 只读取 `CRITIC_ALLOWED_FIELD_PATHS` 内的公开 v2 叶子字段。已知别名先规范化（包括 `creative_brief.original_request → creative_decision.intent_summary`）；未知/越界字段写脱敏 `critic_warning`、原路径、`action=ignored` 和 Trace 后忽略，不使整个任务失败。输入上下文仍不是可写输出。模型 Patch 必须关联真实 warning、在可写白名单内、预期值匹配且不重复；无法应用的建议转为待人工审核，不放宽直接 `apply_patches` 的保护。确定性硬约束与资产身份/版本审核不受模型字段容错影响；只允许一次自动局部修订，重新审核未通过则等待用户。

DirectorPlan 追加可选 `style_boundary`、`character_expression`、`character_pose`、`character_presence`；沿用已有实体版本存储，不新增表。未设置的新增字段不参与历史审核指纹，避免旧 v2 审核凭据无故失效。每次导演执行记录脱敏 Director Input Snapshot：Project、Conversation、用户请求摘要、Brief 版本、选中资产/记忆版本、前序 Run 与 Trace。四类创意回归使用隔离文本模型替身，只验证路由、上下文与数据契约，不代表真实模型的导演审美评价。

工作台执行中输入框保持可用：补充需求保留发送时模式，在**当前页面**排队，当前 Run 结束后依次提交，错误或结果未知时停止自动处理；可撤回未执行补充。队列不是后端 Conversation Message，刷新会丢失未提交补充，UI 必须明确说明。取消调用现有 Core Run 接口，Coordinator 在阶段前后和 Critic 事件边界检查取消；不能中断已发出的模型 HTTP 请求，但取消后不进入下一阶段、不保存新方案。复用已有 Run/Event，不增加 Runtime、API 或 Workflow。Fast 对话只展示创作理解与导演摘要；Professional 可查看独立节点，页面切换保留原会话与编辑草稿，尊重减少动画设置。

## Director Pipeline 稳定性与真实验收（2026-09-29）

前端 Workspace 大规模布局改造暂缓；本轮仅补现有草稿编辑/确认交互。
收口既有 Director Coordinator / Critic / Context，
不创建 Runtime、Skill、Asset、Prompt 或 Provider 副本。

- **输入隔离**：导演调用只读取当前 Brief、当前任务与选中资产。项目标题/描述属于导航元数据，
  可能保留旧创意，不再送入导演模型充当故事事实；Project API 仍保留导航元数据，
  所有 Context 只传 project_id，兼容的旧导演入口也不能从标题恢复旧创意。
  新的导演 `task` 先经 Context Builder 的语义边界检查（复用共享文本出口，无关键词映射）；
  同方向修改保留 Brief。新方向以当前请求创建独立 Brief 修订，不携带旧偏好或硬约束；
  `CreativeBriefFork` 通过现有 `comic_entity_versions` 和 CAS 追加父版本、原因及归档生命周期，
  不覆写旧 JSON，不新增表。历史快照保留当时状态，`brief_versions()` 投影当前归档状态。
  判断失败直接返回可追踪错误，不能默认继承或伪造新 Brief。GET Context 仍为只读，不调用模型。
  分叉后的隐式资产召回排除该创意边界前创建的资产（改版不改变资产所属方向）；只有明确
  指定 Asset ID 才可跨方向复用。新资产仍复用原资产召回，旧 DirectorSpec/分镜/镜头不自动继承。
  既有聊天历史、其他作品不会自动送入生成上下文。
- **恢复校验**：已经完成的 Run 也先校验任务、Brief/资产版本、Storyboard/Shot 身份，
  再返回原结果。新输入不能用 `resume_run_id` 借回旧方案；显式空资产列表不会偷用旧引用。
  阶段恢复核验实体 ID，而不只比较恰好相同的版本号。
  源 Run 的请求不同时在边界判定和模型调用前拒绝恢复；普通新请求不查找或恢复失败 Run。
  每个 Run 明确保存 `input_brief_id` / `input_brief_version`，生成事件关联输出 DirectorSpec ID
  和版本。输入快照事件的 `creative_context` 公开 brief_used、previous_brief_detected、
  fork_created 和 reason，便于区分沿用、分叉与显式恢复，不保存思维链。
- **审核与写权限分离**：Critic 可以审核 v2 公开决策字段，但 Patch 只允许小范围白名单。
  别名先规范化；未知路径记录 warning 后忽略。硬约束、Project/资产身份与风格引用始终只读。
  逐字证据仅忽略排版空白，不做模糊语义匹配。不可核验的合法字段结论转为 `needs_revision`，
  保留候选并停止下游，不能使 Run 因引文格式直接崩溃，也不能伪装 pass 或应用未核验 Patch。
- **Director Debug Trace**：在原 Run state / Runtime Event 中保存输入快照与每个阶段的
  输入/Context/输出 SHA-256、长度、键名、来源版本、复用阶段与编辑来源。控制台和现有结构化
  日志能按 trace/run/project/skill 关联共享 LLM 请求 ID、provider、model、真实 token、耗时、
  retry、审核规范化 warning 与安全 error_id/traceback；不记录原始无效模型响应或 CoT。
- **草案与审核结果分离**：前三个导演阶段的真实公开输出通过 Schema 后，先保存
  `director_candidate`，发出 `director_draft_created`，再执行 Critic。审核待修订、审核请求
  失败或 Patch 应用失败时保留原草案，Run 为 `waiting`，不伪造 pass，也不启动 Prompt/生图。
  生成阶段失败仍为 `failed`，不默认填充方案。每阶段的 `stage_statuses` / `stage_failures`
  复用现有 Run state，记录 stage_name、trace_id、error_id、input_version、已完成公开输出和
  安全异常摘要；完整脱敏堆栈留在统一日志中。审核失败不得发出审核完成事件或推进完成检查点。
  任务 API 的 `director_spec` 可返回真实草案，并明确 `director_spec_status=draft`、
  `draft=true`、`ready_for_prompt=false`。满足不可变约束的待审草稿追加到原实体版本表，
  返回真实 spec_id/version；违反硬约束的候选只保留 Run，不提升为当前修订。
  通过审核后的已存方案才返回 `reviewed`；ready_for_prompt 还须当前版本的用户确认。
  Fast 隐藏节点输出，但返回最小真实状态和故障原因；Professional
  提供完整公开阶段输出。重启只查询现有 Run，不自动重提；显式恢复只重做必要审核。
  非法 Patch（未知字段、禁止写入、格式错误、过期预期值或替换值不符合 DirectorSpec Schema）
  记录 `INVALID_PATCH` 并忽略该建议。硬约束/资产身份只读，最多自动修订一次，门禁不放宽。
- **真实与离线验收分开**：`evals/director.jsonl` 定义异兽、人物、情绪、科幻四个独立案例。
  `uv run python scripts/verify_director_pipeline.py --allow-paid --max-cny 2` 经现有应用 API、
  Coordinator 和共享 LLM 执行；项目/会话/Run 位于独立 `data/qa/director-live-*/runtime.db`，
  不写生产业务库。输出公开方案、Trace、真实 token、保守费用估算和审核结果到同目录 report。
  配置缺少对应文本价格时拒绝执行；逐调用检查剩余额度，错误/未知用量停止，
  不自动重试、不自动切备用供应商。总额是按配置的保守单价估算，不冒充供应商最终账单。
  四组均完成、实际 pass、主体无串案、视觉策略不完全相同，才能标记通过；
  四个样本不代表所有创意的审美质量已获证明。离线替身只验证工程契约，不替代真实验收。

### 当前创意和可编辑草稿补充（2026-09-29）

- 工作台新输入显式发送 `creative_operation=new`；不同于当前 Brief 时直接分叉，
  不把旧 Brief 交给模型猜测是否沿用。新任务只使用本轮显式绑定资产，旧 StyleBible
  不自动继承。旧客户端的 `auto` 语义边界接口保留兼容；恢复操作仍严格绑定原请求。
  阶段系统指令不再把所有当前任务都解释为旧作品补充。
- 两种模式均可直接编辑公开分层方案；未保存编辑可以放弃，版本历史可恢复为新修订。
  指令式修改必须绑定 `expected_director_version`，只传当前 Brief、当前公开草稿及其资产，
  通过原共享文本出口产生修订。当前方案未选中、版本变化或硬约束/资产引用改动即拒绝。
- 原 POST 的 `draft` 保存人工修订，`revision_instruction` 保存指令修订，均清除旧 Critic
  结果。`creation_mode + review_current=true + expected_director_version` 通过同一 Coordinator
  重新审核当前稿，不重新生成前三阶段、不接受客户端 pass。审核通过仍需显式用户确认。
  手工修改在原 Run 中记录请求、版本与 Conversation，任务 API 投影为保存草稿，
  不伪造 Skill 执行。没有新 Runtime、Skill、Prompt、Asset、Provider 或数据库表。
- 当前修订由后端版本/审批恢复；旧 Run 入口 URL 不应让工作台在刷新后默认编辑第一版。
  前端仅复用已有 DirectorNodeView、MessageComposer、Markdown、历史和 API 客户端，
  保留现有布局与字号。离线浏览器验收复用 `workspace-preview.mjs` 的内存数据，
  与真实付费模型验收分开。

### Phase 7.3.1：摄影输出适配与阶段恢复

摄影阶段保留旧 JSON 读取，并支持公开说明 `public_decision`、`structured_plan` 和
`creative_reason`；Skill 契约适配仍使用原 Registry，不绑定 Provider。自然语言只通过
共享文本出口做一次语义提取，不重新导演、不追加历史上下文、不按情绪套用摄影公式。
提取只整理原文，未表达字段保持 null。已有完整摄影计划默认 complete，历史审核指纹
不因新增空值字段改变；不完整计划显式为 needs_revision 或 missing，不能伪装完整。

摄影阶段解析/调用失败保留已完成的创意、视觉方案，形成可版本化 v2 草稿和 WAITING Run，
不是整条流程 FAILED；其他阶段的基础设施/契约异常仍保留其原失败语义，不凭空填充决策。
沿用原 Runtime Event payload 的 `node_warning/schema_validation_failed/stage_output_saved`，
记录 skill/trace/project/输入版本、错误 ID、缺失字段、解析问题及有界脱敏公开原输出。
私有 think/reasoning/CoT 和未知 JSON 字段不进入快照；原响应仅保存 SHA-256 用于关联。
摄影未完成不发 completed 事件、不推进摄影完成检查点，显式恢复只重做摄影及后续审核。
Critic 仍检查硬约束与资产身份，缺失摄影转为待修订，无默认镜头或自动补齐 Patch。
没有实际 complete、Critic pass 及用户确认，不能进入 Prompt/制作。没有 UI、表结构、
Runtime 架构或 PromptCompiler 改动；离线解析/恢复测试不代表真实模型的摄影质量验收。

## 唯一入口

- 后端：`src/kantoku/__main__.py` → `shells/web_studio.py` → `core/runtime/`。PyCharm 共享运行配置在 `.run/Kantoku Backend.run.xml`，使用项目 `.venv` 与 `scripts/run_backend.py`；标准命令为 `python -m kantoku serve`。
- 前端：`frontend/src/main.ts` → `App.vue`/`router.ts` → `views/`。`frontend/src/services/core.ts` 是 Core API 客户端。Vite build 输出到 `src/kantoku/shells/web/`，该目录是后端静态资源，不是另一份前端源码。
- `core/` 不导入领域或适配器；`domains/` 负责状态和业务分支；`capabilities/` 负责通用 AI 能力；`adapters/` 负责供应商与平台协议。文件 Skill 由 `core/skills/loader.py` 扫描 `skills/`。

## 生图请求与恢复

`generation_request_id` 是台账的 `reservation_id`，本地 `idempotency_key` 与它一致。`ledger` 保存 `run_id`、`provider`、`provider_job_id`、预算状态与最终 `artifact_id`；`image_result` 保存最近的查询结果。旧数据库由现有预算连接入口增量补充元数据列，保留历史台账。

一次请求只调用一次 `submit`。`submitted`/`unknown` 且有 `provider_job_id` 时继续 `query`；还在生成则 Run 保持 `waiting`，重启后对同一 Run 执行 `resume`。没有任务 ID 时标记 `NEEDS_RECONCILIATION` 并保留预占，不自动重提。只有用户明确创建新的生成任务，才使用新的请求 ID。`released` 表示未提交或明确失败并释放预算，不能冒充已提交任务。

查询 `data/logs/kantoku.log` 时用 UI 的 `error_id` 或 `trace_id` 定位完整脱敏 traceback；生图步骤还带 Run/Node、生成请求、Provider 和供应商任务 ID。`data/` 内的日志、SQLite、下载图片以及 Run、Artifact、Ledger 业务记录均不进入 Git，也不因代码清理而删除。

## 清理审计规则

先用 `rg` 和 Git 清单检查静态引用，再查路由、动态导入、Skill manifest、测试及构建输出。没有证据证明安全删除时只标注候选。旧版本交给 Git 历史，不创建备份副本。`src/kantoku/shells/web/` 虽是构建产物，但 Python 独立启动时会从这里提供页面，因此不能把它当缓存直接清空。

## 交付检查

### Conversation Workspace 隔离与共享外壳

首页与 Comic 共用 `WorkspaceShell.vue` 和 `ConversationHistory.vue`。节点导航与最近
Conversation 分别管理，切换节点只替换中央内容；标题来自 Conversation，不取 Project
标题或 Run 错误。首次输入的短标题仅用于导航，不改变用户原始需求；用户重命名后不覆盖。

新建必须先创建真实 Conversation，再立即清空当前消息、选中 Run、草稿、失败状态和输入框。
发送时捕获所属 Conversation，异步返回用选择版本校验，不得把旧响应写入新聊天。
切换视图不会取消已提交的后台任务。恢复只认 Run 中显式的 `conversation_id`；同 Project
不代表同 Conversation。现有 Conversation 详情通过 `related_run_ids` 返回绑定 Run，
过滤在数据库查询的 limit 之前执行；不改 Director、审批、Provider 或数据表。

路由查询参数 `conversation` 保留当前会话，刷新后恢复同一会话。Comic 历史任务通过
绑定 Run 的真实公开输入/结果投影恢复，不把 Run/节点/错误记录单独列成历史聊天。

Comic 专业工作区的新创作消息显式发送 `independent_plan=true`。每轮使用独立 Project/
DirectorSpec 与生产指针；同一 Conversation 的多个方案分别编辑、确认和规划分镜，旧方案
不会因为后续对话而过期。原 `draft/revision_instruction/review_current` 仍只修改或审核所选方案，
旧 API 未传此标志时保留动作续写兼容路径。`director_run` URL 精确选中本轮 Run，刷新和
返回时同时恢复所属 Project；会话聊天从所有显式绑定项目的 Run 恢复，不按当前项目截断。
继承先核实同一 Guided Comic Conversation，复用既有 CreativeContextLock、不可变 Director
和固定资产修订；Bible 来源可显式记录原 `project_id`，仍读取原 Prompt 修订/Artifact 并核验
Artifact ID，不复制 Prompt 正文或新建记忆表。固定人物/世界/风格保留，本轮表情、姿态、
视觉焦点与视线说明重新生成；新方案不继承旧确认。明确新主题不携带原记忆。旧版已阻断的
Shot 续写在聊天中显示未完成原因，不冒充新方案，重新发送才走独立方案路径。

独立方案即使继承上下文锁仍属于系统生成，不因人物记忆而标记 USER_EDIT。内部验证复用
既有有界修正与复审，保留真实审核结论；不可修复的阻断项仍停止确认/制作，不伪造通过。
用户主动编辑后的公开建议可复制，并按可编辑叶子字段定位至对应节点、聚焦编辑框；
定位保留其他待保存内容，不自动应用建议。身份、硬约束、审核与未知字段没有编辑跳转。

`scripts/verify_conversation_workspace.py` 可启动独立 HTTP/SQLite 浏览器验收环境，复用
现有应用和测试夹具；离线文本替身明确标识，禁止生图及外部模型调用，不访问生产数据库。
须实际验证：旧聊天 → 新建立即为空 → 新任务短标题 → 返回旧内容 → 返回新内容 → 刷新，
以及执行中切换、搜索、重命名持久化。截图、数据库和日志仅留在忽略的 `data/qa/`。

在根目录执行 `uv run python scripts/check_structure.py`、`uv run ruff check .`、`uv run pytest`；在 `frontend/` 执行 `npm run build` 和 `npx vue-tsc --noEmit`。检查 `git diff --check`、待提交文件与忽略规则，确认没有密钥、日志、数据库、运行时图片后按 Conventional Commits 提交并推送，不使用 force push。涉及付费供应商的实测须单独确认预算与凭据，离线测试不冒充真实账单验收。

## 首页 Quick Domain 真实执行入口（2026-10-03）

- 首页提交显式 `selected_domain` 与 `execution_mode=fast`；普通下一条显式传 `selected_domain=null`，避免持久化的待完成技能污染新消息。旧客户端只允许消费尚未绑定任务的选择。`visual` 是 `studio` 的入口别名；专业会话拒绝首页快速模式参数。
- 同一个 `conversation_router` 优先处理本条手动域选择：Comic/Commerce 进入现有领域 Graph，视觉进入共享 Image/Video Capability。不能再让通用 Chat/Image Planner 覆盖手动选择，也不能在 `stream_conversation` 中复制领域流程。
- Comic 快捷任务在已有 `comic.production.v1` 增量接入导演、审核门、分镜、Prompt 节点，复用 ComicDirectorCoordinator、作品存储、Prompt Compiler、Studio Image/QC/Artifact；旧专业任务没有快捷上下文时仍沿用原图。首页单镜头生产不自动开启视频，也不宣称已制作整部多镜头作品。
- 每条快捷 Comic 任务使用独立的 Project/Brief，输入只含本条需求和显式资产；生产 Run 保存 conversation/message/trace 与 `quick_creation` 中的 Brief、Director Run、分镜、镜头、Prompt Artifact 绑定。导演审核失败保留真实输出，等待修订或返回失败，禁止回退普通聊天、默认方案或未经审核的生图。
- 同一消息的顺序或并发重试复用原生产 Run；进程内分配保护只覆盖创建/提交，不锁住导演执行。`domain_dispatch` 事件必须在 Worker 启动前写入，避免同一 Run 的并发事件序号冲突；恢复生产节点时复用明确绑定的 Director Run，不自动重提未知账单模型请求。
- 费用门放在任何付费导演/图片调用之前，按配置估算整个任务（文本、图片、视觉检查及重试）；已知预计费用不超过首页配置限额时自动执行。首页自动 Comic 图片任务超限或未定价时明确失败，不弹中间审批卡、不假报零成本；旧手动任务与专业模式保留原 Core Approval。Commerce 继续公开标记现有 Mock 数据源/Marketplace，不假装已连接真实商品平台。
- 测试必须等待所有已提交 Worker 结束后才撤销离线替身，并显式禁止真实模型客户端；否则异常退出后的线程可能使用恢复的生产配置。离线回归与付费验收分开执行。

### 首页漫剧自动出图与专业确认（2026-10-03）

- 新首页漫剧图片任务保存 `quick_creation.auto_create_image=true`，内部完成导演/真实 Critic/分镜/Prompt，再自动进入共享图片调用。Critic 的艺术建议（`needs_revision` 且无 error finding）不要求人工确认：生产策略为当前不可变修订生成 Core Approval，记录 production Run 与 `human_review=false/allow_advisory=true`，保留真实审核结论，不伪造 pass 或用户确认。硬约束/资产冲突、摄影缺失、审核执行失败、审核指纹或版本不匹配仍阻止生产。分镜与 Prompt 只接受绑定当前版本的策略授权；直接编译没有授权仍保持严格门禁。供应商真实异步等待/未知账单仍沿用查询/对账，不重提，QC 不通过不能交付。
- 专业作品生产默认 `approval_required=true`，当前导演修订须人工确认；显式 `false` 的已有自动任务保持兼容，同一生产图在 `director_gate` 授权当前导演版本。费用/QC 审批保持既有专业机制，首页预算限额不替代它们。相同 request_id 的重试必须保持作品、导演/镜头版本和审核策略不变。旧待确认 Run 没有自动标志，保留原确认语义。
- 真实图片提交记录 `image_generation_started`（provider/model/prompt_hash），归档记录 `image_generation_completed`（artifact_id），异常记录 `image_generation_failed`（provider_error），沿用 trace/run/task/error ID 和脱敏完整 traceback；未知提交仍标记 unknown 并保留预算，不能因失败日志而重提。首页继续仅显示现有粒子加载、真实阶段映射进度和最终图片，不展示导演中间稿。
- 首页自动 Comic 文本调用在该 worker 的 ContextVar 作用域使用共享 `chat(single_attempt=True)`：只使用已报价主模型，关闭文本自动重试和备用模型；报价同步按此策略计算。不能仅从报价删除未定价备用模型却仍允许调用它。视觉模型沿用原重试并包含在报价中；其他会话与专业模式不受这个局部策略影响。
- 专业工作区“生成当前画面/镜头”通过现有 `/api/runs` 提交 `production_project_id`、project/director/shot 版本、conversation 与唯一 request_id。服务端核实当前已审核且已确认的修订及会话归属后，复用同一个 `comic.production.v1` 的分镜、Prompt、Image、QC、归档链；不再生成另一套 Director。专业费用/QC 审批仍在原任务界面处理，重试同一 request_id 返回原 Run。
- 新首页快捷图片任务不将 DirectorSpec 中间数据写成聊天消息；公开结果留在原 Director Run/Trace 中。首页只展示状态、阶段粒子进度和真实图片/必要错误；历史 quick-director 消息在首页不展示长篇分析。专业节点仍投影可读公开字段。首页图片继续复用原预览/下载组件。
- 创意理解中的 hard_constraints 是输入 Brief 的只读投影，模型不得擅自新增或改写；输出适配层绑定原列表，原始用户需求继续进入导演、Critic 与 Prompt 上下文，不将空列表误解为可以忽略用户原文。
- 粒子占位与图片同宽；百分比明确标为**阶段进度**，只由真实 Runtime 节点完成事件计算。供应商未提供细粒度进度时保持当前阶段百分比并显示真实等待秒数，不人为虚涨。100% 必须同时有 completed Run 和真实图片 URL。错误展示真实错误，不冒充完成。
- 快捷选择发送后由 Run 保存执行上下文，输入框与下一条消息恢复普通模式；顺序/并发重试不重新绑定已消费的选择。预算费用门仍在首次付费调用前，专业任务不新增首页确认门。

### Comic 工作区制作与专业确认（2026-10-04）

- 工作区普通模式默认自动制作；只有主动选择专业导演模式并开启人工审核才走导演草稿确认入口。
  两者复用原生产 Workflow、Run、预算台账与 Artifact，不修改 Commerce 审批或任务中心。
  专业草稿保存三个公开决策节点与真实 Critic。
  用户确认当前不可变修订后，Core Approval response/state 投影为 `approval.status=approved`，
  原 confirmation Run/Event 保存 `workflow_transition: director_review → storyboard_generation`。
  不改 Core 的通用 decision 枚举，不另存一套审批。指纹和依赖有效、摄影完整且无阻断项时，
  用户可以明确接受艺术 warning；不伪造 Critic pass。缺失/失败审核、硬约束/资产冲突仍拒绝确认。
  确认只表示当前版本已获授权，不立即付费。“进入下一步”打开分镜并通过既有作品生产 API
  创建绑定该导演版本的 Run，继续分镜、Prompt、Image、QC、Artifact；不再次生成导演。
  导演页没有生图按钮，镜头生成入口只在分镜页；输入/刷新/版本恢复均读取服务端审批状态。
  确认后复用自动制作策略，预算后台预占/结算，QC 保存真实结果，不额外要求 Task Center 费用/QC 批准。
- 普通模式及关闭人工审核的专业模式通过原 `POST /api/runs` 提交 `state.creative_request`、当前 guided Comic
  Conversation、唯一 `request_id`、`creation_mode` 与 `approval_required=false`。
  应用入口复用同一个 Conversation Workflow 分配锁和 `comic.production.v1`，在 Worker
  启动前持久化用户消息及 `domain_dispatch(entrypoint=comic_workspace)`；不再只创建导演 Run。
  网络重试必须沿用 request_id 和原请求/导演深度，即使 Prompt 已编译也返回原生产 Run，
  不重新生成图片。每条新创意使用独立 Project/Brief，不继承旧作品或失败任务。
- 自动制作使用已有 fast 执行策略：预先报价、配置的自动额度上限、已报价文本主模型单次调用、
  内部 Critic、分镜、Prompt、Image、真实 QC 和 Artifact。未定价、超限、硬约束冲突、
  审核执行失败或未知账单仍停止并明确报错，不能为了自动体验绕过安全门。
  `creation_mode=professional` 仅增加导演决策深度；Conversation 仍属于 guided 工作区。
- 首页 `+ → 漫剧` 保持同一自动策略，不展示内部导演报告或人工确认卡。
  不会自动启动历史草稿或重新提交旧失败 Run。
- 前端轮询父生产 Run 和事件，首次进入真实分镜阶段自动切换分镜页；之后可自由返回导演节点。
  对话和工作区复用原阶段粒子占位/图片附件，结果读取当前 Conversation 的持久化 Artifact。
  自动任务隐藏草稿确认控件；刷新只恢复状态，取消父任务同时取消未完成的关联导演任务。
  没有新增 Runtime、Skill、Prompt 系统、Provider 或数据表，没有页面布局重做。
- 生图明确拒绝且预算已释放/结算后，用户可以显式重新生成；保留原失败 Run，新请求绑定原镜头及
  指定 Prompt 修订。编辑 Prompt 沿用现有版本 API，不重写 Compiler。网络重试保留 request_id。
  供应商提交/账单未知时禁止重提（包括换模型），有 job_id 时恢复原 Run 查询，无 job_id 时等待对账。
- Run 响应的 `image_execution` 是现有节点时间、供应商身份和预算台账的只读投影，不新增持久化结构。
  前端仅显示真实模型、节点耗时及已结算费用；未知费用显示待结算，不估算冒充账单。
  模型/protocol 仍只读取 settings.yaml，更换兼容型号无需代码白名单；前端换模型入口提示配置步骤，
  不虚构未实现的单任务模型覆盖。日志贯通 image_generation_started、provider_request_sent、
  provider_response_received、artifact_saved、image_generation_completed/failed，并保留脱敏异常链。

### 创作域按镜头生图诊断（2026-10-04）

- 已确认导演方案的创作域请求继续复用 `comic.production.v1`；不修改首页、Commerce 或任务中心。
  所选镜头必须通过当前导演/资产/Prompt 版本校验，生成任务使用实际 `sequence_number`，不可固定为 1。
  创作域分镜列表优先返回当前导演版本下的最新分镜，再返回历史分镜；历史仍可显式查看。
  过期分镜仍明确拒绝，不能为绕过错误而静默换成新导演方案或其他镜头。
- 创作域节点追加 `COMIC_IMAGE_GENERATION_STARTED/COMPLETED/FAILED` 日志和既有 Runtime Event，
  携带 project/run/shot/task/trace、provider/model、Prompt 指纹与版本。失败标明
  `shot_validation/storyboard/prompt/prepare/generate/qc/human_review/archive`，原 error_id 和异常链保留。
  图片 Artifact 元数据绑定 Shot、Storyboard 和 Prompt 版本，不新增 Artifact 系统。
- 成功记录 Artifact、真实已结算分值与端到端耗时；供应商明确拒绝并释放预算才记录 0。
  图片已计费而归档失败不能记录 0；提交结果未知保持 waiting/待对账，不伪装失败也不自动重提。
- 排查实际供应商响应先于修改链路：2026-10-04 两个创作域 Run 已完成分镜和 Prompt，
  在 `generate` 收到 `403 AccessDenied.Unpurchased`，当时配置为 `qwen-image-2.1-pro`。
  这属于平台模型权限，不是导演 Approval；不得通过删除审核、重写 Provider 或盲目重试掩盖。

### Comic 专业审核恢复与镜头隔离（2026-10-05）

- Critic 公开 JSON 校验失败时，使用同一有界输入只请求一次结构修复；不回传非法响应或私有推理。
  仍无合法结果时，保存 `critic_status=unavailable`、`critic_result=null`、原 DirectorSpec、错误 ID
  和 `critic_unavailable/node_warning` 事件，导演 Run 等待人工确认。没有伪造 pass、confidence 或审核内容。
  完整摄影及当前 Brief/资产/镜头版本仍须有效；只有当前版本的显式人工确认允许继续制作。
  首页自动策略不能接受 unavailable，原硬约束/资产冲突门禁保留。
- 专业“进入下一步”只通过已有 Storyboard API 规划分镜；用户选择镜头后才提交图片。
  新镜头的 Prompt 版本列表为空是正常状态。图片加载、错误、重试和费用按 Project/Shot/生成请求投影，
  只放在分镜画面中；导演、资产、Prompt、历史和导航不挂图片占位。
  网络重试绑定原请求及原 Prompt 版本；显式重新生成创建新请求，保留失败记录和导演版本。
- Comic 图片日志补充估算/实际人民币费用、耗时及供应商 HTTP 状态/错误码/脱敏消息。
  2026-10-05 真实专业闭环已到达当前图片 Provider，但 `qwen-image-2.1-pro` 仍返回
  `403 AccessDenied.Unpurchased`；镜头可直接重试、预算释放且记录 ¥0.00。
  真实图片交付验收为 **BLOCKED（供应商模型权限）**，不得用离线测试图片冒充成功。

### Comic 新工作区外部图片策略（Module 1，2026-10-05）

- 新 Comic Guided Workspace 固定要求人工确认当前导演版本；进入下一步仍复用 Storyboard API。
  镜头按钮改为准备 Prompt，通过现有 Prompt 编译 API 传入 `image_mode=external` 和会话 ID。
  后端验证 Comic Guided 会话、项目归属及当前导演人工确认后，复用 PromptCompiler 和原子
  Prompt 修订 / Core Prompt Artifact 保存。编译 Run 完成，媒体派生状态为 `awaiting_external_image`。
- `external` 是工作区图片策略及编译目标，不是图片模型名称；不改变 Shot 的创作审核状态。
  准备状态只显示在当前镜头；刷新后从匹配当前 Shot / Director 版本的 Prompt Artifact 恢复。
  此入口不创建 StudioTask，不调用 gen_image、ImageProvider、QC、图片 Archive 或图片费用预占/结算。
- 未指定策略的 Prompt API 保持原 provider 目标；`comic.production.v1`、旧 Studio/CLI/API、
  首页 Fast、ConversationImageService、共享 Core/Provider/预算/审批继续保留。
  不迁移 `auto_create_image`，不新增 Runtime、Artifact 或 Workflow。上传及 ChatGPT 集成属于后续模块。

### Director Skill 知识实际加载（2026-10-05）

- 现有五个导演 Skill 的 Registry/handler 保持不变；原 `knowledge_refs` 过去只有名称，
  现在前三个模型阶段由 Comic Domain 解析实际知识文件。六份正文位于既有
  `skills/comic/director/knowledge/`，覆盖叙事、构图、镜头设计、摄影、空间连续性及灯光。
  引用由 manifest 决定；缺失、空正文、未解析引用、越界路径或超长知识明确报错，不能假报已加载。
- 同一 Coordinator 在 Comic Guided 会话（包括精简导演模式）及无会话的 Professional API 中，
  将相关知识交给原共享文本阶段适配器。正文进入 system 指令；当前 Brief、任务及固定资产
  继续作为有界创作输入，知识不覆盖作品事实或硬约束。Autonomous 首页和无会话 Fast API
  保持原模型输入。人工编辑节点不假报模型执行，Critic/确认/外部图片策略保持原流程。
- 原 Run state、阶段 Debug 和 `director_skill_loaded` Runtime Event 保存 Skill 版本、引用、
  文件路径、长度及 SHA-256，不保存知识正文或模型私有推理。重新执行的阶段读取当前文件；
  复用阶段保留来源 Run 的知识指纹，不伪造本次重新加载。日志带 trace/project/shot/skill 身份
  和 `director_skill_loaded=true`；加载错误由原 public_error 留下 error_id、trace_id 与脱敏堆栈。
- 未创建第二套 Skill、Compiler、Runtime 或 Artifact。完整外部 Image Prompt、复制、上传、
  Prompt 资产库及上传图片后的确认/视频衔接仍是后续模块，不能以本次知识接入冒充已完成。

### Director 字段来源与执行方案（Module 1.5，2026-10-05）

- DirectorSpec 的 `field_provenance` 按字段绑定值的 SHA-256、来源类型和可信状态，依据引用
  Brief/资产的 ID 与版本、实际加载的 Skill hash、应用模型请求 ID 或人工编辑的版本。
  用户约束或对应资产字段只有逐字一致才记为 confirmed_fact；模型补出的摄影、姿态、
  色彩等是 model_choice/creative_choice。方法证据说明知识进入了输入，不冒充作品事实，
  也不宣称已证明每个模型选择与知识之间的因果关系。缺值、未决参数或无请求证据记为 unresolved。
- 来源由服务端维护，客户端和修改模型不能自报 confirmed_fact。手工编辑只更新变动字段，
  保留未变依据与上一个值的指纹；指令修改及合法 Critic Patch 标记为模型选择。复审、
  重跑和恢复仍使用原 Run/版本表，兼容字段与分层决定对应，不复制一份独立方案。
- 新 Comic 导演工作区绑定 `execution_policy=single_image`。最终景别、机位、主光源、
  主光方向、色彩及人物执行字段须明确选择；视觉阶段与摄影阶段各最多收敛一次已返回的
  候选。不依据情绪填默认参数、不选第一个候选。仍未确定时保存真实草稿及具体字段，
  等待修订；排除说明、同义角度和机位高度不误当互斥候选。首页保留原策略，历史空元数据
  不改变旧审核指纹；人工编辑继承服务端策略，不能通过删除隐藏字段绕过。
- Critic 只接收一份分层决定、必要 Brief/资产/镜头上下文与去重后的字段依据，不再重复
  扁平兼容字段和完整 public_decision。编辑模型也只读取精简来源，不能重写来源元数据。
  应用请求 ID 关联阶段 Debug 与日志，区别于供应商 response ID；实际供应商 ID 在实测
  Trace 中另行记录。Critic 的一次结构修复/unavailable 恢复及版本绑定的人工确认保持原流程。
- 本模块未增加 Skill、Registry、Agent、Runtime、Workflow、Artifact 或依赖；不修改 UI、
  Prompt Compiler、Canvas、图片上传或 Video。真实文本实测在隔离数据库执行，另行授权
  上限，记录用量与配置价格估算，不把估算称为最终账单，也不自动代表用户完成方案验收。

### Comic Prompt Production Pipeline（Module 2，2026-10-05）

- Comic 新工作区保持 `external` 策略及导演人工确认。确认后规划分镜，在当前镜头调用
  原 Prompt Compiler 的 `complete_prompt=true`，输出 Character / World / Style Bible、
  Shot Direction 与 Negative Constraint。默认 `false` 保持首页及旧 API 的输入兼容。
  未绑定资产的外貌、服装等是可编辑的模型选择，不冒充用户事实。
- Compiler 读取当前 Brief、固定镜头资产版本、已确认的 DirectorSpec 和已有字段来源。
  相同角色资产的镜头复用已保存身份 Bible，同场景/风格沿用对应内容；不会跨项目或角色
  继承。镜头局部景别与机位优先于导演总体摄影，整体色彩与主光继续来自导演方案。
  模型请求、Brief/资产版本及 hash、导演 Skill 方法证据、复用 Artifact 和人工编辑来源
  保存在既有 Prompt 修订与 Core PROMPT Artifact。方法证据不等同逐字段因果证明。
- 工作区可以预览、编辑、复制和导出完整正向正文与负向约束。Prompt 资产库管理项目镜头、
  历史版本、差异及来源；编译入口只在分镜。确认 Prompt 仅记录当前内容指纹，不涉及费用审批。
  编辑与恢复生成新修订，不能覆盖历史、删除固定资产事实或继承旧版本确认。
- 用户外部图片上传只登记文件到 Core IMAGE Artifact，关联 Conversation/Project/Shot/
  Prompt 版本和 trace。支持 PNG/JPEG/WebP，文件不超过 5 MB；仅该 Comic 导入路由允许
  7 MB JSON 请求，其余路由保持原大小限制。上传验证版本并保留原 Shot 的创作状态，
  媒体投影为 `image_uploaded`；不代表视频已实现或图片由内部模型生成。
- 已写的内部单镜头请求 DTO、服务按模型选择与生产日志扩展保留为 **future capability**。
  模型选择须显式 `quick_creation.future_image_model_selection`，不是默认路径；没有新增
  Comic 图片生成路由，新工作区不调用 Image Provider、图片预算、Task Center 或生产工作流。
  原生产流程、首页、Commerce、Core/Skill/Director/Critic 及全局导航保持兼容。

### Comic Prompt 链路治理（Governance Module 1，2026-10-06）

- 当前 Comic 仅复用 DirectorSpec → 原 PromptCompiler → Core PROMPT Artifact；旧 Persona/
  Recipe、首页及 Studio 入口兼容保留，不能复制成新的 Prompt 生成系统。
- Compiler 修订 `comic-prompt-3` 保持旧模式兼容，完整模式保留 v1 导演有效字段、解析
  Shot/导演执行要求、使用自然语言正文，并在原 context_sources 中记录输入 lineage 和
  各段 input_basis。Skill 方法是输入证据，不能称为事实或逐句因果证明。
- 完整模式采用同一 Domain 文件中的字段白名单：Character 身份/外貌/年龄/服装/
  固定特征，World 地点/时间/世界观/环境，Style 艺术风格/摄影风格/色彩/材质，Shot
  动作/姿态/构图/镜头/光影变化。模型错放的已识别分句原样归入 Shot，在
  `scope_relocations` 记录原字段、目标字段和内容 hash；归位后仍须严格 Schema 校验。
  不用模型重试、固定摄影映射或另一套 Bible 存储。
- 导演镜头决策在 Compiler 请求的 `shot_direction` 中，不作为稳定身份输入。同一
  固定角色/世界/风格复用最新合格 Prompt 修订；旧自由段落仍可读但不自动复用。
  人工编辑/恢复须遵守同一字段标题及校验，不能通过删去契约标记降级；固定资产
  不静默清洗。自然语言分句识别有边界，最终复制内容仍须人工审阅。
- 原 Prompt 确认/外部上传投影使用受约束只读查询，保留同一 Run/Artifact、版本和指纹；
  不新增表、缓存或 Core 迁移。JSON 条件仍可能扫描数据库，不承诺索引级查询性能。
- [本模块报告](../architecture/prompt-governance-module-1.md) 保存检查、真实文本及 QA 编辑
  证据；自然语言仍须人工审阅。真实文本通过不代表外部图片质量通过，验收后再提交。


## Runtime Storage 治理（2026-10-10）

2026-10-14 故事编排与导出材料见[模块三接口与验收](../architecture/comic-backend-module-three.md)：同聊天跨作品明确选入已采用图片，服务端固定版本故事、核验确认、历史恢复与原始图片 ZIP；不接入视频生成。原累计预算账本缺失时禁止新增付费实测。

2026-10-12 后端图片观察/外部改图模块见[模块二接口与验收](../architecture/comic-backend-module-two.md)：显式分析、选中发现项、原 Compiler 编辑模式、不可变指令确认、外部结果追加、实际两图对比与人工版本选择。上传和 GET 不调用模型；观察不升级为用户事实。没有自动内部生图、视频或新增 Agent/Runtime。前端尚待接入，不能把后端接口实现描述成页面已可用。

测试路径以[运行存储规范](runtime-policy.md)的 2026-10-12 修正为准：pytest、离线及付费验收均使用项目外的自有会话，结束删除图片副本/报告/日志/隔离数据库，公开报告输出终端。只有必要累计费用 audit 保留；账本缺失拒绝后续模块付费，不能归零重开。下面旧阶段报告路径是历史记录，不代表新验收继续积累文件。

运行文件统一通过 RuntimeManager 与 DatabaseManager。目录、容量、过期、旧数据保留和正常退出边界见[运行存储规范](runtime-policy.md)，本节优先于旧章节中的 data/logs 和 data/qa 路径描述。标准检查命令 `python -m kantoku doctor` 默认只读。离线验收脚本和 pytest 使用 tmp/sessions 的独立运行环境并在退出时清理；旧验收目录保留。付费导演验收的账单/审计资料放在 data/paid-qa，不能当普通临时目录回收。

## 动态会话上下文（2026-10-11）

本节替代历史阶段记录中“同一会话默认继承”、固定词重置及无条件继承摄影的行为说明。当前自然语言轮次复用 `ConversationContext`、原 Conversation/Run/JSON Artifact 与共享文本出口；没有第二套 Agent、Runtime、向量库或 Provider。

- 普通对话与独立创作方案使用同一预算、近期原文、任务状态、摘要和范围内历史读取机制。每轮语义结果决定目标、当前任务、引用及变更；检索字符串和最新方案均不是继承规则。多个任务分别保存目标、有效/撤回约束和未完成事项。
- 理解输入将近期原文按原用户/助手角色顺序传入，并标注消息来源；当前请求单独置于最后。记忆 JSON 只提供摘要、状态和来源索引，避免将旧任务或旧判定误作当前请求。历史 system 记录作为带角色标记的资料，不能提升为本轮 system 指令。
- 入口注入可用动作，Core 不保存领域动作词表。原任务中已经核验的事实可按相同来源继续使用，避免每轮重读全部约束；新事实必须核验原文，撤回/替代事实恢复必须有新的用户依据。资产来源存在不等于模型补充的描述也是资产事实。
- 历史读取限定当前 Conversation，核验 Run/Artifact 归属、不可变导演 ID/版本和本轮消息锚点。项目资产由原领域存储核验；显式选中的资产继续受保护。新聊天不自动加载其他聊天，也不建立用户长期偏好档案。
- 只有本轮选中的来源字段参与创作锁。支持整类设定和公开 `director_plan` / `cinematography` 叶子字段；多个方案可分别贡献字段，重叠引用报错。未选中的模型摄影、姿态、风格等交还本轮模型选择。用户明确事实、资产事实和模型选择分别标记；审核不依据创作动词或字段变更静默丢弃有效硬约束。
- 关键歧义返回短澄清，回复仍读取原请求及任务状态。模型解析或来源核验失败保留原消息，返回现有 error/trace；不默认猜测继承。导航和异步完成不能改变已保存的本轮锚点。
- `runtime.context_token_budget=18000`、`context_summary_tokens=2000`、`context_recall_tokens=6000` 可配置，摘要/召回上限同时受总预算约束。token 使用字符估计，不等同供应商 tokenizer 的精确用量。领域 Skill 指令另属原领域输入。
- 理解、压缩和最终聊天输入均在发送前检查估计容量；近期窗口同时预留 Schema、任务状态、摘要和召回空间。超出本轮锚点的消息读取明确拒绝。无效模型 JSON 的错误只记录字段路径和错误类型，不将原始非法输出或私有字段值写入异常日志。
- 容量不足时按消息范围增量压缩。摘要保存在内部 JSON Artifact，包含版本、父摘要和覆盖范围；不产生逐条消息文件，不列为用户交付资产。原文与历史版本完整保留，摘要可通过范围读取核对。
- 摘要预算约束整个 JSON，超过容量只允许一次进一步压缩；仍不满足则保留旧摘要与全部原文并返回可定位错误。来源读取明确区分单条 `id`、排他性的前后范围及范围内原文查询；本轮消息可以作为范围锚点，未来消息仍拒绝。
- `GET /api/conversations/{id}/messages` 增加 `limit`、`before`、`after` 和重复 `id` 范围查询，返回 `next_before`。原详情接口保持兼容。
- 导演独立方案请求可选 `request_id`，新客户端发送唯一 ID；重试读取原 Run 或澄清/聊天结果并核验原输入版本。已认领但结果未绑定的请求明确报错，不自动再次调用模型。既有编辑、恢复、审核仍绑定其原请求与版本。

验收区分工程替身测试和真实模型语义质量。120 轮压缩/召回与实际模型输入检查见 `tests/test_conversation_context.py`，截图、多来源和部分字段引用见 `tests/test_dynamic_director_context.py`。运行文件测试只针对可证明归属的项目目录，不能将此前回收站 16 GB 全部归因于本项目。真实付费语义验收仍须按本手册确认预算。

真实上下文验收复用 `scripts/verify_director_pipeline.py --suite context --allow-paid --max-cny 2`；默认仍是原四类导演验收，不带 `--allow-paid` 立即拒绝执行。上下文套件使用 112 条明确标记的人工 QA 历史，执行八轮真实理解/压缩/召回，检查续写、换题、纠正、比较、部分引用、早期召回、关键歧义和跨聊天隔离。它不是 112 轮真实模型对话，也不执行图片/视频生产。报告保存实际公开上下文、来源、用量和配置价格估算；未知用量或额度不足停止，不重试或切备用模型。审计和独立数据库保留在 `data/paid-qa`。

当前默认路径增加逐轮文本理解，以及需要时的历史读取和摘要调用；仍走原共享文本出口和日志，不表示这些调用免费。没有新增依赖、Agent、Runtime、向量库或业务表迁移，没有清理系统 Temp、回收站、旧 QA 或历史业务数据。新增 Core 文件用于领域无关的会话记忆；新增两份测试验证消息预算/来源及实际导演输入，未创建平行业务服务。旧前端 bundle 由同一源码正常构建替换。

2026-10-11 验收记录：完整后端回归 987 项通过（最后的原生对话角色调整前）；调整后的相关后端 90 项通过，最终上下文专项 20 项通过，包含新增本轮锚点读取检查。前端 116 项通过，类型检查/构建、ruff、结构检查及标准后端 HTTP 启动通过。隔离环境 20 次正常启停后 SDK 暂存与 SQLite 侧文件均清理；30 分钟空闲运行未产生失管新文件，日志等已登记内容增长 238960 字节。最后追加的完整回归按用户收尾要求中止，只清理该次已停止进程的自有测试目录，旧会话/验收/业务数据保留。

真实模型语义验收 **尚未通过**：低容量摘要、短指代判定和读取契约暴露了上述改进点，不能用离线结果代替语义结论。最后一次实测在 `deepseek-flash` 返回 HTTP 402 / Insufficient Balance 时停止；用户随后要求减少测试并收尾，未再付费调用，也未切换模型。九次隔离尝试合计保守估算 0.785136 元（包含最后一次用量未知的预留，不是最终供应商账单）；实际请求消息、公开结构结果和用量保存在对应 `data/paid-qa/context-live-*/report.json`。最终阻断报告位于 `context-live-54f721e27c6f4b5f8ca832f09fc305c2/report.json`。后续真实语义验收须根据剩余额度明确授权，仍不得自动重提用量未知的调用。

### 后端记忆可靠读取修复（2026-10-12）

- 当前链路为理解目标 → 系统补读来源 → 核验并重新理解 → 按本轮需求继承 → 保存任务状态。`available` 只保存有界来源目录，`recalled` / evidence 只保存已读取的内容；模型漏填 `reads` 时，系统仍会补读 `sources`、新增事实和展示产物的依赖。已读来源不重复读取，第三轮补读实际执行，另留一次最终理解；仍缺少依据则返回澄清，不把读取前的猜测保存为生成结果。
- 会话理解和压缩调用复用共享文本出口，但在本轮 ContextVar 内设置 `single_attempt=true`，结束或失败后恢复原策略；不隐藏重试、切换模型或重发未知调用。来源归属、消息锚点、不可变版本仍严格核验。读取后 Run 的 Project / Director ID / version 发生变化也拒绝继承。
- `SourceRef.kind` / `HistoryRead.kind` 增加通用 `resource`，通过入口注入领域读取器，Core 不引入业务依赖。Comic 资产引用使用 `asset:<project_id>:<asset_id>:<version>`，是原资产修订的读取身份，不建立第二份资产存储。只索引本轮关联项目；已实际读取并记入同一会话内部 Context Artifact 的历史资源可以按原修订重新核对，其他聊天不能借此读取。
- 独立方案由语义结果定位原任务、选择继承字段和有效用户约束；领域通过已有 Context Builder 注入人物/世界/风格的续写原则。默认动态路径不调用关键词规则补判继承。旧兼容入口保持原契约。整类设定与叶子字段在多个来源中重叠时返回短句澄清；新任务不会借用旧确认。
- Run 中保留完整解析与请求审计，导演阶段只收到本轮解析、当前任务的 active 事实及选中字段，不把其他任务、撤回事实或完整请求信封交给创作模型。未选来源的正文也不进入最终聊天装配。摘要与近期窗口可以裁剪，必需来源和解析结果不能为掩盖容量超限而删除；理解、压缩及最终输入均检查估计 token，仍放不下时保留原记录并返回可定位错误。
- 资产目录提供名称、别名、类型和简短描述；名称匹配只调整目录顺序，选择由模型根据本轮目标判断。选择后读取正文并核验版本与固定约束，显式引用优先，锁定修订不漂移。目录截断后不能用不完整候选默选资产。动态路径关闭名称/别名与唯一风格的隐式继承；旧 `select_relevant` 和只读 Context API 默认行为保持兼容，不增加查询时的模型调用。
- 本次复用原 Conversation、Run、Artifact、资产修订、日志和 RuntimeManager；无新增依赖、业务表迁移、Runtime、Agent 或业务文件。后端与 WorkBuddy 的前端工作分开，没有修改前端源码、样式或构建产物。源码留待本轮人工验收，不混入其他未提交改动提交。

验证：`uv run --no-sync python -B -m pytest tests/test_conversation_context.py tests/test_dynamic_director_context.py tests/test_comic_assets.py tests/test_comic_context_continuity.py tests/test_web_studio.py` **209 项通过**；改动文件的 ruff、结构检查和差异空白检查通过。标准 `python -m kantoku serve --no-browser` 在 RuntimeManager 创建的独立临时库与空闲端口启动，HTTP 返回 200；停止后该隔离库没有 WAL/SHM 遗留，SDK 暂存目录已清理。未运行前端构建或付费模型；工程替身测试不能代替真实语义质量验收，后续仍复用原付费验收脚本并单独授权预算。

### 完整 Prompt 约束补全与用户气泡合并（2026-10-12）

- `ERR-9417A5A7` 对应文本调用成功后的约束校验失败；当时 Brief 和 Director 的同一句用户原话重复计为两项缺失。原 Compiler 现在向模型提供去重的 `required_constraints`，完整模式明确要求原文进入四段 Context 的正文，由系统装配正向 Prompt。仍严格拒绝缺失约束，不把摘要或负向正文当作正向依据。
- 缺失约束错误增加原编译请求 ID、缺失清单编号和数量，不把用户原文放入错误日志。外部完整模式复用既有一次修复入口，成功收到文本但缺失结构或约束时使用同一来源快照补全一次；仍缺失则停止保存。供应商异常及结果未知不进入此修复。旧模式和人工编辑的不可变约束检查保留。
- 用户另行允许本次小范围前端显示修复。`DirectorWorkspace` 在提交前保存原 request_id，以持久化消息的 event_id 识别本轮临时气泡；Run 已出现但消息尚未绑定时，使用原 `creative_context.input_message_id` 合并。不会按文本相同删除不同请求，不改变用户历史或 WorkBuddy 的样式、导航、构建产物。

本轮验证：完整 Prompt 回归 **47 项通过**；旧 Prompt、上下文续写与动态上下文组合测试初次 **107 项通过、1 项报错文案断言失败**，恢复原结构错误措辞后，相关结构修复与新约束修复专项 **6 项通过**。气泡合并及提交相关前端行为测试 **5 项通过**，前端类型检查、改动 Python 文件的 ruff、结构检查及隔离标准启动通过；启动 HTTP 返回 200，正常停止后无 WAL/SHM 或 SDK 暂存残留，自有验收目录已清理。导演工作区整组前端测试另有一项旧断言禁止标签页，与 WorkBuddy 当前流程导航冲突，未扩大本次范围修改。未运行前端构建、未调用付费模型、未改写生产数据；部署须重启后端并由正常前端构建更新页面。

### 作品与画面任务工作区（2026-10-16）

新建作品采用“一个作品的连续对话 → 每条新创作独立制作 Project”，只有故事编排与导出材料在作品内共享。后续动作、表情和镜头续写仍是新方案的首次生成；只有明确修改指定方案才进入 USER_EDIT。每次首次交付都经过原导演内部校验与有界修正，修正失败保留候选并禁止确认，不以隐藏审核建议代替内容修复。历史列表一作品一行，复用搜索、重命名和软删除。旧任务对话与逐 Brief 故事规则仅兼容读取。接口、恢复来源及本轮验证见[工作区身份契约](../architecture/comic-work-centered-workspace.md)。静态页面由当前源码重新构建；后端部署需加载当前源码。真实模型语义验收须另行明确预算，离线测试不代替真实质量结论。

### 首次生成必定交付与对话页加载（2026-10-16）

首次生成门禁与加载热路径修复，不新增 Runtime、Provider、数据表或依赖。

- **首次生成交付门禁**：`coordinator.py` 的 `generation_blocking_findings()` 取代“Critic verdict 必须为 pass”的判定。只有错误级结论，或与用户请求/硬约束/固定资产真实冲突及摄影不完整的结论（`GENERATION_BLOCKING_CODES`）才阻止把候选提升为当前导演稿。视觉因果、色彩、构图、质量词等优化建议随方案一并交付，真实 `critic_result`（含 `needs_revision`）原样保留，不伪造 pass。根因证据：截图作品 `comic-project-4be5fc53…` 的两次首轮 Run 均只有 `VISUAL_CAUSALITY_MISMATCH` warning / `INVALID_PATCH` info，却被旧门禁判为 `generation_incomplete`（ERR-B9DF288E）。
- **审核暂不可用**：作品新方案在 Critic 不可用（`critic_result` 为 None、`stage_failures` 仅含审核阶段）时保存为标注 `critic_status="unavailable"` 的草稿，`can_confirm=true` 但 `user_confirmed=false`，生产仍须显式人工确认；生成阶段（理解/视觉/摄影）失败仍按原语义判为未完成。该放宽仅作用于 `creative_context.workspace_work_id` 存在的作品新方案，非作品路径判定不变。
- **有界重试**：作品首次生成（FIRST_GENERATION/NEW_CONTEXT）的三个模型阶段失败时经 `_run_generation_stage()` 重试一次，并记录 `director_generation_stage_retry` 事件；人工修改与复用阶段不启用，保持原失败语义。
- **加载热路径**：`RuntimeStore.list_runs()` 增加 SQL 侧 `project_id`/`project_ids` 过滤；`list_comic_project_tasks()` 与 `workspace.conversation()` 不再取全表 Run 再在 Python 逐个解析 `state_json`。本地库实测单次由 347 行 / 9.9 MB / 190 ms 降到 21 行 / 217 KB / 42 ms（约 4.5×）。
- **前端轮询与取数**：`DirectorWorkspace` 的固定 2 秒全量 `refresh()` 改为自适应（任务运行 2 s、空闲 20 s、页面隐藏暂停、可见时立即补一次），并复用列表已有作品、并发拉取作品上下文，产物图片改为后台加载、不阻塞首屏。
- 验证：`test_comic_workspace.py` 新增 `test_first_work_delivery_succeeds_with_advisory_only_findings`（撤掉修复即失败）；相关后端组合通过，仅剩 5 项既有失败（用原始门禁表达式复现，确认与本次改动无关）；前端 169 项通过，`vue-tsc`、ruff、`check_structure.py` 通过；`python -m kantoku serve` 正常启动。未调用付费模型。

### 首次交付补全与首屏解阻塞（2026-10-17）

本节补充并替代上节“生成阶段仍按未完成返回”的作品首轮行为。修改限 UI、Shell 请求组装和原 Comic Domain；复用原阶段 Schema、Coordinator、Run、版本、日志与人工确认，无新增业务文件、依赖、数据表或执行系统。静态交付文件由原前端构建更新。

- 加载根因：正文曾等待上下文、全组任务/Run、素材、故事和侧栏列表的串行链路；同键 GET 互相取消会返回 null，旧逻辑继而清空消息。消息现在独立读取并提前显示，同选择共享在途请求，晚到/取消响应保留当前消息。首读复用作品/context，缓存终态 Run，故事只在故事/导出入口读取；已知路由不等待侧栏历史。刷新锁绑定选择与 Promise 身份，不再因切换聊天永久占用。浏览器进一步确认 App 的全局 Core 列表每 4 秒与工作区重复取数（首次含全库历史扫描）；Comic 工作区现在暂停该列表及全局 timer，离开立即读取并恢复原周期，旧响应按导航版本丢弃。
- 首次展示根因：提交后使用发送前的作品快照打开新 task，导致后端成功却显示任务不存在。现改用刷新后的作品列表定位新任务。
- 首轮恢复：仅作品新方案使用单轮 90 秒模型等待预算、单次最多 30 秒，并通过原共享文本出口关闭叠加的供应商重试/备用切换。上下文模型传输/结构异常保留本轮完整原文并建立独立草稿，不猜测旧来源；原来源归属、版本与容量核验继续生效。约束提取不可用时使用已有逐字约束兼容契约（版本为空），保存全部有效用户原文及退化诊断，不伪造语义审核。
- 原生成阶段对坏结构最多补一次；超时/网络结果未知不再次付费提交。摄影 missing 不再绕过恢复。持续失败时在原 Domain 生成符合 v2 Schema 的本地可编辑预案；实际冲突修正仍失败时保存原候选/审核诊断，再替换为明确标注的本地稿。纯艺术建议直接交付完整原稿，不为建议额外重跑模型。
- 本地稿公开 `generation_source=local_fallback`，状态文案与方案均标注本地预案，来源不含虚构模型请求。旧审核与新内容解绑，`critic_status=unavailable`、`critic_result=null`、`user_confirmed=false`；仍须显式人工确认。完整原始需求、所选 Bible/叶子字段和资产引用保留，默认摄影选择不冒充用户事实。人工修改、其他业务及旧非作品入口保持兼容。
- 验证：作品/Coordinator/草稿 60 项，质量/动态上下文/会话记忆/摄影 109 项，creation mode 29 项，HTTP 边界 2 项均通过（共 200 个相关用例）；旧模式两项状态文案断言与摄影历史指纹夹具的新增空字段已对齐现有契约，未改变生产审核哈希。前端全部 181 项通过（工作区与外壳 70 项），类型检查与构建通过；ruff、结构检查、差异空白检查通过。标准启动 HTTP 200，正常退出无 SQLite WAL/SHM。
- 隔离真实 HTTP/SQLite 浏览器验证首次发送、新 task 打开、进入完整导演方案和刷新恢复，模型使用离线替身；未调用付费模型、未改写生产数据。故障注入证明已覆盖路径可交付结构完整草稿，不表示任意自然语言的艺术质量、矛盾输入、数据库不可写或进程中断也能无条件成功。部署需重启后端并加载本次已重建的静态页面。

### 每条创作消息首次编译必定出指令（2026-10-17）

修复“第二条及以后创作消息首次生成导演方案时在‘完成生成 Prompt’环节报错”。修改限 `comic.prompts`、`comic.constraints`、`comic.coordinator` 与 `web_studio` 原链路，复用既有 Draft/Schema/审核/版本/Artifact，无新增业务文件、依赖或数据表。

- 现场：作品 `comic-project-a09f2332…` 的第二条创作消息 → 子 Project `comic-project-3613c730…`，Run `run-9c36d46be931…`（`comic.prompt.compile`）报 `ERR-A2EE89E4`，用户看到“本轮要求未获得有效证据，尚未生成指令”。
- 根因：该轮 4 条约束中 `一脸充满爱意得看着镜头`（`scope=shot`，target 为面部表情/目光）未被正文承载——`ShotPromptContext` 没有表情字段，该轮 `execution_emotion` 为空，编译器把表情写丢，独立复核如实判 `uncertain`。旧逻辑只有一次模型重编，第二次仍缺项即抛出，整条 `prompt.compile` 失败。
- 三阶保障（`StructuredImagePromptCompiler._verify_constraints`）：① 首轮核验未覆盖 → 带公开 `issues`/`candidate` 重编一次（`content_repair`），其余已成立设定保留；② 仍缺项 → 服务端按 `scope` 把该要求写进既有字段（`CONSTRAINT_SECTION_FIELDS`，表情/神态/目光/情绪沿用 `shot_context.pose` 的“神态与情绪：”约定），并同步重建 `positive_prompt`（保存以 `positive_prompt` 重建四段，两者必须一致）；③ 复核仍存疑 → 以 `delivery="advisory"`、`unresolved=[…]` 如实交付，`review` 结论原样保留，不伪造 pass。
- 覆盖所有创作入口：`compile()` 是唯一实现，作品首轮、第二条及以后（`_continue_comic_shot`）、直连 `prompt/compile`、内部生图（单段正文）共用；删除 Shell 层重复的语义修复分支，只保留结构补全。
- 同类死路一并消除：`exact` 原样文字缺失与 `scope=shot` 表情遗漏都走同一补齐通道，不再出现“要求没写进正文→整条编译失败”。协调器的“审核不可用”判定保持原作用域（作品新方案），未改动直连创建路径的等待/确认语义。
- 保守边界：供应商超时/结果未知仍判失败且不重复付费提交；改图（`compile_edit`）仍按原范围核验，不放宽。
- 验证：`test_three_creative_turns_…` 扩到 5 种故障模式（正常/一次修复/持续遗漏/复核始终存疑/超时），持续遗漏由“必须报错”改为“补齐后交付”，新增 advisory 断言；新增 `test_missing_requirement_is_projected_into_matching_section`；用真实失败现场数据回放，表情要求已落到 `姿态` 行且 `positive_prompt` 与四段一致。未调用付费模型。

### 图片工作台三处界面修正与导入图片删除（2026-10-17）

修改限 `ImageOptimizeStage`、`ComicImageImport`、`DirectorWorkspace` 三个原组件与 `comic.media` / `web_studio` / `core.ts` 原服务链路，复用既有 `comic_entity_versions`、Core Artifact 生命周期与 Run 追踪，无新增业务文件、依赖或数据表。

- **框选提示不再压画面**：`.wb-drawing-hint` 原先绝对定位横跨画布顶部，图片满屏时盖住画面内容。改为画布之外的 `.wb-draw-status` 一行状态（视图栏下方、仅框选阶段出现），主题色浅底胶囊，与 `.wb-auto-pill` 同一套语汇；画布内不再有任何常驻或临时浮层。
- **删除已导入图片**：导入行的 `ComicImageImport` 新增具名 `actions` 槽，组件不猜业务语义；`ImageOptimizeStage` 在其中放「删除图片」。点击先弹 `wb-import-confirm` 同款确认面板（列出后果），确认后调 `POST /api/comic/projects/{project_id}/images/{image_id}/delete`。后端 `ComicMediaStore.delete_image()` 走作品级 CAS 与 `_comic_media_action` 幂等，一次删除该图片的全部版本、采用指针与逐版本改图草稿，作品修订 +1；图片产物转入回收站（可恢复/purge），不在此直接删文件。
- **两条硬引用必须先拒绝**：故事编排已固定的图片（`story.fixed_images`，含已排进 `story.shots` 的）与正作为参考资产使用的图片（`image_references.bindings`）一律拒绝，否则下游故事材料与身份/风格参考会失去来源。图片“正在当修改基础”不阻止删除：工作台按 `families` 重新选图，基础图与草稿自动回退。
- 顺带修正 `ComicMediaStore.get()`：图片已删除时不再把内部产物 id 泄露成“找不到指定 Artifact”，统一回答“找不到指定图片版本”。
- **去掉无用途信息**：右侧面板标题不再重复「文件名 · vN」小字（`.wb-base-name` 一并删除）；`DirectorWorkspace` 的「节点详情」（顶栏与图片流程条两个入口、右侧 `workspace-inspector` 面板、`inspectorOpen` 状态与取事件副作用、`events`/`runHistory` 及相关样式）整体移除，执行历史仍在「真实执行记录」与版本历史里可查。
- 验证：`tests/test_comic_image_delete.py` 6 项（删全部版本并回收产物、不动其他图片且只记一次作品修订、故事固定拒绝、参考资产拒绝、过期版本与同 request_id 重放、HTTP 路由），Comic 相关后端组合 179 项通过；前端 183 项通过，其中新增「删除需确认」「框选提示在画布之外且标题不再重复文件名」两项契约；`vue-tsc`、`npm run build`（已更新 `src/kantoku/shells/web/`）、`check_structure.py` 通过。未调用付费模型、未改写生产数据。

#### 修正：框选区域不再静默失效（2026-10-17 补）

现场（用户截图 + 真实库 `comic-project-c2ed96ce…`）显示：视图栏是「查看 v2 · 已采用」，而持久化的工作台 `image_workbench` v45 仍是 `image=v2 / edit_image=v1`，于是 `onBase=false`，「框选区域」长期处于禁用态；点「以 v2 开始修改」后，v2 的改图草稿又停在 `phase=review`（上一轮指令已整理），同一判定 `draft.phase !== 'editing'` 继续把按钮锁死。两个条件任一为假都表现为"点了没反应"，而唯一的解释是右侧那行把文件名和版本混在一句话里的小字。

- 根因一（可用性门禁过严且无自恢复）：`框选区域` 的 `:disabled` 同时要求 `onBase` 与 `draft.phase === 'editing'`。现在改为只保留真实不可用的条件（无当前图、图未解码、比较视图、操作中），把"版本不一致 / 指令已整理"交给点击时的准备逻辑处理。
- 根因二（提示位置与措辞）：上一轮删掉了标题旁的 `文件名 · vN` 胶囊，但同一信息以「本轮修改基础仍为 v1（…png），中央正在查看其他版本。」的句子留在下一行 —— 既是"没修好"，也让唯一线索变成位置错、措辞含混的一段话。该行已删除。
- 改动：新增 `toggleDrawing() / ensureEditable() / startDrawingFor()`。点击框选时若正在看另一版，先 `makeBase(current)` 以该版为修改基础（与「以 vN 开始修改」同一语义，状态照常持久化），若草稿处于 `review` 则退回 `editing` 并保存，然后进入框选；「框选绑定／重新框选」两个入口走同一条准备逻辑。基础版本改为在视图栏标注（`查看 v2 · 已采用 · 修改基础 v1`），不再重复文件名。
- 验证：`frontend/tests/comic-image-review.test.mjs` 20 项通过，其中新增「查看版本与修改基础不一致时框选仍可用（并写入 `edit_image=当前版`）」「指令已整理（phase=review）时框选仍进入框选」，原两条断言改为核对视图栏的 `修改基础 v1`；全量前端 184 项通过，`vue-tsc`、`npm run build`（产物已更新）通过。后端无改动。

#### 修正：分析结果按内容归属，删除后的图片不再复活（2026-10-17）

现场：同一作品里两张内容不同的图片，右侧「分析建议」给出**完全相同的 5 条**。真实库 `comic-project-c2ed96ce…` 的证据链：`image_workbench` / `image_review_draft(:2)` 指向 `artifact-f7431dd18f674`，而该报告的 `basis.images[0]` 是 `1d6ac37d…png`（sha `f5f6cc25`），当前 v2 的内容却是 `941cedf8…png`（sha `1e5bc136`）——**旧内容的分析被当成新内容的分析显示**。

- 根因（两段叠加，均由上一轮"删除已导入图片"引出）：
  ① `delete_image` 只回收了 `comic_entity_versions` 里各版本的产物，未回收以 Artifact ID 充当 `image_id` 的旧分镜上传记录（v1 的只读投影）。删除后该图片下次读取又"复活"成 v1，新导入的图被追加成它的 v2，版本号因此被复用。
  ② 报告列表只按 `(image_id, version)` 过滤。版本号一旦复用，旧版本号上的报告就会被匹配到新内容上；前端 `makeBase` 的兜底（按 task 匹配）与按 id 兜底读取也都只看版本，不看内容。
- 改动（不新增表、不改报告契约）：
  - `ComicMediaStore.delete_image()` 额外回收该家族以 `image_id` 为 id 的旧产物（校验 `metadata.project_id`），并支持删除"只有投影、没有版本行"的旧图片；删掉后 `get()` 不再投影出 v1，追加版本的旧路径会明确失败，新图只能成为独立图片。
  - `ComicImageReviewService.list()` 在给出 `image` 时追加内容指纹条件 `json_extract(payload_json,'$.basis.images[0].sha256')=?`，分析、改图指令、对比三类报告统一"只属于它真正算过的那份字节"。
  - 前端 `ImageOptimizeStage` 新增 `fromThisContent()`：草稿里的 `analysis_artifact_id`/`instruction_artifact_id` 若指纹不一致就当没有报告，并清掉指针落盘；`ComicImageView.sha256`、`ComicImageBasis.images` 补进类型。
  - 顺带修好同类错位：切换「图片」下拉（换的是另一张图而非另一版）现在一律 `makeBase()`，右侧草稿/分析/指令跟着换，不再沿用上一张图的内容。
- 保守边界：不跨版本复用报告——相同字节若以别的版本号分析过，仍要求重新分析，因为 `edit()` 以完整 basis 哈希（含版本）校验，跨版本复用会在"整理改图指令"时才失败。
- 验证：新增 `test_reports_are_scoped_to_content_not_to_a_reused_version_number`（把 v1 报告伪挂到 v2，列表必须为空，v1 自己仍可取）、`test_deleting_a_legacy_image_trashes_its_artifact_so_it_cannot_reappear`（删除后 `list` 不含它、产物为 trashed、`get` 报"找不到指定图片版本"、追加版本失败）；前端新增「另一份内容字节的报告不得显示为本图分析」「切换图片加载该图自己的草稿与分析」两项契约，并把测试桩的草稿键改成 `image_id:version`（与生产一致）。图片相关后端 83 项、全量前端 186 项通过，`vue-tsc`、`npm run build`、`ruff`、`check_structure.py` 通过。用真实库只读回放：v1 → 1 份（3 条，属它自己），v2 → 0 份（旧的 5 条不再冒充），前端会在下次打开时清掉失效指针。

### 故事编排：AI 初稿吃已填内容，校验可定位到镜头字段（2026-10-17）

修改限 `comic.stories`、`comic.models`、`web_studio` 原链路与 `ComicFlowStage`/`storyWorkbench`/`core.ts`，复用既有 Story 版本、Review、Confirmation 与导出契约，无新增业务表、依赖或平行实现。

- **AI 整理初稿不再只看「本轮故事要求」**：`ComicStoryGenerateRequest` 新增可选 `draft`（标题/概述/镜头快照），`instruction` 改为可选；只填了标题、概述或镜头也能整理。模型收到 `current_draft`，服务端再用 `_merge_user_draft()` 兜底合并——用户已写的标题、概述与每个镜头的动作/运镜/时长/转场**原文保留**，只补空缺，图片与镜头顺序不被改写。`instruction` 为空且没有任何框架时才拒绝（前后端一致）。
- **初稿力求一次过**：生成后先跑确定性结构检查，再有界修复一次（`_repair_once`，只改被指出的字段，仍不合规就退回原稿，不连环重试）；模型复核判 `needs_revision` 时同样只修一次，按 pass > warning > needs_revision 取更好结果。
- **校验先确定性、再模型**：`_structural_findings()` 直接产出「镜头 N · 字段 · 错误类型 · 修改建议」，缺镜头/缺动作/缺运镜/缺时长都不必花模型调用；`ComicStoryFinding` 增加 `code`（11 类错误类型，未知值归 `other`）与 `suggestion`，模型负责内容一致性并同样给出这两项。
- **校验结论按内容复用**：`_review_current()` 在 `content_sha256` 与校验模型都未变时直接沿用上次结论（`review_reused=True`），所以「AI 初稿未修改就点校验」不会因为模型抖动而翻车；一旦用户改动，workspace 的 `view()` 因 hash 不匹配不再带旧 review，确认按钮随之关闭，必须重新校验——这就是「修改后重新校验」的服务端依据。
- **确认门禁**：`confirm` 先看校验结论再校验字段完整性，提示语统一为「当前故事尚未通过校验，请先校验或修改后重新校验」；接口与按钮文案统一为「校验 / 确认当前故事 / 进入导出材料」。
- **前端**：`AI 整理初稿` 在有已填镜头时可用（按钮写明「按已填 N 个镜头」），请求携带 `draft`；校验结果按「镜头 · 字段 · 错误类型 → 问题 → 建议 → 依据」渲染，新增 `storyFieldLabels`/`storyCodeLabels` 统一中文标签。
- 验证：`test_comic_stories.py` 31 项通过，新增「生成沿用已填框架且不改写用户原文」「校验指出镜头/字段/类型/建议且不调用模型」「内容未变复用结论、不再付费调用」「AI 初稿未改可通过、改动后需重新校验」；前端 21 项故事用例（含「无勾选/无要求时按已填框架生成」「校验结果命名镜头·字段·类型·建议」）通过；全量前端 188 项、`vue-tsc`、`npm run build` 通过。未调用付费模型。

#### 故事确认门禁与初稿交付补充（2026-10-17）

- 生成阶段的内部检查不再代替用户主动校验。原 `story_review` 修订增加 `validation_requested`：初稿为 false，点击校验后为 true；内容未变时复用真实结论并追加校验记录，不再次调用模型。服务端确认和两页按钮共同检查此标记。旧确认记录仍可读取；尚未确认的旧稿需主动校验一次。只有优化建议而没有错误时可直接点击确认，客户端随明确确认提交原 `accept_warnings`，不再增加勾选步骤。
- 初稿经原有有界修复仍存在错误时，仅保留带真实检查结果的原 Run 候选，返回位置、类型及建议，不替换当前故事或伪装成功。修复只采纳被指出的字段；候选恢复仍需校验确认。模型返回未通过但不给问题位置时拒绝该无依据结论；旧格式发现项补齐可读建议。
- 确定性检查补充标题、概述、转场及 0.1–60 秒时长范围。有限数值可作为未完成草稿保存，越界时定位到具体镜头和时长字段，确认严格阻断；非数值及非有限值仍在结构边界拒绝。
- 已填镜头图片与本次勾选图片合并送入生成，保留固定旧版本；合计超过 8 个版本明确提示，不静默截断。服务端在模型调用前拒绝遗漏框架图片的请求。
- 修改归属为现有 Comic Domain 与前端交互，复用原服务、API、修订、Run、Artifact 和导出，不新增文件、依赖、数据库表或 Provider，不改用户历史与默认外部图片路径。后端组合 148 项通过，时长修改后的故事专项 39 项通过；前端 192 项（故事 25 项）、类型检查、构建、ruff 与结构检查通过。标准后端与前端开发启动均 HTTP 200，后端正常退出无 WAL/SHM。真实 HTTP/SQLite 的离线页面验证了生成后锁定、主动校验解锁、清空动作后定位错误、补全后重新校验确认。没有调用付费模型，模型语义质量未作真实验收。

#### 修复：点击整理初稿被旧图片引用拦截（2026-10-17）

- 现场 `ERR-0AA7003D`：作品 `comic-project-7039a12a…` 的故事 v88 固定旧图片 v2，历史重复版本记录后来指向另一份字节。原固定 Artifact 文件仍在，但已经回收；当前明确采用的 v1 与故事原图 SHA-256 完全相同。重新读取不会改变这个冲突，所以每次生成都在模型提交前失败。
- 原媒体 `require_image` 增加仅由服务端调用的不可变 `snapshot` 参数，故事的保存、读取、生成、确认及导出沿用原固定 Artifact，并继续核验身份、作品/会话归属、active/ready、文件及哈希。新入镜引用仍须当前采用；回收或损坏的原图仍阻断，不恢复回收站或放宽真实性校验。
- 若原图不可用，页面提供「使用已采用的同图修复引用」，只匹配 SHA-256、格式、作品和会话均一致的当前采用图。用户已勾选该同图时，点击生成会先按原保存接口修复引用，再整理；镜头文字保持原样。内容不同或来自其他作品时不自动替换。
- 点击动作在等待自动保存之前立即显示处理中，错误与通知同时显示在生成按钮旁；成功重新读取清除旧错误。复用原控制器、API、版本和 Runtime，无新增文件、依赖、数据表或供应商调用。
- 当前用户故事通过原公开保存接口追加为 v89，仅镜头 1 的引用改为同字节的已采用 v1；标题、概述、要求及镜头文字不变，v88 原历史已核对保留。未直接修改生产表，未执行模型生成或写入虚假校验。浏览器实际读取 v89、原错误消失。
- 验证：故事、作品、图片审核与删除组合 120 项通过；前端 196 项（故事 29 项）、类型检查、构建、ruff 和结构检查通过。新增离线回放覆盖现代作品与旧聊天在版本记录被复用后，生成实际收到原图字节、ZIP 导出原图、原图损坏仍阻断；前端覆盖保存期间立即反馈、按钮旁报错、同图修复及拒绝其他内容/归属。标准后端隔离启动 HTTP 200，正常退出无 WAL/SHM。真实模型语义未作付费验收。

#### 修复：故事复审证据错配时丢失初稿（2026-10-18）

- `ERR-CC45EA75` 发生于初次生成、初审和一次内容修正之后的复审。日志只能确认引文未匹配所指当前字段，未保存非法响应，不能断言只是空格差异。旧候选保存点位于所有审核之后，因此该异常同时丢失了已经生成的公开初稿。
- 原故事服务兼容同字段中保留词边界的连续空白/换行、等值时长和严格相同的图片引用 JSON；实际存入审核的依据仍是字段原文。虚构内容、跨字段引文、错误镜头及不同图片版本继续拒绝，不做语义模糊匹配。
- 成功返回但公开结构或引文不合契约时，最多纠正一次审核；只传公开坐标、固定诊断和已核验发现，不回传非法响应。已核验错误不会被纠正后的 `pass` 抹掉；传输失败/结果未知不进入此纠正，不叠加供应商重试。
- 在任何审核前通过原 Run 保存候选，合法初审结果绑定原内容保存。修后复审失败保留原候选及原真实结论，不替换当前故事、不伪造通过；候选恢复仍须主动校验确认。修复后实际内容未变时不再复审。
- 前端生成失败后只读查询原任务，立即展示已有候选恢复入口并保留原始错误，不重新生成。沿用原版本、CAS、图片身份、审核指纹、确认和导出门禁；无新增依赖、数据表或业务系统，未改写用户故事及图片。当前 PyCharm 后端须重启加载源码，静态页面由原构建更新；未调用付费模型。
- 验证：故事、作品、图片审核与删除组合 **142 项通过**（含新增 22 项）；前端 **198 项通过**（故事 31 项），类型检查、构建、ruff、结构检查及差异空白检查通过。两类不同故事、多图与原始 PNG/WebP 的生成→修复→复审纠正→主动校验→确认→ZIP 导出使用实际隔离存储和模型替身完成；连续坏引文、错误来源及审核传输失败仍阻断，候选可恢复且同请求不重发。标准后端和前端开发启动 HTTP 200，后端正常退出无 WAL/SHM，自有验证进程与启动会话均已清理；不代替真实付费模型语义验收。

#### 故事自动纠错与顶部简短校验（2026-10-18）

本节按用户明确要求更新“AI 整理初稿”的语义，替代上文已填字段永远原样锁定的行为。人工保存和人工校验仍不自动改写输入；自动整理则必须修正发现的错误，再交付真实通过检查的初稿。

- 原 `_repair_once` 已有字段白名单，但末尾再次合并用户框架会覆盖刚修正的非空字段。现在只在首次组稿沿用原框架；内部修正直接采用已核验问题指向的动作、运镜、时长、转场、标题或概述，其余文字、图片版本、镜头身份及顺序保留。最多两次实际内容修正（结构修正占一次），每次改稿均重新核验；新发现的问题可以继续修正，不再按相同失败等级丢弃已完成的修正。没有实际改动就停止，无限重试、错误冒充通过和未知请求重发均禁止。
- 生成、审核、修正复用同一故事语义说明：静态图及其来源描述起始画面；稳定身份、固定资产及明确持续生效的硬约束受保护。合理后续动作、对白、表情及有时间顺序的机位变化不因图片提示词未提及而判错；没有精确数量要求的近义规模描述至多是优化建议。真实起始衔接缺失、同时互斥要求及必需步骤与时长冲突仍须修正。此为模型输入契约，离线替身不能证明任意语义判断正确。
- 审核契约更新为 `comic-story-2`。候选携带 `review_contract`，仅复用当前契约的真实审核；旧候选缺少标记或使用旧口径时仅恢复草稿，主动校验按新口径执行，不能把旧审核重新标成新版本。
- 生成仍失败时只返回短摘要，完整发现留在原 Run 候选。手动校验的结构化问题统一展示在页头，默认只显示位置、简短问题与修改建议；不显示长篇证据，不在 AI 按钮下和右侧重复。历史长说明原生折叠，右侧链接可跳至顶部；修改后旧结果隐藏、确认及导出重新锁定。
- 复用现有 Domain、组件、API、Run 和不可变修订；没有新增依赖、业务文件、数据库表或 Provider，也未改写生产故事和图片。后端组合 145 项通过；最终候选契约修改后，候选恢复/失败保持/已填内容自动纠错相关 10 项通过（包含新增两种旧候选兼容），前端 199 项（故事 32 项）通过，类型检查、构建、ruff、结构检查通过。标准后端隔离启动 HTTP 200，正常退出无 WAL/SHM。浏览器使用隔离真实 HTTP/SQLite 和本地模型替身验证顶部错误、修改后锁定、重新校验通过与确认解锁；未执行付费模型实测，部署须重启后端并刷新静态页面。

#### 修复：单帧负向词误作视频禁令与制作字段无法协同（2026-10-18）

- 只读核对 `ERR-A54CCA3F` 原 Run：请求已包含 v89 的全部框架，候选最后仅剩一条错误，把转向正面的转场判为违反“持续背面”要求。真实来源原话只是起始画面“它背对着镜头”；原单图 Compiler 的负向词另有“正面或面部可见；转身面向镜头”。它们曾无角色区别进入故事审核。公开 Run 仅保留最终候选和结论，不能据此推断每轮修正的全部内容。
- 生成、修正与审核共用 `_story_sources()` 投影：保留来源原话、有效替代、scope/origin/trust、固定资产等依据，标为起始帧参考，不向视频模型传单图编译负向词。持续限制按真实要求语义判断，不按旧 scope 标签猜测；真实身份、固定资产和明确持续禁令仍受保护。原来源快照、历史版本和内容哈希不改写，审核契约升为 `comic-story-3`，旧结论不得冒充当前口径。
- 首次合并只固定已有标题、概述及镜头身份、图片、顺序；四个制作字段采纳模型协调后的结果，缺值才沿用原框架，不再把修正全部覆盖回冲突原文。同图重复镜头优先按稳定 shot_id 匹配；仅框架与返回池均唯一的同图结果允许旧 ID 兼容，不跨图借用字段。
- 内部生成、修正和复核始终携带只读 `user_intent` 原框架，保留合法剧情、角色、动作及对白；有错的执行方式允许纠正。手工校验不携带旧整理目标，只判断当前用户稿。问题镜头的动作、运镜、时长与转场可以联动，只有真实转场/连续性错误可授权下一镜边界协同；警告不扩大修改范围，无关镜头及故事文字不被顺手改写。空稿缺镜头时明确授权依据已选图补镜头。
- 仍最多两次内容修正，每份修改结果真实独立复核，未通过不发布；未改稿主动校验复用该份真实结论，改动后旧结论失效，错误及建议沿用顶部简短展示。未新增依赖、文件、业务表、Core/Provider 路径，未修改生产故事和图片。
- 验证分组通过：原故事 68 项、新增 13 项及旧候选口径 2→3 的补充用例，作品/图片审核/删除 85 项，后端共 167 个独立用例；前端 199 项、类型检查与构建通过。新增覆盖初次协调修正保留、同镜时长联动、原意图贯穿、静态负向与持续要求区分、同图重复入镜、跨镜授权及警告不扩大范围。两位只读审查专家复核，ruff、结构检查、差异检查通过；标准后端隔离启动 HTTP 200、退出 0、无 WAL/SHM，自有会话已清理。未进行付费模型语义复测，不能把这些离线结果称为真实模型效果验收；运行中的旧后端需重启后加载本次修复。

### 资产图片完整预览与工作区导航精简（2026-10-18）

- UI 修复复用原 `ArtifactCard`、`MediaArtifactContent` 与列表样式。图片摘要不再受文字摘要 `3.3em` 高度裁切，使用独立预览区与 `contain` 等比显示；明确图片行的网格区域，窄屏上下排列。原图详情、下载、筛选及回收站功能保持。
- 按用户要求从 `DirectorWorkspace` 左侧导航移除“作品素材”“版本历史”，删除对应无用图标导入。保留已有数据、读取接口及旧链接兼容，不删除素材或历史记录；无新增文件、依赖、业务数据或后端改动。
- 前端 199 项测试、类型检查、构建、结构及差异检查通过，静态页面由原构建更新。浏览器实际读取本机三张图片：均已解码，预览高度及内容高度同为 200px，无裁切；详情可完整显示并提供下载原图。当前作品导航实测仅六项（画面方案、生图指令、图片工作台、故事编排、导出材料、对话），临时预览标签及尺寸覆盖已清理。未调用付费模型或写入生产业务数据。

### 修复：清理旧图片后整个作品列表无法读取（2026-10-18）

- `ERR-D1E63E01` 的实际链路是 `GET /api/comic/works` → `workspace.list/get/get_task` → `media.list`。图片家族的 v2 等修订仍在，v1 仅为旧外部上传 Artifact 的投影；原图回收或彻底删除后，读取列表名称、来源 Brief 和版本历史仍调用要求 active 的 v1 入口，导致一个失效旧图阻断全部作品加载。只读产物接口确认现场原图及后续产物已为 purged，没有执行恢复、删除或改写生产数据。
- 修改限原 `ComicMediaStore.get` 及三个历史元数据调用方。内部 `_legacy_history` 仅在同一图片家族、作品、会话确有后续修订时允许读取旧上传的历史元数据；原图片/Artifact 身份及来源校验继续生效。默认 `get`、采用、文件可用性及故事导出检查不放宽，不把已清理文件当成可用素材，也不增加 HTTP 参数。整个家族被移除后仍不允许历史投影令图片复活。
- 新增四种真实存储及 HTTP 回归：旧 v1 回收/彻底清理 × 新版保留/同样停用，验证作品列表、来源 Brief、原始标签与历史仍可读，另一作品不受影响，版本及生命周期不被改写，实际媒体不可用时仍阻止采用；移除整个家族后不重新出现。没有新增文件、依赖、表、Provider 或执行框架，未修改本轮前端显示与导航。
- 验证：作品、图片审核/删除/版本删除及故事组合 171 项通过，语义媒体的独立作品/来源归属专项 2 项通过，ruff、结构和差异检查通过；前端类型检查及正常构建通过。标准后端隔离启动首页及 `/api/comic/works` 均 HTTP 200、退出 0、无 SQLite WAL/SHM，自有会话已清理。未调用付费模型；当前运行服务需重启后加载本次后端修复，页面刷新重新读取作品。

### 图片清理后的依赖链修复（2026-10-19）

本节补齐上轮只修目录元数据、未覆盖工作台与故事实际调用的漏项。现场 `ERR-042938A7` 是报告列表校验已停用图片失败，`ERR-235395B3` 是故事候选在逐项可用性检查前读取旧采用 v1 失败；`ERR-7D77FC47` 则是已彻底删除的原文件确实不存在。失效素材不能通过隐藏错误重新变成可用图片。

- **Domain 与 Shell**：复用 `ComicMediaStore` 的 `image_view` / `selection_view`，版本和采用指针分别公开可用性、原因及生命周期。历史版本、旧采用身份、工作台修改基础和持久化草稿保留，不自动更换采用版本。旧上传只剩采用指针时也可显示不可用历史家族；明确移除后不再投影复活。计数只把可用的采用版本计入已采用数量。列表仅检查身份、生命周期、文件存在及容量，不在轮询中重读所有媒体哈希；实际文件读取、采用、分析、确认与导出继续核验字节。
- **隔离单项失败**：故事候选和报告列表返回逐项不可用诊断，单张旧图或单份回收报告不阻断有效项及整份作品。历史父图仅用于已核对归属与指纹的来源沿革，不能成为模型输入或替代失效指令授权。父作品当前故事对下属画面图片的硬引用一并保护，兼容没有会话字段的旧固定快照；不让旧历史故事永久锁住已解除引用的图片。
- **前端**：工作台不再抓取已知失效版本或自动分析它；修改需求与报告独立读取，单份报告失败不清空需求。失效基础的新增编辑明确仅保留在本页，切换正常图片不会带入旧文字或报告指针。故事、候选和历史独立应用，失效图片不能勾选或添镜头，原故事仍可阅读。回收图片提示从回收站恢复，彻底删除图片提示重新导入；原文件缺失的正常版本仍只允许同哈希恢复。
- **通用 Core 删除安全**：沿用原 Artifact 服务、CAS 和 FileCatalog。先在同一写事务内核对状态与共享文件引用，再执行删除与状态更新；同库文件目录复用该事务，避免再次取得写锁。文件删除失败不提交清理状态，保留回收站的恢复/重试入口；其他正常、回收站及内部产物仍引用的文件保留。保留未解析输入路径检查目录链接/联接，拒绝经重定向删除其他文件；已 purged 的产物即使与其他产物共用文件，内容网关也拒绝读取。文件系统与 SQLite 无法提供断电时的跨系统原子保证，未以事务声称消除此物理边界。

没有新增业务文件、表、迁移、依赖、Provider、Agent 或 Runtime，也未改写生产作品、故事、采用指针、历史或文件。静态页面由原前端构建更新。所有模型为明确离线替身，不是付费语义验收；用户已经彻底删除且无保留副本的图片不能由本次代码修复凭空恢复。

验证：删除、图片审核、语义媒体、故事与作品组合初跑 **199 通过、4 项旧 DTO 全对象相等断言失败**；断言改为核对原不可变元数据不变，并额外核对实际可用性后，图片审核整组 **43 项通过**。Core、HTTP 与运行存储最终 **114 通过、1 项宿主链接权限测试跳过**；新增目录重定向删除用例在本机实际执行通过。前端全组 **203 项通过**，类型检查、构建、ruff、结构与差异检查通过。真实隔离 HTTP/SQLite 页面验证失效基础保留原需求、切换有效图片不串内容、故事及有效候选可读、刷新恢复；标准后端首页与作品接口 HTTP 200、正常退出 0、无 WAL/SHM，临时会话已清理。

完整后端扫描初次 **1227 通过、8 失败、1 跳过**。其中旧故事删除保护遗漏已修复并复测；四项版本 DTO 与一项历史 hash 夹具按新增可选字段对齐，生产审核哈希不改。另两项旧导演测试与现有质量测试的审核不可用门禁互相冲突，本轮只对齐负向测试契约，保留正常/可修复模型的完整闭环，所在文件及对应质量门禁 **14 项通过**；不借图片修复放宽导演审核或人工确认策略。八项初次失败均已在对应专项复跑通过。完整扫描期间继续修复的目标文件通过各自专项复跑验证，不把初次全量失败报告描述为最终全量一次全绿。

### 提示词连接失败与错误就绪状态（2026-10-19）

现场 `ERR-1B139515 / trace-d638536df88f` 是完整 Prompt 编译的模型连接失败：SDK 异常链中有 Windows `10048` 地址/端口冲突，尚未取得模型文本。没有证据表明本次错误由导航移除或图片删除引起，也未发现共享文本客户端未关闭；不凭一次连接错误改造连接池或更改用户代理配置。

- **共享模型出口**：维护原 `core.llm` 兼容门面，通过有界、去重的异常类型/系统错误码识别本机地址冲突、DNS、证书、连接拒绝/中断和超时，向用户给出短句恢复提示。保留原异常链、错误编号、追踪、客户端关闭及通用重试策略；公开响应不拼接代理地址、凭据或原始异常正文。文本、视觉和流式入口共用诊断，不建立新网关。
- **外部完整 Prompt**：原 Shell 编译适配器在单次调用范围设置并恢复原 `ContextVar`，旧 Brief 与语义 Brief 都不会在传输结果未知后隐藏重试或切换供应商。已返回文本后的有界结构/内容修复保留，内部生产默认策略不变。未收到有效文本不会追加 Prompt、推进作品版本或标记 ready；已有有效 Prompt 在失败后仍保留，用户可以明确重试。
- **前端取数与状态**：就绪状态核对当前作品、镜头、分镜、需求、导演版本以及完整正文，不再按全项目历史数量判断。取消请求保留原内容，单个镜头历史读取失败不阻断其他镜头；晚到响应按所属作品/镜头处理。历史读取诊断与编译/上传错误分开，仍共用镜头内一个显示位置；读取恢复只清自己的诊断，不抹掉真正的编译失败。编译失败不再重复写入全局对话错误；保存成功后刷新失败单独显示为连接问题，不冒充生成失败。

修改限原 UI、共享文本出口和 Shell 请求适配，不新增业务文件、依赖、表、迁移、Provider、Agent 或 Runtime，不改写生产数据、审核通过条件、账单或代理设置。没有进行付费模型验收；当前不带密钥的 HEAD 连通性探测返回 401，仅证明当时能建立服务连接，不能代替真实生成质量验收。手动编译跨标签的请求级幂等属于既有提交治理边界，本次保留原接口，不以按钮禁用声称已解决跨客户端重复提交。

验证：最终 Prompt/共享模型专项 **90 项通过**，前端全组 **214 项通过**，类型检查与正常构建通过，ruff、结构和差异检查通过。真实隔离 HTTP/SQLite 与真实 SDK 离线传输回放初编失败、重编失败保留旧版本及显式重试成功；浏览器验证错误仅一处、失败不误报就绪、成功后及刷新恢复完整 Prompt。标准 `python -m kantoku serve --no-browser` 隔离启动首页及作品接口均 HTTP 200，正常退出 0，无 WAL/SHM，自有验收会话已清理。

最终全量后端按清单将全部 **70 个测试文件**分为四个独立隔离会话执行，合计 **1253 通过、1 跳过、0 失败、0 报错**，四组进程均退出 0。跳过项是宿主不允许创建符号链接的旧清理测试；实际目录联接防护用例仍执行通过。本次完整扫描包含最终旧 Brief 单次传输包装，没有用中断前的进度冒充最终结果。各组 JUnit、标准输出、完整文件分组及汇总通过 RuntimeManager 登记为审计报告，存于项目测试根 `artifacts/validation/prompt-chain-76384e34b0/`；测试数据库与图片会话已清理，不保留整份测试运行目录。

### 已采用图片的多轮视觉延续（2026-10-20）

修复作品对话在已采用图片之后丢失角色外貌、服装、场景结构和风格依据的问题。归属为现有 Comic Domain 与 Shell 请求组装；复用图片 `image_selection`、不可变 DirectorSpec、原 Prompt Compiler、Core Artifact 和现有共享文本出口。

- 作品新方案从范围内的已采用图片或引用方案的 `reference_context.continuity` 恢复图片身份、版本、Artifact、内容指纹及不可变 Prompt 来源。来源选择依据本轮语义与明确引用，不用最新任务、角色关键词或固定轮数。已编译的后续方案作为当前有效文字依据，避免局部修改在下一轮回退首图。
- 存在已采用图片来源时，增加一次有界、单次尝试的文本判断，选择图片与本轮明确授权的 Prompt 叶子变化。修改范围须附本轮逐字依据，未知字段、来源不明、歧义、传输失败及容量不足明确停止；不会退化成无约束重画。明确独立换题才移除旧图片引用，不继承旧任务事实。未采用图片的原路径保持兼容。
- 原 Compiler 最后投影保留字段，避免模型、Shot 或摄影组装将其覆盖；约束复核后的正文再次检查保留字段。只换服装不释放身份、外貌和场景，动作变化不释放其他视觉元素。用对话修改当前方案和直接编辑均由服务端更新相应权限，并保留原审核及重新确认要求。
- `continuity` 只保存现有版本引用及修改依据，不建新记忆表。图片采用版本、内容、作品/会话归属、Prompt 身份与请求时间锚点重新核验，编译保存前再次核对。直接上传或外部改图没有完整文字依据时，不把旧导演或旧 Prompt 擅自当作新图的像素事实。
- 最终复制/导出正文明确要求附上同一张已采用图，并给出文件名、版本及内容指纹；沿用现有 `final_prompt` 与 `reference_attachments` 契约。外部 Codex 仍需实际附图；纯文字不能证明外部模型已使用参考图，离线检查不替代真实生图质量验收。

新增/删除文件、依赖、表及迁移均为无；未修改 Core、Provider、前端源码或静态页面，未改写生产图片、采用指针与历史。现有 API 增加可选来源元数据，不改变默认外部复制/上传及人工确认路径。新增的文本判断属于原文本能力调用，不宣称免费。

验证：相关后端组合 **250 项通过**；随后完成自然语言修订与直接编辑接入，工作区与编辑回归 **65 项通过**，末次调整后的专项 **8 项通过**，覆盖两类不同主体、65 条无关讨论及 Run 后继续、漏选稳定字段、局部服装/动作变化、服务重建、换题、非法释放、歧义、未知传输、采用版本变化及跨作品拒绝。各组有重叠，不相加为独立用例总数。ruff、结构及差异检查通过。标准后端入口在 RuntimeManager 的隔离会话启动，首页与作品 API 均 HTTP 200、退出 0、无 WAL/SHM；前端类型检查及原构建在隔离目录通过，仓库静态产物未重建。验证会话均已清理，没有付费模型或外部 Codex 生图实测；人工画面质量待用户使用实际参考图验收。源码尚未提交，部署需重启后端。

### 返回工作区误建作品（2026-10-20）

修复从全局侧栏的资产、任务中心、设置等入口返回工作区时丢失作品定位并创建空白作品的问题。修改归属为前端 Shell 的导航与既有工作区初始化，不涉及业务层或视觉布局。

- 原路由记录各领域最近的有效作品、画面任务、会话及节点地址，使用页内状态和标签页 sessionStorage；全局“工作区”及命令面板入口恢复该地址。在其他页面刷新后也可恢复，存储不可用时仍可在页内返回；无效或跨领域存储不能成为当前选择。
- 没有明确作品或会话的工作区入口从现有作品列表恢复，不因现代作品对话被旧历史列表过滤而新建。明确指定的作品读取失败时保留原错误入口，不创建替代作品；原“新建作品”按钮行为保留。
- 沿用原测试与离线预览工具，预览的作品和创建计数只在进程内存中。没有新增源码文件、依赖、表、迁移、Provider、Agent 或 Runtime，没有删除误建的历史作品或改写生产数据。正常构建更新原后端静态页面。

验证：前端全组 **218 项通过**，类型检查、正常构建、结构与差异检查通过。隔离浏览器实际验证从资产、任务中心和设置返回，设置页刷新后再返回，以及图片工作台经资产返回原节点；创建计数始终 **0**、作品数量保持 **1**。主动创建的原处理函数另有执行测试，确认只产生一次创建。本轮没有修改后端，标准后端入口沿用同日上项隔离启动验证。

### 执行中离开后的结果恢复与聊天选择（2026-10-20）

修复作品首轮执行中离开工作区后只留下用户消息、结果不再自动出现的问题。根因是空作品未恢复原请求编号，且没有画面 Project 时刷新直接返回；已有请求恢复也只由按钮触发。归属为现有前端 Shell 的请求呈现与恢复，后端执行、Run、版本、模型调用及审批沿用原契约。

- 空作品和已有画面统一读取原标签页请求标记；返回后的自适应轮询只查询原作品请求与旧任务兼容接口。运行中、尚未绑定和未知结果继续等待，不自动重发模型。终态从更新后的作品读取新任务，在原页面恢复已有方案或讨论回复；原提交连接因页面刷新断开后也可查询恢复。
- 查询在同选择内复用在途请求，异步结果核对作品、会话选择版本与请求编号，离开后不改写其他选择。晚到的旧提交只能清自己的标记；载入结果失败保留待查标记及可读连接诊断。没有 Project 的旧空快照可通过只读刷新发现已经保存的首个画面，当前提交仍在处理时不抢占其界面。
- 原全局选择高亮引用了未定义的 `--selected` / `--selected-ink`；选中高亮改用已有主题颜色。聊天正文和输入框明确允许原生文字选择，快捷键继续由浏览器处理；历史回复保持只读，不把复制粘贴变成改写历史。
- 复用原组件、API 客户端、路由与离线预览工具，没有新增源码文件、依赖、表、迁移、Agent、Provider 或 Runtime，没有修改后端业务、生产数据、费用和人工确认。正常构建更新后端静态页面。离线预览增加内存中的延迟请求及计数，并限制 API 后缀匹配，避免测试页 `/assets` / `/tasks` 刷新被误当成 JSON 接口。

验证：前端全组 **225 项通过**，类型检查及正常构建通过；原后端的作品请求重放与 HTTP 会话边界 **2 项通过**。标准后端入口在 RuntimeManager 隔离会话启动，首页载入最终静态文件及作品 API 均 HTTP 200，退出 0，无 WAL/SHM，自有会话已清理。隔离浏览器用延迟响应验证执行中切走再返回自动出结果、原页面刷新丢失提交连接后仍能恢复；两条用户请求只计两次提交。鼠标双击可选中回复文字，实际选择颜色为主题蓝底白字；输入框 Ctrl+A/C/V 的复制粘贴原样成功。未调用付费模型，界面替身不代替真实模型语义质量验收。

### 多轮事实来源误标与任务建立前失败的终态（2026-10-20）

用户再次报告多轮对话出现“模型描述不能升级为用户事实”。实际日志 `ERR-50828538` / `trace-455377f84c4a` 证实错误来自原 ConversationContext 的事实校验，发生在 Run 建立前；旧请求查询仍返回 submitted。未保存的无效模型正文无法还原，不把截图内容推断成某一个具体模型字段的证据。

- 归属为 Core 通用会话来源校验及 Shell 的请求结果呈现，复用原 ConversationContext、文本出口、RuntimeStore、内部 JSON Artifact、作品请求查询和前端恢复组件，不另建记忆或恢复系统。
- 对已经解析、已经读取并通过身份与版本核验的任务事实，标记来源类型或原文摘录错误时，原链路只允许一次有界结构纠正。纠正仅能覆盖指出的事实位置，不能改任务、目标、动作、事实状态或已合法事实；随后再次执行原严格校验。用户事实仍须引用用户消息的连续原文，模型创作选择不能成为用户或资产事实。来源不存在、跨会话、版本错误、已撤回事实恢复及传输错误均不能通过此纠正绕过。
- 理解当前请求与压缩历史摘要复用同一个纠正方法；按任务事实来源提供正文，避免把整个历史重复送入纠正。摘要纠正后再次检查容量。不按人物、题材、关键词或对话轮数选择规则；截图原话仅在测试夹具内。来源与用户修改范围继续交给原上下文和图片延续链路处理。
- 原请求认领后、Run 建立前的异常保存为同一请求的内部失败结果，查询返回 failed 与原错误编号和 Trace。相同请求重放返回已保存错误，不重复调用模型。前端收到这个终态后结束同步并保留公开诊断；运行中或未知结果仍只查询，不自动重发。
- 状态/API 变化仅为作品请求查询的可选 error 字段及明确失败终态；成功和已有 Run 的投影兼容。异常结构纠正会增加一次原文本能力调用，不宣称免费；没有响应的传输错误不自动重试。没有新增文件、依赖、表、迁移、Agent、Provider 或 Runtime；没有改变外部生图和人工确认默认路径。正常构建更新后端静态页面。

验证：`tests/test_conversation_context.py`、`test_dynamic_director_context.py`、`test_comic_workspace.py`、`test_web_studio.py` 合组 **183 项通过**；包括截图原话的连续后续请求、不同事实来源、历史摘要重复压缩、纠正失败、越界/重复/遗漏/撤回/虚构及跨会话拒绝，原 120 轮记忆测试也执行通过。前端全组 **226 项通过**，类型检查、构建、ruff、结构与差异检查通过。真实隔离 HTTP/SQLite 浏览器以延迟失败验证发送中离开、返回恢复原错误并结束等待、继续第二轮，两次输入只有两次提交，错误编号均保留。标准后端入口在 RuntimeManager 隔离会话启动，首页载入本轮 bundle，作品 API 均 HTTP 200，退出 0，无 WAL/SHM；自有测试会话已清理。没有付费模型和外部 Codex 生图质量实测，源码未提交，运行中的旧后端需重启。

用户另外明确授权备份后补记截图中的一条历史失败请求。已通过 DatabaseManager 的 SQLite backup 保存 `data/backups/kantoku-0115a7199f64468b8b2f3d3eb950abaa.sqlite`，核验同一作品、会话、消息事件及日志 Trace 后，通过原 RuntimeStore 仅追加内部结果 `artifact-ac509c59a75149f3b3adab5c5133747f`，关联请求 `b09b1d51-7940-4f26-8de9-77cad5c83a37` 和原错误编号。没有改写用户消息、Run、方案、图片或账单，也没有模型调用；前后消息完整对比一致。历史请求的原完整信封不可还原，恢复记录如实标注缺失，不伪造版本和输入。需要恢复时使用备份副本核对，不能覆盖活跃数据库；本次未执行删除或回滚。这项一次性旧数据补记没有进入运行逻辑。

### 已采用图片后的连续创作结构纠正（2026-10-21）

现场 `ERR-BFDD317F / trace-86ee33fba125` 已完成会话理解及消息补读，图片延续调用也已收到文本，随后在 `workspace.image_continuity` 的结构校验处失败，第三个画面尚未建立。非法响应未保存，不能推断具体错误字段。原 Schema 将修改字段声明为任意字符串，仅运行时限制叶子路径，且坏结构没有纠正入口；既有 120 轮测试只覆盖通用记忆，已采用图片测试的模型替身始终返回合法结构，未覆盖这一组合。

- **归属和复用**：只修改原 Comic Domain 的 `models.py`、`workspace.py`，复用当前图片采用版本、不可变 Prompt 来源、共享文本出口和统一错误日志。合法字段枚举直接由现有 `ComicPromptContexts` 派生，候选枚举取本轮已核验目录；不按素材、关键词、轮数或最新图片固定选择。
- **有界纠正**：只对已经收到的无效结构纠正一次，每次调用前重新检查容量。只给原请求、原候选、安全错误位置/类型及逐字段核验后的公开片段；不传非法响应、未知键或私有推理。已合法的图片选择、修改字段、原话及条目数量不许在纠正时改写；额外未知键也不能使合法字段丢失保护。空结果、缺少选择和纯空白引文/问题不能伪装成有效决定。纠正后仍执行原来源、逐字依据、采用版本、内容与归属检查。
- **保持边界**：传输错误不重发；持续坏结构、未知来源或真实歧义仍保留原图并公开诊断。原请求重放不新增模型调用。结构纠正可能增加一次文本费用，不宣称免费；未启用内部生图、改变人工确认或账单处理。多候选中的外部改图没有原 Prompt 身份时，继续以其真实图片版本为基准，不用旧文字冒充新图像素事实。

验证：上下文、动态导演、图片连续性、完整 Prompt 与语义媒体五文件 **228 项通过**，HTTP 文件 **73 项通过**。工作区整文件 **67 项通过**；最后补充有效决定校验后，空 JSON、仅变化列表、空白问题的纠正成功用例另跑 **3 项通过**，没有声称最终 70 项一次全量执行。两类不同素材分别完成 **8 轮**创作（首轮建立并采用图片，其后连续 7 轮），含导演确认、分镜、Prompt、服务重建、累计服装/外貌/环境/摄影变化保持、两次坏结构注入及逐轮请求重放；另覆盖多候选外部改图 v2、纠正扩权拒绝、未知传输、容量不足和空白授权。

两位专家交叉审查；ruff、结构和差异检查通过。前端类型检查和构建在自有隔离目录通过，未重建仓库静态文件。标准后端入口隔离启动，首页及作品 API 均 HTTP 200，退出 0，无 WAL/SHM，自有会话已清理。无新增/删除文件、依赖、表、迁移、Agent、Provider 或 Runtime；没有改写生产作品、图片、请求历史或账单。未调用付费模型，离线回归不代替真实模型语义和画面质量验收；运行中的旧后端须重启加载源码，历史失败请求仍保留原结果，需重新发送创作消息。

### 图片来源决定与参考图实际交付（2026-10-21）

新现场 `ERR-85BF3361 / trace-96544d92ddc7` 在图片延续模型成功返回后，被来源关系检查拒绝；没有原始无效响应，不能断言当次具体字段。上轮纠正只覆盖结构错误，未覆盖候选身份的唯一别名和互斥分支冲突。另一个交付缺口已经确认：原复制和导出操作只有文字，文件名和 SHA-256 不能让外部应用取得图片。

- **来源判断**：在原 `ImageContinuityDecision` 和 `ComicWorkspaceStore` 中，候选键按本轮已核验目录生成。图片 ID、Artifact ID 仅在唯一对应一个候选时归一为该候选；同图对应多个方案文字状态时拒绝猜选，不按最新、首项、题材或轮数选择。候选选择与独立画面依据同时出现时，进入原一次纠正，只能保留原图片和已合法修改，或使用原逐字独立依据且清空旧图修改。第三来源、改写引文、新增权限、未知或歧义身份继续拒绝。非字符串候选及私有对象不得进入纠正输入。采用版本、内容、会话和作品归属检查保留。
- **实际参考图**：原 `core.ts` 的受认证 Artifact 内容读取增加 Blob 入口，原 `shotGeneration.ts` 负责核对指令记录的 SHA-256 后准备交付。`DirectorWorkspace` 和 `ComicShotGeneration` 提供“复制图文”、单独复制图片、单独复制文字、参考图预览与下载原图。预览和下载同样核验内容，缓存按 Artifact 和指纹区分；跨作品晚到结果不能改写当前选择，复制恢复只清自己的诊断。没有新建媒体 Service、公开图片 URL 或外部自动发送。
- **外部接收边界**：剪贴板提供真实 PNG、完整正负向文字和含图片的 HTML。JPEG/WebP 先核验原字节，再为剪贴板转 PNG；原文件与来源指纹不改。接收应用自行选择格式，成功复制不代表外部对话已附图。多参考图明确要求逐张复制或下载，不能静默只取第一张或降为文字成功。“导出文字”如实保持文本导出。原 `image_prompt` 说明来源文件名、版本与指纹不是图片地址或检索条件，附件改名或转码后按实际附图识别；缺图时先补图的要求保留。
- **范围**：复用原 Domain、Compiler、Artifact、UI 和版本契约；无新增业务文件、依赖、表、迁移、Agent、Provider 或 Runtime，不改变默认外部生图、人工确认、生产数据和账单。原离线预览脚本增加 `--clipboard`，只提供内存夹具及本地接收页；正常构建替换后端所需静态 bundle，未手改压缩文件。

本轮会话上下文、动态导演、图片连续性、语义媒体及 HTTP 五文件 **249 项通过**，完整 Prompt 文件 **53 项通过**；这两个分组没有重叠。前端全组 **233 项通过**，类型检查、正常构建通过。浏览器实际检查了真实参考图预览与图文复制成功状态；另有原生剪贴板读回记录包含 PNG（508×859，818968 字节）、完整正文及图片 HTML。没有向外部 ChatGPT/Codex 发送图片或进行付费模型验收，不把剪贴板读回称为跨应用接收或生图质量通过。标准后端隔离启动载入最新静态文件，首页与作品 API 均 HTTP 200、退出 0、无 WAL/SHM；前端开发入口 HTTP 200，临时浏览器与自有验收进程已关闭。运行中的旧后端需重启、页面需刷新；历史失败请求保留原结果，重新发送创作消息才使用修复。

最终来源专项整文件 `tests/test_comic_workspace.py` **88 项通过、退出 0**，包含两类素材各 8 轮完整创作及新增身份/冲突/私有对象用例；此前单跑的新增 19 项是其子集，不重复计数。与上面两个不重叠后端分组合计 **390 项通过**。两位专家最终复核通过，目标 ruff、结构与差异检查通过。没有改写现有生产图片、采用指针、历史请求、方案或账单；此次验收未自动重启正在使用的后端。

### 修复：无效继承字段在过滤前被判重叠（2026-10-21）

用户现场 `trace-db600fd117ce` 的三次上下文理解均成功，最后装配估计 13036/18000 token，并非容量或第三轮次数限制。保存的来源中，首方案字段为 `character_presence/style_boundary/color_strategy/lighting`，次方案为 `character_presence/character_pose`。除合法组 `lighting` 外，其余都是缺少对象前缀的裸字段，原消费白名单会丢弃；但更早的 `inheritance_conflicts` 按原始字符串相交判断，两个无效 `character_presence` 触发了“这些方案指定了重叠的设定”澄清，图片延续还未执行。两份实际人物描述并不相同，不能用同人物、继承链或最新方案规则替代来源判断。

- **最小修复与归属**：原 Comic Domain `ComicContextBuilder.inheritance_fields()` 由既有字段声明、DirectorPlan/CinematographyPlan Schema 和已读取的实际对象共同给出可用完整路径；Shell 在冲突检查前统一投影来源，并将同一投影用于公开上下文、记忆、图片延续、继承及 scope 计算，删除后段不同口径的局部过滤。未知字段沿用原丢弃诊断，不新增裸字段别名，不凭素材或轮数选值。领域指令动态列出完整字段并要求每个继承目标选取一个有依据的来源。
- **边界保持**：空字段来源仍保留并核验 Run、Project、Spec ID 和版本；同一 Run/版本的同一合法字符串字段只消费一次，不合并不同 Run 或版本。合法组与叶子间的真实重叠仍澄清，未放宽图片采用、原图字节、人工确认、事实来源或请求重放保护。投影只处理继承选择，不删除当前用户要求、有效事实及原始历史。
- **复现与验证**：旧生产源码下，新增动态用例 5 项均失败；修改后的两素材完整链路也在第三轮先复现相同澄清。修复后动态文件 18 项通过，记忆投影断言增强后的相关 4 项通过，最后新增精确同源去重/错版本拒绝 1 项通过；最终 19 个用例分批验证，重叠子集不相加。图片来源边界 44 项通过，两类素材各 8 轮完整链路 2 项通过，第三轮同时选中前两轮来源并验证投影、双候选准确选择、累计 Prompt、参考图交付和请求重放。长链首次复跑末尾因测试替身忘记切回旧方案目标而出现两项 StopIteration，修正测试状态后两项完整复跑通过，未据此改动产品逻辑。
- **其他检查与限制**：项目、图片连续性及 HTTP 三文件初跑 170 通过，旧任务历史时间顺序用例 1 项失败；该未改动路径的单项复跑通过，未将初次分组描述为全绿。通过运行中服务只读 API 核对原两方案身份和实际字段，重放来源投影后保留 `[[lighting], []]`、丢弃 5 项无效选择且不再误判；合法完整路径重叠仍被拒绝。两位专家复核、目标 ruff、结构和差异检查通过；前端类型检查与构建通过。标准后端隔离启动首页与作品接口均 HTTP 200、退出 0、无 WAL/SHM，自有会话已清理。

无新增或删除业务文件、依赖、数据表、迁移、Agent、Provider 或 Runtime；未调用付费模型、改写生产方案/图片/历史请求或账单，也未自动重启正在使用的后端。当前故障的工程复现已通过，不宣称任意模型语义或外部图片质量已验证；重启后端后重新发送创作消息，历史澄清结果仍按原请求保存。

### 完整 Prompt 的历史来源契约与当前事件（2026-10-22）

现场 `ERR-2A08883C / trace-a8627796e934` 的两次文本调用均成功，失败在原 Compiler 重新解析固定的旧 Style Bible。来源按 `semantic-contexts-2` 合法保存了“远景弱化细节”的摄影质感；本轮约束提取退化为逐字兼容契约后，旧解析却按本轮契约拒绝相同来源。模型重编仍会被服务器放回同一份固定文本，增加重试不能解决该确定性冲突。

- **来源兼容**：只修改原 Comic Domain 与 Shell，复用既有 Compiler、Project/Prompt 修订及 Core Artifact。服务器从已选中的不可变 Prompt 读取原契约与具体字段值，只有逐字不变的字段得到兼容；保存时重新核验，编辑从数据库前版取依据，恢复从指定历史版取依据。沿原 `context_sources` 保存指向原语义 Prompt 的引用，下一轮可重新核对；客户端自报契约或来源不能扩大范围。不把整个当前 Brief 升为语义契约，逐字硬约束继续生效。
- **全部消费边界**：整段 Bible、部分字段锁、编译末次校验、保存、编辑、恢复和再次复用使用同一份精确字段依据。固定输入在模型调用前预检，非法来源不会再消耗结构修复调用。合法空身份/空段落可按原值保持；新值、类型、长度、换行、整份空 Prompt 及固定资产检查仍严格执行。
- **事件与姿态**：原单幅分镜把 `character_pose` 当作整个事件，导致保持垂钓坐姿时覆盖箭矢来袭。现在分镜从当前任务或已确认创意取得事件，姿态与视觉焦点独立保留；焦点可以是静态主体，不要求写成动作。自动镜头的任务说明交给原模型分别编译动作、构图与表情，不把整句原话强压到动作字段；人工短动作仍精确投影。已保存的自动镜头仅在动作逐字等于当前导演姿态时兼容读取当前 Brief 事件，并记录实际输入来源，不改写 Shot 或历史。长事件由原模型凝练，不截断、不增加修复轮数。
- **回归校正**：新增用例先复现整段和部分锁两种来源失败，随后覆盖约束提取超时、旧姿态保持、新事件、静态焦点、采用图指纹、编辑、恢复及服务重建后继续。扩大回归发现“视觉焦点即事件”的初版修法不成立，已修正生产逻辑；旧模型替身改为实际生成本轮事件，不依赖服务器覆盖。构图核验进一步检查真正的构图字段，持续遗漏仍如实返回 `advisory` 和未确认项，不把正文其他位置出现同一句话冒充构图已纠正；未扩展原通用约束自动改写规则。

本轮相关分组分别为项目/旧 Prompt/语义媒体/HTTP **105 项通过**、上下文连续性/导演质量 **131 项通过**、完整 Prompt **60 项通过**、工作区 **91 项通过**。末次自动动作组装调整后重新执行完整 Prompt 60 项、工作区 91 项及相关连续性 4 项，重叠专项不重复计数；工作区整组退出 0，包含两类不同素材各 8 轮完整流程。最初的失败及替身修正已单独定位，不将早期失败运行描述为通过。对实际失败来源另做只读字段回放：旧校验拒绝、精确保留通过、改值后兼容失效；完整生产闭环使用隔离存储与离线模型替身。

前端类型检查与正常构建、目标 ruff、结构及差异检查通过。标准 `python -m kantoku serve --no-browser` 在 RuntimeManager 隔离会话启动，首页与作品 API 均 HTTP 200，退出 0，无 WAL/SHM，自有会话已清理。无新增或删除业务文件、依赖、表、迁移、Provider 或执行系统；未写入生产作品、参考图、历史请求和账单，也未重启用户当前服务。真实模型语义及外部图片质量未作付费验收；运行中的后端须重启加载修复，可直接在现有分镜重新生成完整 Prompt，无须重建作品。


### 逐元素视觉延续与本机 Codex 任务交接（2026-10-22）

- **归属和复用**：在原 Comic Workspace、Prompt Compiler、图片分析和 Core Artifact/修订链上扩展。新增 `domains/comic/visual_continuity.py` 专门处理原来没有的逐对象属性观察、来源核验和合并；它是已采用图片的派生证据，不是第二套 Conversation Memory、媒体库、Compiler、Agent 或 Runtime。没有新增依赖、表或迁移，也没有修改首页、Commerce、供应商接口、图片预算或导演人工确认。
- **触发和依据**：上传、采用和 GET 仍不调用模型。下一轮真正选中采用图时，用现有共享视觉出口观察一次，并沿该图片实际绑定的不可变 Prompt 恢复祖先，兼容旧单图来源。观察及成功快照按图片、采用版本和输入依据复用；来源只认同作品的真实采用记录、Artifact、版本及原字节。历史采用参考不因后续换选而被当成最新图片；失效、跨作品、指纹变化及未知调用仍明确停止，不改为无参考重画。
- **保持和变更**：面容、服装、具体道具、环境、风格等按对象属性分别保存元素 ID、值、原图与采用版本；本轮可见性及公开观察独立保存，不能升级为用户原话。背面、遮挡和暂时出画保持原来源，未经授权的差异只成为 advisory。修改同时定位叶子字段与具体元素；必要的位置/遮挡联动可以改变对应属性，不能释放整个人物或世界。仍未出图或未被新图证实的修改保存为 pending，后续同对象同属性的新要求覆盖旧要求；要求红衣却观察到蓝衣不会静默更新为蓝衣。失败只保存诊断和原输入，既有设定及用户图片不覆盖。
- **模型输入与分析**：模型只接收公开属性和来源 ID，图片字节逐张去重发送，不为每个属性重复展开整份图片元数据。原 Compiler 输出保留逐元素参考及待落实修改，原图片分析实际带上不同轮次的参考图，观察问题仍由原工作台展示；新 Prompt 预览也显示偏差提示及每张参考图的用途。结构和来源可验证，模型的视觉判断及外部生成质量不能由替身测试证明。
- **本机交接接口**：`POST /api/comic/shots/{id}/prompt/codex-task` 接收 request_id、conversation_id、expected_project_version、expected_prompt_version、expected_shot_version。首次导出核验当前已确认导演和完整外部 Prompt；使用 RuntimeManager 保存不可覆盖的 task.json 和真实参考图副本，登记原 Core DOCUMENT Artifact 和 Comic 修订。重复交接返回同一快照；原图之后变化不会改写已导出的副本，任务或副本损坏则拒绝交接。缺图、超出八张参考、过期来源和跨会话均不静默降级。同画面的本机导出复用原请求锁，文件发布仍为 no-clobber。
- **用户操作**：镜头卡片及生图指令页新增“复制 Codex 任务”。只复制一条包含本机任务路径和指纹的短指令；任务明确要求 Codex 查看每张图，并实际用全部本地参考图参数（内置 image_gen 的 referenced_image_paths）调用可用生图工具。复制失败保留已保存的短指令，晚到响应不能跨作品写剪贴板或反馈。结果继续由用户上传和采用；导出不调用生图、不自动唤醒外部 Codex、不自动回传，也不把短指令成功复制称作外部已接收或真实生图成功。旧 Prompt 的单图交接保持兼容；后续新的创作轮次建立逐元素来源，不改写历史 Prompt。
- **费用和部署**：新增的视觉观察及必要的元素定位复用现有单次尝试的文本/视觉出口，可能产生模型费用，不宣称免费；本轮没有真实付费模型调用。实际配置须支持所需的多图视觉输入。部署需重启现有后端并刷新页面，静态文件由当前前端正常构建更新。没有改写生产作品、图片、采用指针、历史请求或账单，源码未提交或推送。

工程验证分组：Workspace/动态导演/上下文连续性 **203 项通过**；语义媒体/HTTP **96 项通过**；完整 Prompt 与逐元素视觉 **74 项通过**；图片分析文件 **43 项通过**。其中初次三文件组合为 115 通过、1 项并发导出失败，按原请求锁修复后重跑 Prompt/视觉组通过；末次公开观察、紧凑输入和回传分析补充后，视觉文件 **9 项通过**，新接口/视觉相关子集 **14 项通过**，这些子集不重复累计。覆盖正脸→背影→露脸的真实隔离导演/确认/分镜/Prompt/上传/采用/导出链，第一张脸与第二张服装的独立原始字节，以及回传分析确实接收目标图和两张历史参考；另覆盖多人定向换装、道具出画保留、未完成修改、授权但颜色不符、服务重建、无效结构/引用/私有字段、跨作品及文件损坏。所有视觉及文本判断是明确离线替身，没有对真实画面效果作通过结论。

前端 **235 项通过**，类型检查及正常构建通过，改动文件 ruff、结构和差异检查通过。全仓 ruff 另报原有 `context_declaration.py:25` 的未使用 dataclasses.field，已对照 HEAD 确认，未扩大本次范围处理。标准后端入口在 RuntimeManager 自有隔离会话启动，首页、最终静态 bundle 与作品 API 均 HTTP 200、退出 0、无 WAL/SHM；前端开发入口 HTTP 200。验证会话使用完毕即清理，未重启用户当前运行服务。真实 Codex 生图工具接收与画面质量仍须另行实测，不能用任务文件、剪贴板或离线模型替身冒充实际生成验收。

### 修复：视觉元素定位在新方案建立前报错（2026-10-22）

现场 `ERR-E0E78DBA / trace-80fcc0cd05c0` 已完成图片观察和文本调用，在 `workspace._visual_change_targets` 的返回结构/身份校验处失败，新方案尚未建立。原非法响应没有保存，不能确定具体错在数组层级、数量还是身份。代码确认该新增定位步骤只有文字约定，没有逐项身份枚举及有界纠正；而且没有对应既有属性也会请求模型，前项合法、后项非法时会部分修改内存中的权限。这是上轮离线替身始终返回规范结构留下的覆盖缺口。

- **范围与复用**：仅修正原 Comic Workspace 方法、既有两份测试及本手册，复用共享单次文本出口、原图片/Prompt 来源和统一日志。本次故障修复无新增文件、依赖、表、迁移、Provider、Agent 或 Runtime，不改变外部出图、导演确认、上传和采用流程。
- **定位契约**：先核对已有显式元素身份；显式空列表保持为空，不再重新猜测。不存在相应既有属性时没有可释放的旧身份，因此无需追加定位调用，其他元素继续保持。确需定位时，按原字段提供具体合法身份枚举和等长分组 Schema；单项请求的一维字符串数组可无歧义归一，兼容空 question，不能把某人的衣服权限扩成另一人的面容。
- **一次纠正**：只纠正已收到的坏结构，使用原请求、固定字段/原话、安全位置/类型及核验后的身份片段；不回传非法响应、未知身份或私有字段。合法分组原样保留，坏分组中的合法身份不能丢弃，原分组数量不能增加；纠正后再次严格校验。全部成功后统一采用权限，失败不留下部分修改。每次调用前检查容量；传输失败、真实歧义及持续错误仍明确停止，不重发结果未知的调用、不退化为无参考生成。
- **诊断和费用**：现有错误日志增加定位阶段、安全错误坐标/类型、响应指纹及原 trace/project 关联。仅必要的结构纠正可能增加一次文本调用，不宣称免费；本轮未调用真实付费模型，未改写生产作品、图片、采用指针、历史请求或账单，也未重启正在使用的后端。旧失败请求仍返回其原结果，部署后需重新发送创作消息。

验证：新增定位专项先在旧实现复现 **13 项失败、2 项通过**，修复后该专项 **15 项通过**，随后补充显式身份、混合坏分组及完整三轮重放。最终 `test_comic_visual_continuity.py` 与 `test_comic_workspace.py` 合组 **121 项通过**，含正常与坏格式两条真实隔离存储的三轮导演→确认→分镜→Prompt→上传/采用→参考图导出/回传分析链及同请求重放，模型判断均为离线替身。Prompt/上下文组合初跑 **157 通过、1 项旧文案断言失败**：断言未包含上轮已增加的“姿态”标签；修正为同时核验字段标签和原话后，Prompt 整文件 **65 项通过**，原组合中上下文 **93 项通过**，不把子集重复计数。

改动文件 ruff、结构及差异检查通过；前端类型检查与构建输出到 RuntimeManager 自有隔离目录通过，未重建仓库静态文件。标准后端入口隔离启动，首页、现有静态文件和作品 API 均 HTTP 200；前端开发入口 HTTP 200。后端正常退出 0、无 WAL/SHM，自有验证会话已清理。工程复现已验证，真实模型返回及外部生图质量仍未作付费复测；运行中的后端需重启加载修复。

### 聊天模式连接验证：暂停进度（2026-10-22，尚未验收）

用户要求今天保存，次日再回答账号权限问题。本节仅记录未完成工作，不是发布说明；前文测试结论不适用于下面的新增代码。

- **第一道门槛仍未通过**：必须在用户实际 Codex 聊天模式验证短指令取任务、读取两张真实图片、生图工具实际使用两图及真实输出。账号权限、私有隧道及目标聊天入口尚未确认；本机未发现 tunnel-client、TUNNEL_ID 或相应运行密钥配置。不能据此断言账号无权限。官方设置页 `https://platform.openai.com/settings/organization/tunnels` 已打开，浏览器读取连续两次超时，停留在“请稍候…”；等待用户次日确认“可创建隧道 / 无权限 / 无法打开”，不要索取聊天中的密钥。OpenAI Developers 尚未安装或连接。
- **本轮已保存的实验代码**：`pyproject.toml`/`uv.lock` 增加可选 `chat-bridge` 依赖（官方 MCP SDK 2.3.0，原有依赖版本未升级）；`shells/chat_bridge.py` 只开放读取固定任务和逐张返回真实图片两个 stdio 工具，启动时必须明确指定任务号白名单，不开放 HTTP 监听或任意远端路径。标准 CLI 新增实验性 `chat-bridge --task-id …`，SDK 按需加载；未更改工作台主操作或启用聊天流程。
- **复用边界**：`ComicPromptStore` 增加既有 Artifact/任务修订的固定快照读取与图片读取，不启动 StudioApplication、不运行迁移、不选新图或调用模型；检查任务归属、有效生命周期、固定内容哈希、参考图次序和容量。沿用原数据、版本、Artifact、DatabaseManager 及 RuntimeManager，无新增 Agent、执行引擎或业务存储。旧任务仍是 `codex-image-task-1` 和原完整 Prompt，不能称为新 `execution_prompt` 已实现。
- **验证未完成，不能发布**：新增连接测试初跑为 1 通过、8 个夹具初始化错误；共同首因是离线 `image` 配置缺少 `output_dir`，尚未执行到 MCP 图片往返断言。另已发现新测试误用了不存在的 `ComicProjectStore.history`，须改用原修订查询核验读前读后不变。ruff 报测试新增部分四处行长，生产改动文件当次 ruff 通过。下一次先修正这些测试夹具/断言和格式，再跑真实 SDK stdio 两图与重启重放、损坏文件、跨作品白名单及缺少可选 SDK 的回归；本轮还未重跑构建、标准启动和完整回归。
- **后续顺序**：先等账号确认并完成实际隧道/聊天验证，不能用本地协议测试或缩略图代替。门槛未通过，不继续稳定对象/属性授权、按当前镜头筛选参考图、精简执行说明、聊天交付类型及主界面改造。真实付费验收预算须按原规则另行确定。

未调用真实付费模型，未重启用户服务、改写生产作品或图片、清理用户历史，未提交或推送 Git。当前新增入口明确为实验性，代码保留在本地工作区等待续做；不要将其标为可用。

### 统一作品一致性与固定生图输入（2026-10-23）

本节替代上一节的隧道实施方向。用户已取消密钥/MCP/隧道路线；实验 stdio Shell、CLI 子命令、可选 MCP SDK 声明/锁定依赖和实验测试均移除，原本地任务交付及快照校验保留。不新增 Agent、执行引擎、业务数据库或模型依赖，不改生产数据和历史文件，不自动启用内部付费生成。

- **对象属性契约**：原视觉记录追加 `visual-elements-2`。同一对象跨 Prompt 栏目使用稳定 `entity_id`，属性使用独立语义标识，`field` 仅供兼容展示；新对象按本响应明确的临时分组分配身份，不以名称作为永久身份。已建立对象和属性不能通过更名、换栏目或重复 ID 被重建。旧 `visual-elements-1` 修订继续读取；升级追加派生记录，保留原属性值及像素来源。新版固定任务产生的图片不得以旧观察契约静默降级，旧手动路径保留兼容。
- **本轮选择与权限**：现有工作区选择来源后，必要时用原文本出口编译对象权限和所需属性 ID；没有图像观察之前不猜对象 ID。`visual-plan-2` 使用具体对象、属性和原话区分修改、首次设计及新增对象；未知外观不得冒充已有定稿。原 Prompt 栏目的修改范围仅维护旧文本兼容，不决定新版对象授权。完整有效设定仍保留，编译只投影本轮元素；未落实的稳定修改继续保存，出画对象的待修改要求不成为本轮图像输入，旧动作/构图/摄影不继续累加。必要观察和语义整理可能增加现有文本/视觉调用，工程验收使用离线替身，不宣称免费或真实语义通过。
- **执行正文**：原 Prompt 修订及 Core PROMPT Artifact 保存 `execution_prompt`，Compiler 版本升为 `comic-prompt-6`。从已校验当前段落完整投影，保留必需内容和负向约束，不复制历史原话快照、文件名、路径、指纹或逐元素审计。参考图编号与简短用途进入实际执行说明，内部适配不会只传图片而丢失用途；布局、动作和摄影服从本轮镜头。原四段正文及完整来源继续在“查看来源”中读取，旧 Prompt 不直接重写。
- **共用固定输入**：原导出接口委托同一个 `export_generation_task`，创建 `image-generation-input-1`，固定作品/分支、镜头、Prompt/视觉版本、执行说明、必要图片、允许变化、首次设计项及审计指针。图片副本和 task.json 复用 RuntimeManager、Core DOCUMENT Artifact、原请求锁及不可变修订，重放不重选图或调用模型。资料放在 artifacts/image-inputs 下按作品隔离的紧凑目录；完整身份仍在任务及 Artifact 中，no-clobber 阻止短目录碰撞覆盖。路径为 Windows 原子暂存预留空间，不改变用户文件位置。旧 `codex-image-task-1` 及其原路径仍可核验读取。
- **内部模型适配**：现有共享图片能力按固定输入 Artifact ID 读取同一份正文和原始图片字节，检查会话、生命周期、任务/图片哈希、顺序、边界和容量；不接受另一套临时参考替换该输入。实际 Provider 在预算认领前验证全部参考图，不静默减图、拼图或改成纯文生图。固定输入指纹进入原提交指纹，阻止同一请求编号复用其他输入；原预算、单次提交、未知账单及旧单图调用保持兼容。生成产物保留输入 Artifact/指纹/图片关联。未来生图界面再接入该契约：作品内进入沿用当前分支，独立进入选择作品或创建最小记录，复用原工作台登记和采用，不复制一致性存储，也不把当前专业导演门禁强加为未来独立入口的用户步骤；本次没有提前新增该界面或模型接入。
- **候选核对和采用**：外部上传可指定 generation_task_id，未指定时关联该 Prompt 已导出的同一固定任务；跨作品/会话/镜头/版本拒绝。分析读取图片原绑定的 Prompt 和参考，不借当前新方案替代；旧图片的原说明缺失时公开限制，仅分析可得像素依据。已绑定固定任务的过期结果不能覆盖当前新 Prompt。采用额外变化须明确选中该图依据中的对象及属性并追加采用版本；观察仍须证实变化，隐藏、未满足或未经授权的部分保留旧来源。页面提供可选的逐属性接受入口，不自动重生。
- **Codex 交付状态**：主操作显示“复制生图指令（待验证）”，返回作品资料夹位置和短任务指令；首次须让目标 Codex 本地项目实际具备该文件夹的读取权限。复制成功不再显示“即可生成、无需附图”的未经证实承诺。状态始终为 unverified，尚未在用户目标生图聊天完成双图读取→实际传入生图工具→真实输出；没有把文件存在、缩略图或离线模型返回当成这道验收通过。运行中的旧后端须由用户重启加载本轮代码，未自动重启。

验证记录：最初五文件组合为 **323 通过、4 失败**，两项为旧指令不存在的历史图片兼容问题，已修复；另两项为旧断言要求把图片指纹/审计提示放入执行正文，改为核对内部来源和实际目标要求。该四项对应专项复跑通过。随后 Prompt/视觉/图片分析组合 **145 项通过**；新增固定任务过期保护后，Prompt/视觉整组 **102 项通过**。补充未知外观必须首次设计、三轮对象契约/明确接受新脸/拒绝其他对象的专项 **3 项通过**，旧身份升级及出画待修改 **2 项通过**；各组重叠不累加。资料夹按作品分层的初版还触发 Windows 路径长度错误，改为紧凑目录后，原字节交付与过期结果两项复跑通过。共享生成及原预算/未知任务测试 **19 项通过**。后续修订以实际专项结果为准，不把早期失败组描述为一次全绿。

前端 **237 项通过**，包含真实组件中额外采用只发送所选属性、执行说明优先及未验收交付提示；类型检查及正常构建通过并更新后端静态页面。目标 ruff、结构及差异检查通过。标准后端入口在隔离会话启动，首页和静态脚本 HTTP 200，正常退出 0、无 WAL/SHM；最初以 Windows 强制终止探测退出的那次为退出 1，未记为正常关闭，后续使用正常中断完成退出验证。`npm run dev` 隔离启动 HTTP 200，自有进程及测试会话用完已清理。没有真实付费模型调用或生产作品/图片/历史改写，没有提交或推送。真实 Codex 双图交付和画面质量仍须单独验收，不能据上述工程结果标记可用。

### 续创失败：任务状态结构与本机代理连接（2026-10-23）

现场 `ERR-89259382 / trace-850f069ffced` 的两次理解请求都取得真实模型响应。第二次先在 `tasks[3].status` 遇到枚举校验错误（另记 `ERR-DF54E51C`），旧工作区适配器随后退化为独立新任务；在图片来源选择调用时，本机代理连接又以 Windows 10048 失败，尚未建立新画面。未保存的非法状态原值不能从日志还原，不把测试替身的状态值称为实际模型原话。

- **最小修复**：原 `ConversationContext` 对仅发生于任务状态的枚举错误纠正一次，只允许返回明确位置的合法状态；目标、来源选择、已合法任务状态、事实和动作均不改写。理解与摘要复用同一方法及现有预算审计、单次文本出口；来源/事实/混合结构错误仍严格核验。原 Shell 将这类可纠正响应交还共享解析，不提前丢弃已选来源；纠正超时或持续无效明确失败，不伪装成新作品成功。
- **安全和兼容**：非法状态原值（可能为任意私有对象）不进入纠正输入；纠正在原 ValidationError 处理范围外执行，后续传输异常不携带非法模型正文的异常上下文。重复、遗漏、越界位置、改写事实及不合法状态均拒绝。状态集合及历史数据不变，不新增依赖、文件、表、Runtime 或 Provider；只有已收到坏状态时可能增加一次原文本调用，不自动重发未知模型请求。另修正现场源码 `conversation_context.py` 文件首部多余缩进，避免下一次重启发生语法错误。
- **网络实况**：只读检查显示该进程经系统代理访问模型，当前没有端口耗尽证据；无密钥、不提交生成的 HEAD 探测经代理及直接连接均返回 401，表明探测时服务可达。未改代理、端口或网络系统设置，未通过自动重试掩盖故障，也不能把当下连通称为彻底解决间歇性 Windows 10048。截图请求仍保留原失败结果，重启加载修复后须重新发送创作需求，刷新或查询原请求不会重跑模型。

验证：新增状态纠正专项 **12 项通过**，覆盖第四任务状态错误、未知私有对象、不扩大权限、纠正超时及工作区不丢失来源。随后完整执行 `tests/test_conversation_context.py`、`tests/test_dynamic_director_context.py`、`tests/test_llm.py`、`tests/test_comic_workspace.py` 四文件，全部通过（专项属于其子集）；目标 ruff、结构与差异检查通过。前端类型检查及正常构建通过，无前端源码变更；标准后端入口隔离启动首页与最终脚本 HTTP 200、正常退出 0、无 WAL/SHM，自有会话已清理。未调用付费模型、改写生产历史或重启当前 PyCharm 后端；真实新一轮创作仍须人工验收。

### 启动维护提速与 Codex 本机工作任务（2026-10-23）

用户改用 Codex 工作模式，未来仍需在项目内生图。本轮只优化原启动维护和固定任务交付，保留上一节及统一一致性的修改；不提前新增内部生图界面或启用付费 Provider。没有新增业务文件、依赖、表、迁移、Agent、Runtime 或第二套任务存储，没有改写生产作品、图片、历史请求和账单。

- **启动阻塞**：现场旧进程停留在配置日志后且未监听页面端口。实际库的隔离副本初始化约 0.096 秒，没有待恢复媒体任务；原启动却要同步枚举两千多项缓存并逐项开数据库事务。已用受阻缓存枚举的确定性测试复现旧启动无法返回，不把现场全部等待时间归因于这一处。原 `RuntimeMaintenance` 改在已有维护线程中立即执行首次扫描，SDK 暂存认领仍在启动阶段完成；不是新增后台系统。缓存以最多 64 项复用原 `FileCatalog` 同一事务登记，保留路径、生命周期、旧文件、租约及链接边界。关闭时停止并等待原线程，执行已登记文件及数据库的原清理，不再重新扫描整个外部缓存树；错误继续进入原日志。
- **可诊断性**：标准 `serve` 入口增加应用就绪、后台维护就绪及 HTTP 就绪阶段耗时。主线程无需等待缓存扫描，后续卡顿也可按阶段定位。没有修改端口、代理设置或自动重启用户进程。
- **固定任务一致性**：原导出优先读取 Prompt 修订中已经保存的 `execution_prompt`，不在导出时用新版格式器重编；历史缺少该字段的 Prompt 继续兼容。新增回归先复现旧导出正文随格式器变化，再核对 Codex 导出与通用内部适配均取得同一已保存正文。真实图片版本、用途、顺序、快照校验和原不可变 Artifact 仍由同一输入契约提供，旧任务不重写。
- **工作模式交付**：镜头及生图指令页主操作改为“复制 Codex 工作任务”，反馈说明首次将返回的作品资料夹关联到 Codex 本机工作模式，后续向能够读取该目录的工作对话粘贴短指令。保留旧图文复制、下载及失败恢复。官方说明区分本机资源访问和无本机文件权限的云端执行，不能把本地路径交付描述成任意聊天可读，参见 [Work 官方说明](https://learn.chatgpt.com/docs/get-started-with-work)。`codex_delivery.status` 仍为 `unverified`：尚未在用户目标工作对话完成两张真实参考图读取、实际传入生图工具及真实画面验收。未来内部界面仍须复用已有固定输入、预算、请求及结果采用机制，本轮没有提前建立另一条生成链。

验证：新增缓存启动、批量登记、事务回滚、维护错误及固定正文回归均通过；其中启动阻塞和固定正文两项先在旧实现失败，再由生产修改修复。完整后端 **1395 项通过、1 项跳过**，跳过原因是当前系统不允许创建符号链接，未记为通过；前端 **237 项通过**，类型检查与正常构建通过并更新后端静态文件。目标 ruff、结构及差异检查通过，重叠专项不重复计数。

标准后端入口使用实际数据库的一致性隔离副本和 1024 项自有缓存启动，从进程创建到首页 HTTP 200 约 **3.404 秒**；应用、后台维护和 HTTP 就绪阶段分别约 63、110、141 毫秒。最新静态脚本及带原会话认证的作品 API 均 HTTP 200，正常退出 0，无 WAL/SHM。副本仅清除其自身的运行文件目录登记，避免测试维护接触生产路径；父进程的副本数据库连接已在子进程启动前关闭，退出侧文件检查针对子进程。`npm run dev` 隔离启动 HTTP 200，自有测试进程及会话用后清理。此耗时是该隔离条件下的实测，不承诺所有机器或生产状态均相同。没有真实付费模型调用，没有自动重启当前 PyCharm 后端，也未提交或推送；用户须停止旧后端后重新启动并刷新页面，真实生成及画面一致性仍单独验收。

### 图片工作台强化：模块一，需求整理与有界纠正（2026-10-23，待人工验收）

本模块只处理手动要求和指令核验的可靠性。局部裁剪、建议分级、定位框/授权范围分离及两图退步评测仍为后续模块；按修改 → 测试 → 人工验收 → 授权提交 → 下一模块推进。规则不绑定题材、文件名或截图中的具体对象。

- **工程基线**：修改前原图片审核/语义媒体/完整 Prompt 三文件 133 项通过，工作台前端 25 项通过。当时 constraints、prompts、image_review 和 ImageOptimizeStage 的内容身份分别为 `289bcdc1f85d6081e51282f907d1845f7a2e7b4f`、`ab0349b6b09a8e8bdb0e71a4bde5841def474dbd`、`53aeccd07c493c95227c5d9f588c1e643f48031b`、`2b8a93289bdd78b5a0c03ba7060fe62d1e2a16e8`；其中已有上一轮未提交改动，不能按 HEAD 整体回退。这是工程基线，不是已采集的真实模型质量基线。
- **需求提取**：原 `normalize_constraints` 接收原文实际绑定的人工授权范围，代词不再仅凭宽泛 target 推断为整图。对象歧义/互斥输入与提取遗漏、编号或候选范围错误分开表达；只有关联有效需求编号的真实范围问题才形成 `needs_clarification`。其他调用方没有编辑依据时保持原严格行为。
- **整轮一次纠正**：原提取、编译与最终独立核验共享一个 `EditCorrectionBudget`。只对已收到的坏结构、来源/证据或覆盖结果纠正一次，再经过原独立核验；通常最多增加两次原模型调用，不叠加各阶段重试。错误原始响应和未知字段不进入纠正输入。传输/容量/结果未知不进入纠正；已指出正文矛盾但执行正文和负向均未改变时，不追加审核碰运气，也不因摘要变化放行。
- **候选和业务结果**：原 Compiler 在独立核验前将 Schema 合法的公开候选保存到原 Run，绑定图片、输入及范围来源。`edit_status=ready` 才创建可确认的原 PROMPT Artifact；`needs_clarification/review_unavailable` 没有正式指令 ID，只在原媒体请求中保存结果，Run 为 WAITING，需求与候选保留。媒体请求的 completed 仅表示本次操作已有可重放结果，不代表图片或正式指令已完成。系统核验问题保留原 error_id/trace_id；实际传输异常仍为 FAILED，候选不丢失。
- **页面及恢复**：原工作台原地显示具体需求或系统核验问题，未核验候选折叠且不能确认/用于结果回传。刷新从原任务恢复 WAITING 结果，不把它当作仍在运行而永久锁住，也不自动提交模型。输入变化清除旧显示，原指令、报告、版本和历史仍保留。旧成功响应未提供 edit_status 时继续兼容。
- **边界**：复用原 Domain、Compiler、共享文本/视觉出口、Run、Artifact 和媒体请求认领；无新增业务文件、依赖、表、迁移、Provider 或 Runtime。Pillow 留待模块二，不在本模块提前安装。没有改写生产图片、方案、用户历史或账单，没有自动重启用户服务，也未提交或推送。

最终验证：图片审核/语义媒体/完整 Prompt/旧 Prompt 四文件 **145 项通过**，其中图片审核增加 8 项故障与纠正边界用例；前端全组 **239 项通过**，其中新增两种 WAITING 结果刷新与修改后的失效检查。前端类型检查及正式构建通过并更新原静态资源；仅有现存大 bundle 的构建提示。改动 Python 文件 ruff、结构检查及差异检查通过。标准后端 main 入口在 RuntimeManager 自有隔离环境启动，首页与最终静态脚本 HTTP 200，正常返回 0、无 WAL/SHM；`npm run dev` 自有隔离环境 HTTP 200，停止后端口关闭。两次启动自有会话均已直接清理。

本模块未进行真实付费模型、24 张人工标注图片或 12 组外部改图对照验收，不能据上述离线结果宣称画面质量或抽卡次数已改善。部署须正常重启后端并刷新页面；历史失败请求不自动重跑，新整理请求才使用本模块逻辑。模块一人工体验待用户验收，后续模块尚未开始。

### 图片工作台第二版分析上传超时（2026-10-23）

- **现场证据**：`ERR-79E7ED92 / trace-aebeee18fe64` 的视觉调用一次尝试约 34250 毫秒，异常链为经 HTTP 代理发送请求体时的 `httpcore2.WriteTimeout → httpx2.WriteTimeout → APITimeoutError`，不是前端计时器或需求核验拒绝。尚未获得模型响应，费用未知不能按零处理；不能据此断言供应商尚未接单。现有认证只读 API 核对 v2 为 3153062 字节、v1 为 3460610 字节，均为 1774×887 PNG，显式参考数为零；第二版实际更小，没有证据支持按第二张或题材增加特殊分支。
- **通用传输副本**：在已有 `capabilities.image` 增加内存中的 `prepare_vision_image`，由原 Shell 视觉装配入口复用。核对格式、解码及像素容量，按浏览器显示方向处理 EXIF；优先保留分辨率，用高质量 JPEG 减少大不透明图片体积，透明图继续用 PNG。默认最长边 2048、单图 1 MiB，确有必要时才缩小；小图保留原字节。输入/输出指纹、显示及发送尺寸、方向处理、是否有损及实际字节进入视觉输入说明；日志只记数量、字节和处理耗时，不写原图或 base64。保持图片与用途顺序，不丢参考图，也不改原文件、内容身份、版本和原归一化坐标，不新建媒体版本、缓存或业务表。
- **依赖与范围**：用户此前明确允许的 Pillow 本次用于故障修复，声明 `pillow>=11.3,<14`、锁定并安装 13.0.0；没有更新其他已锁定依赖。项目此前无后端图片重编码能力，移除该依赖会失去上述传输准备。本次不提前实施局部裁剪、建议分类、范围扩展或后续模块，不改模型、代理、费用和确认策略；现有未提交改动保留。
- **等待与诊断**：增加可选 `vision_timeout_s`，缺省沿用原文本超时、显式调用参数仍优先；本机和示例配置视觉等待设为 60 秒，聊天等待不变。另增有界副本尺寸/字节配置。原共享出口按实际传输异常类型区分连接、上传和等待响应超时，保留原异常链和 error_id/trace_id；工作台仍 `single_attempt=True`、SDK `max_retries=0`，没有结果未知后的重发、切换供应商或假成功。该配置是各网络阶段的等待参数，不承诺请求总耗时严格等于 60 秒。
- **原图本地测量**：通过原认证内容网关只读读取，v2 的副本为 744475 字节（减少 76.4%）、1774×887，准备约 87 毫秒；v1 副本 784281 字节（减少 77.3%），尺寸同样未变。两份原字节指纹前后一致，没有保存额外图片。仅证明输入体积改善，不证明真实网络或视觉判断已验收。

验证：LLM、配置与图片审核组合 **121 项通过**；后续补充的传输/方向/透明度/尺寸专项 **5 项通过**（含前组的重叠子集，不累加）；共享生图及视觉延续 **57 项通过**。实际 SDK 配合明确离线 MockTransport 验证大原始请求上传失败而有界副本可以返回，以及三类超时均只发一次并关闭客户端；模拟阈值不冒充真实代理限制。目标 ruff、结构和差异检查通过，前端类型检查及正常构建通过，静态脚本哈希与上一模块相同。标准后端 main 在隔离 Runtime 启动，首页及静态脚本 HTTP 200、正常返回 0、无 WAL/SHM，自有安装缓存和启动会话已清理。

没有真实付费复测、生产业务数据或图片改写、自动重启、提交或推送。用户须正常重启后端并刷新页面，然后直接点击现有 v2 的“分析图片”，无需重新导入；原失败请求不自动重跑。真实代理连接和画面分析质量仍待本机实际操作确认，不能将离线成功称作此网络故障已经彻底消除。

### 多画面创作的图片来源容量处理（2026-10-24）

现场 `ERR-A2C9000F / trace-9bb107385fd2` 的会话理解、事实纠正及最终上下文装配均已完成，最终估算为 4278，低于配置的 18000。随后失败在原 `ComicWorkspaceService._continuity_decision` 调用模型之前的容量检查。没有明确 Run 来源时，原实现会同时装配作品内候选画面的完整图片身份、文字依据、公开视觉属性及整份 resolution；这个独立步骤没有按需选择来源，不能把其容量异常直接解释成用户没有说清楚。

- **范围与复用**：仅维护原 Comic Workspace、原测试及本手册；作品新画面和当前方案修订继续调用同一个 `image_continuity`。Core 记忆、模型配置、Provider、Compiler、原 API 及 Run/Artifact/请求恢复保持原契约；无新增文件、依赖、表、迁移或第二套记忆系统，不改写历史记录和图片。
- **按需读取**：完整请求可容纳时保留原调用。超限才用同一注入文本出口选择来源；目录保留每个候选的来源说明、完整 Prompt 分段、公开视觉属性、待落实修改和方案/Prompt 版本，图片身份缩为原 image/version/artifact，来源选择不重复携带全部任务事实或权限 Schema。不同文字状态即使引用同一张图也不合并，不按最新或列表顺序选图，不截断保持要求、不提高容量上限。来源选定后，从原内存快照取该候选完整信息及原 resolution，继续原权限核验；不能借第二阶段换图或切成独立重画。
- **有界与歧义**：来源选择只允许候选 ID、独立画面的逐字依据或澄清问题，不能返回修改权限；候选 ID 与问题同时存在时仍先澄清，不消耗详细核验或吞掉问题。仅超限路径增加一次可能计费的原文本选择调用，原权限结构纠正仍最多一次；每次调用前检查容量，传输未知均不自动重发。目录仍过大时要求明确来源，单个来源的完整依据仍超限时停止并保留原设定，不能假装通过。原图片/采用版本、内容和归属在详细判断后继续核对。
- **诊断与真实边界**：新增完整装配、目录选择及权限核验阶段的估算量、上限和候选数日志，带原 trace_id/project_id，不记正文。通过当前认证只读 API 读取作品资料并在内存重建目录，确认这些公开材料可放入原 18000 上限；该测量没有完整还原原失败请求及全部缓存输入，不冒充逐字现场回放或真实模型验收。模型来源选择质量、实际外部画面效果仍须人工验收。

回归先用八个真实隔离上传/采用的候选及固定容量复现旧实现的同类错误，再验证按需选择后使用同一早期图片的完整指纹与限定修改；同时覆盖歧义问题、独立画面、禁止换分支、采用版本变化、同图不同文字状态、目录仍超限、无效结构、越权字段及传输未知。没有真实付费模型调用或生产数据写入；运行中的旧后端需正常重启，历史失败请求仍按原请求保存，不自动重跑。

验证：Workspace/逐元素视觉/动态导演/上下文连续性四文件 **257 项通过**；图片审核及完整 Prompt 两文件 **123 项通过**。末次“候选与问题同时存在时优先澄清”的修订后专项 **14 项通过**，属于前组的重叠子集，不重复累计。Python 189 文件语法扫描、目标 ruff、结构及差异检查通过。前端类型检查及正常构建通过，静态脚本哈希未变；仅有现存大 bundle 提示。标准后端模块入口及 PyCharm 启动脚本在自有隔离 Runtime 均返回首页/静态脚本 HTTP 200、退出 0、无 WAL/SHM；`npm run dev` 隔离启动 HTTP 200，停止后端口关闭，自有会话均已清理。未自动重启用户当前后端，未提交或推送；重启并刷新后，在原作品重新发送需求即可，不需要重建作品或重导图片。以上是离线流程验证，不代表真实模型来源选择或外部画面质量已经验收。

## 图片工作台强化与多轮消息来源修复（2026-10-24）

本次用户要求继续完成全部强化功能；保留当前未提交改动，未执行 commit/push。按原有层级扩展：Core 仅校正来源身份；Comic Domain 理解意图、整理计划和核验；通用图像裁剪留在原 Capability，供应商请求继续走 Shell 注入的共享出口。未新增表、Runtime、Compiler、预算身份或自动内部改图路径。

- **多轮对话**：`ERR-146FD97E` 对应模型把真实对话消息标成 Run。根据本轮已限定的消息及来源目录，只在同一 ID 的真实类型唯一时校正标签；消息不继承伪造的 Run 版本。未知 ID、类型碰撞和真实版本错误继续核验，不按 ID 前缀猜测，不删除记录或改成最新 Run。先前的图片来源容量处理和原上下文上限继续有效。
- **观察与建议**：`evidence-review-2` 要求明确偏差、可选优化和无法确定分级，附对象、属性、可见证据、目标、收益、保留内容及风险。明确偏差缺少证据、把握不足或风险较高时降级；无明确依据优先建议保留原图。常规一次视觉调用，关键疑点最多追加一次全图加至多三个局部的批量复核。原图先验哈希；Pillow 按 EXIF 显示方向裁剪，记录像素框、方向逆变换及原文件哈希；兼容 CMYK 和透明图。裁剪只在内存传输，不创建图片版本，不将放大称为真实细节恢复。
- **统一计划与人工意图**：新轮次 `region_semantics=locator`，保存原定位框与实际影响范围；旧记录缺省 `strict`，维持原语义。先解析对象、属性、期望、保留和范围，再由原提取器和 Compiler 执行。相同对象属性的人工要求优先，保留处理来源；同向合并时人工范围优先，不采用更大的 AI 范围。重叠选区不当成冲突，互斥人工要求先简短澄清。固定资产冲突需指出约束及修改入口，不能静默解除。
- **范围与恢复**：框外影响显示建议框、原因和原范围内的替代方案，勾选默认关闭。确认绑定图片/参考/约束哈希、需求及范围修订、计划内容和所属会话/作品；输入或建议范围变动后失效。拒绝扩大时保留原需求并说明限制。待确认范围、待澄清和核验暂不可用由原 Run 保存为 `waiting`，不生成可确认 Prompt；可恢复合法候选和逐项原因。范围等待期间禁止底部按钮重新提交；网络结果未知仍查询原请求。整个计划、提取、编译、复核共享一次系统纠正额度，跨范围确认恢复时不重置；正式指令使用 `edit-6`，旧严格路径保留 `edit-5`。
- **改图后检查**：`regression-review-2` 在原比较链路分别报告目标已解决/未解决、应保留内容和新增问题；新问题或非目标损伤会建议保留旧版，不自动采用。按已确认指令导入新版本并开启“导入后分析”时，优先进行这次目标对比，避免再做一次无关整图分析。历史打开不会自动提交。无实际蒙版时仍明确说明文字坐标不能保证框外像素绝对不变。

真实质量验收与工程通过分开记录：用户明确当前没有所需素材。新增的 `evals/image-workbench.json` 是 24 张图片、30 条机器起草需求及 12 组外部结果的待填模板，没有人工标注或真实画质结论。基线在真实改图前采集这一项仍为未完成，历史工程测试不能冒充该基线。复用 `evaluation.py` 和 `scripts/run_eval.py --kind image-workbench` 只读检查文件/哈希、输入指纹、同模型成对记录、人工声明、覆盖和返工指标，材料不足返回 `pending`；真实性和评分仍由验收人核实。缺少材料或有效付费账本时不采集、不付费生成素材、不绕过累计预算。只有真实对照减少返工后才能宣称减少抽卡。

工程验证：后端全量回归通过（一个环境相关用例跳过）；随后最终图片审核/对话上下文两文件再次通过，包含新增的 CMYK/透明图及来源类型碰撞、未知身份和真实版本边界。前端全量 243 项及最终工作台 31 项回归通过（31 项是重叠专项，不重复累计），类型检查/构建通过；目标 ruff、结构检查及 117 个源码/启动脚本的语法扫描通过。两个后端入口隔离启动均为首页/静态脚本 HTTP 200、退出 0、无 WAL/SHM；前端开发入口 HTTP 200，停止后端口关闭，自有会话移除。隔离浏览器检查了分级/证据、范围默认不勾选、禁止重复整理和确认后预览；演示响应明确为替身。生产后端未自动重启，需正常重启并刷新前端后生效；未重放历史失败请求。

## 首次生成完整 Prompt 的作品版本竞争（2026-10-24）

`ERR-45EE5775` 在模型调用前由原作品版本校验拒绝：首次创建分镜将作品从 2 推进至 3，先前开始的 Workspace 刷新在写入后才完成，旧上下文将前端作品指针回退至 2。返回后重进得到较新状态，掩盖了这次竞争。

修复限于 UI/Shell 投影：`enterStoryboard` 立即接收服务端返回的 `storyboard.project_version`、分镜及镜头，不推算 `+1`；同一作品的旧刷新不得覆盖更高版本。请求仍按作品/会话选择轮次归属，切换后到达的创建结果不能改变新页面或解锁其控件。页面可直接进入首次 Prompt 编译；后端原版本/来源校验保持不变，不自动重试编译或模型调用。只给前端既有 Storyboard 类型补可选版本字段，兼容旧响应；不新增文件、依赖、表或第二套状态机制，保留全部未提交改动。

先用有界延迟复现旧实现的 `2 != 3`，再验证创建分镜与旧刷新交错后，首次编译只提交一次且使用返回的版本 3；覆盖重复点击、再次进入不重复创建和切换作品后的晚到结果。前端全量 **245 项通过**，类型检查/构建、结构和差异检查通过；原本机任务导出/版本保护/并发复用相关后端 **5 项通过**，全部离线替身，无实际付费调用。

本机生图交接资料的生命周期本次只核查、不变更：原 `export_generation_task` 将 task.json 与必要参考图副本经 RuntimeManager 登记为 `export`，`expire_at` 为空，不属于自动清理集合。同一不可变 Prompt 重复复制复用同一快照；新 Prompt 版本可能增加独立快照。它们保留在 `artifacts/image-inputs`，防止已经交出的本地路径过期；临时目录/缓存的自动清理不代表这些交接资料会被删除。当前只读盘点为 5 个任务、7 个文件、6,622,492 字节，用户所指 JSON 为 9,430 字节且 SHA-256 一致；这是检查时占用，不是容量限额。没有删除、搬移或给历史导出补设过期时间；不进入 Git 或交付压缩包。

## 整理改图指令停在统一计划核验（2026-10-24）

`ERR-65BE0199` 的两次视觉调用均正常返回，随后统一计划未通过，尚未进入正式 Prompt 编译。旧实现把结构、来源编号、需求覆盖等错误都压成同一提示，纠正输入也只有笼统原因；没有保存原始无效响应，无法追认本次具体错误字段，不能归咎于用户表达或宣称已回放原响应。

修复仍在原 `ComicImageReviewService`：返回结构将 `requirement_ids` 限定为本轮完整需求编号，明确区分定位框/发现项编号。按既有严格模型核验每个公开项目和问题，向唯一一次纠正提供安全字段位置、错误类型及缺失的有效需求编号；已通过结构和来源核验的片段与真实澄清问题必须保留，不能纠正时换对象、改目标或消除问题。纠正通过后继续原提取、Compiler 和独立审核；整轮共享一次额度，不添加逐层重试或未核验提示词兜底。传输异常及诊断写入异常不触发再次模型调用。

现有 Run 增量保存最多两次计划响应的哈希、脱敏结构错误和明确未核验的合法公开片段，不保存无效字段值或私有调试内容，不新增表或产物系统。仍未通过时显示具体系统问题并保留需求/选区；尚未生成候选时不再声称“候选已保留”。旧失败记录不改写、不自动重放，原草稿、范围确认指纹及正式指令核验保持有效。无新增依赖、文件、生产数据改写或提交推送。

离线回归覆盖错误来源编号、坐标结构、字段遗漏、额外字段、无效 JSON 的针对性纠正，以及纠正失败后的有界停止、原请求复用、合法片段及真实歧义保留；原人工优先、范围确认和整轮共享额度回归通过。图片审核、语义媒体、完整 Prompt 三文件相关测试全部通过，目标 ruff、结构和差异检查通过；前端类型检查及构建通过，静态脚本哈希未变。标准后端模块及启动脚本在隔离 Runtime 均为首页/脚本 HTTP 200、退出 0、无 WAL/SHM；前端开发入口 HTTP 200，停止后端口关闭，自有会话均移除。未重启用户当前后端，需正常重启后重新点击整理；没有真实付费模型调用，本次工程通过不代表真实画面质量或原模型响应已经验收。

## 整图修改被误判歧义、保留项产生框及确认页死路（2026-10-24）

通过当前服务的认证只读 API 核对 `run-f410ebd05ed84046852ba2b0ecd042ee`：模型已经返回合法计划，`questions=[]`，整图亮度项明确 `edit_bounds=null`；服务端却对局部定位来源自行追加“范围不能确定”的澄清。计划另把原任务中的保持眼神、保持道具状态拆成修改项，道具保护框碰到定位框外边缘，成为截图中的额外建议虚线。UI 的长目标列表在诊断前占据固定高度，外层又隐藏溢出，具体问题和操作被挤走。这是已保存公开计划及当前源码能直接确认的后续故障，上节结构纠正通过没有解决这些执行语义和布局问题。

- 在原 Comic Domain 引入 `unified-edit-plan-2`：每项必须明确 `modify/preserve`，缺失分类走原共享一次纠正，不猜成修改。任务背景和旧约束只提供上下文/保护依据，不能生成新的本轮改变；保留优先附在修改项中，独立保留项不产生编辑区域或越界申请。原 Compiler 与两处约束核验指令使用相同语义，保护项按原定位识别，不授予修改权限。
- `edit_bounds=null` 表示明确的整图目标。原定位范围没有覆盖整图时，生成 `[0,0,1,1]` 的实际范围建议并等待一次明确确认；覆盖整图则直接编译。不能把它改成对象歧义或反复要求重画定位框。确认恢复同一已保存计划，不再请求计划模型；旧范围、输入、内容及会话指纹校验和整轮纠正额度保持。
- 原 UI 将目标/保留说明改成默认收起，诊断可正常滚动，底部提供“确认范围并生成提示词”。全部所需范围确认后才提交，部分勾选不会触发明知不完整的新请求；真正歧义的原内容不重复提交，先展示对应问题，修改原需求后再整理。旧契约的待确认结果提供“按当前需求生成提示词”，创建新请求保留草稿，不复用旧确认、不改写历史。
- 建议虚线只在当前有效的范围确认阶段显示，默认突出一项；保留项、待澄清、旧计划及最终提示词不再展示它。框是页面标记，不写入原始图片；用户定位框及图片字节、版本和历史完整保留。

没有新增依赖、业务文件、表、Runtime、Provider 或第二套 Prompt 机制，没有修改生产作品、图片、原失败请求或账单，也未提交推送。相关图片/语义媒体/完整及旧 Prompt 四文件 186 项通过；最后将分类设为必填后，图片审核整文件再次 93 项通过（包含前组的重叠用例，不相加）。前端最终全量 248 项通过，含旧等待结果恢复、长目标说明收起、整图/局部/多项确认到可见提示词、重复点击及生成后无建议框；类型检查及正常构建、目标 ruff、结构与差异检查通过。

隔离浏览器实际完成整理→范围默认未勾选→勾选→生成完整正负向提示词，页面检测建议框为 0、目标说明收起；使用明确内存替身，没有访问生产业务库或付费模型，不把此闭环称为真实模型语义验收。两个标准后端入口在隔离 Runtime 均为首页/最终脚本 HTTP 200、退出 0、无 WAL/SHM；`npm run dev` HTTP 200，停止后端口关闭，自有测试会话清理。隔离预览服务及标签已关闭；当前用户后端需正常重启，页面刷新后可保留原需求继续生成。

## 故事整理与主动校验独立、长文本可读（2026-10-25）

现场 `ERR-B7546F37 / trace-9a1bded70f4f` 实际属于 `story.generate`：已经生成初稿并完成一次内容修正，随后内部复核的字段引文连续两次未通过，尚未发布新故事。不是按钮调用了主动校验接口；非法模型原文未保存，不能断言当时是空格、概括还是旧稿引文。旧测试明确要求生成后再主动校验，和用户本次要求不同，本次按用户要求更新这个产品契约。

- 原 Comic Domain 复用故事、审核、确认、Run 与版本存储。审核输入提供绑定镜头、字段及当前内容指纹的 `evidence_id`，模型只选择编号，服务端提取该字段原文；旧逐字引文响应仍严格兼容。未知编号、错误镜头/字段、修复前编号及虚构引文继续拒绝，沿用原有一次结构纠正、最多两次内容修正及传输未知不重发边界，未通过的候选不冒充合格初稿。
- 原故事视图增加派生 `can_confirm`，确认接口使用同一规则：当前内容经过主动校验，或来自同一生成 Run 的当前契约内部复核，且没有错误、来源有效，才可确认。AI 整理成功后可以直接确认，也可主动校验；手写故事直接保存、校验，按镜头/字段显示问题与建议，不经过生成。恢复候选仍须主动校验；任何内容修改都使旧结论失效。`validation_requested` 继续如实表示用户是否主动点击，没有伪造人工校验。
- 原 `ComicFlowStage` 将运镜和转场改为多行输入，文字自动换行并按内容增高，仍可手动调整高度；动作、概述和要求同样随内容增长。恢复候选与内部检查通过的稿件使用不同提示，故事及导出页面都读取服务端确认能力。没有新增业务文件、依赖、表、迁移、Provider、Agent 或 Runtime；未改写生产故事、图片、历史及账单，保留已有未提交改动，未提交或推送。后端静态页面由正常构建更新。

验证：相关故事、图片删除及版本删除组合 **104 项通过**；新增用例先在旧实现复现失败，再覆盖长字段编号、修正后旧编号的一次纠正、五镜头整理→直接确认→ZIP 导出、同请求重放、pass/warning 两种内部结论及编辑后重新锁定。前端全组 **249 项通过**；末次提示区分后相关 **4 项再次通过**，不重复累计。类型检查/正常构建、目标 ruff、结构及差异检查通过，仅有既存 bundle 大小提示。

隔离真实 HTTP/SQLite 页面验证长运镜/转场全部换行可见、手动清空动作后直接校验显示镜头位置及补写建议、AI 整理完成后跳过主动校验直接确认并解锁导出。模型均为离线替身，未调用付费模型，不能据此保证任意故事的真实模型语义质量。标准 `python -m kantoku serve --no-browser` 的首页与最终脚本 HTTP 200、正常退出 0、无 WAL/SHM；`npm run dev` HTTP 200，停止后端口关闭，自有启动会话清理。正在运行的用户后端须正常重启，页面刷新后使用新逻辑；原失败请求及候选历史不自动重放。

### 整理仍失败：跨镜头修正范围、部分进展与候选诊断（2026-10-25）

`ERR-795BBD92 / trace-9951720eb910` 发生在新代码已加载的 `story.generate`，不再是上轮引文错配。认证只读 API 确认原故事仍为 v208，六镜头候选的末次公开审核含 8 条发现：其中包括第 4 镜持剑与第 5 镜来源握刀的衔接，以及把特写跟拍判成冲突等结论。日志显示结构修正与语义修正均已调用模型；旧 Run 仅保留末次候选，不能还原每一轮的具体改动，也不能把这些旧结论全部认定为真实错误。代码和新增用例另确认：原跨镜头错误只授权主镜头，相关镜头修改会被丢弃；部分结构补全只要还缺另一字段，就整体退回原稿。

- 归属为原 Comic Domain、Shell 任务结果投影及故事 UI。复用原 Run、故事/审核/确认修订、媒体请求认领和模型出口。`comic-story-4` 将每镜头按固定图片身份绑定到请求内的来源序号；明确区分当前稿与待整理的旧执行方式、单帧要求与持续限制、相机移动与景别、真实错误与节奏建议。当前字段编号可唯一补回遗漏的镜头身份，显式错误身份仍拒绝。
- 审核的可选 `related_fields` 必须引用当前值绑定的合法字段；只有错误级的衔接/一致性问题允许协同修正所指镜头。未知/过期引用继续走原一次纠正并严格拒绝，warning、图片、身份和用户要求正文不因关联引用获得额外修改权。结构与语义仍共享整轮最多两次内容修正，已完成的部分补全保留；无实际变化立即停止，不增加供应商重试。原 Run 最多保存 3 份公开稿件及真实检查结果，供排查，不保存私有推理或非法响应。
- 有合法审核但仍需修改时，返回 `generation_status=needs_revision` 及候选/具体建议，原故事不替换，原 Run 为 WAITING。媒体请求的已完成结果只表示该次操作已有可重放业务结果，不表示候选已通过或可导出；同请求重放不调用模型。传输异常、非法结构、错误证据和 CAS 冲突仍保留原错误语义。只有真实通过的初稿才直接保存并允许确认；旧契约候选恢复后必须按新口径校验。
- 页面在顶部提供候选问题链接，单独显示候选镜头/字段、问题及建议，候选全文可展开；载入编辑明确追加新故事版本，修改后独立校验。旧失败请求的已保存诊断也可读取，原故事不被预览替换；waiting 结果不会被误当成已采用候选而隐藏，采用后不重复出现。不伪造通过、不自动重提模型，也没有为了显示旧错误改写历史。

验证：新增跨镜头协同、部分结构补全和待处理结果 3 项先在旧实现失败，修正后通过。最终故事整文件 **98 项通过**，相关删除/版本删除 **12 项通过**，合计 **110**；前端全组 **251 项通过**，类型检查及正常构建、目标 ruff、结构和差异检查通过。旧断言中的“内容未修完必须抛系统异常”按本次业务 WAITING 契约更新，传输/错误证据及版本保护用例继续执行。真实运行服务只读核对 v208 和原 8 条候选诊断已在新页面可见；没有点击采用、编辑或提交生产模型。

使用明确模型替身的隔离 HTTP/SQLite 页面验证候选可读、载入编辑、修正运镜后独立校验及确认解锁；不代替真实模型质量验收。标准后端入口的首页和最终静态脚本 HTTP 200、退出 0、无 WAL/SHM；前端开发入口 HTTP 200、停止后端口关闭，自有验收会话用后清理。无新增业务文件、依赖、表、迁移、Provider、Agent 或 Runtime；未修改生产故事、图片、历史或账单，未提交/推送或自动重启用户后端。实际整理需重启后端加载本次修正，旧失败记录只读恢复，不自动重放。

### AI 初稿的真实交付门槛与连续纠错（2026-10-25）

只读核对 v209 和生成 Run `run-4fed539eaa5d414fba3b3c781d7ee10b` 的三轮公开记录：第 4 镜图片来源持剑、第 5 镜图片来源握刀，模型来回统一兵器却未补换兵器事件；一条审核把现稿不存在的“握着刀”声称为当前转场。v209 的来源实际是 `candidate_recovery`：上次允许载入未通过候选，导致带错稿进入当前故事。仅提高修正次数或隐藏错误都不足以解决。

- 修改仍在原 Comic Domain、生成 DTO 与故事 UI，复用原 Run、媒体请求认领、故事/审核修订和两个模型出口。`comic-story-5` 不复用旧口径检查；旧记录不改写。只对问题描述中缺失于当前相关字段的直接引文进行一次反证核对，必须引用同一合法当前字段、原问题中的缺失引文和真实当前原文，且模型判定整条发现只建立在旧引文上，才能撤销；来源要求、语义概括、同时包含真实问题或不确定的发现保留。反证结构或引用无效时保留原问题，传输未知直接停止；公开核对收据有界保存在原 Run。
- 错误级 `image_conflict` 也可按合法关联引用协调其他镜头的执行字段，图片、镜头身份与顺序仍固定。修正接收最多三份历史公开稿及检查，要求保留每张图片的起始事实，用实际收剑/换刀等事件衔接，并协调动作和时长。整轮最多三次内容修正，最后一次用统筹策略；无变化可改用一次统筹策略，已审核的旧稿或无变化稿不重复抽审核结论。原 Run 最多保留四份真实稿件/检查；每份变更稿仍独立核验，通过后才返回 `generation_status=ready` 并保存。
- `adopt-candidate` 拒绝未审核、旧契约或仍有错误的候选，不能直接载入并伪装交付。页面改为“继续 AI 整理”，复用 generate 的 `candidate_run_id`；服务端校验原 Run 归属、WAITING 状态、原故事版本、要求、固定图片与来源指纹，从服务端原候选继续，保留最初用户意图。旧请求重放不调用模型，未知/失败提交不能借继续按钮自动重发。当前故事保留到真实通过，手动编辑后的故事仍可直接独立校验；内部通过后可直接确认或主动校验。
- 页面区分当前故事错误数与优化建议数，完成提示只跟随真实 ready 结果，旧候选不能因刷新重新出现。原有长运镜/转场的多行换行与自动增高保留。无新增文件、依赖、表、迁移、Provider、Agent 或 Runtime，无生产故事/图片/历史改写，未提交或推送。

验证：新增复现先失败；最终故事及图片/版本删除组合 **119 项通过**，其后补充的修正传输未知不吞错用例 **1 项通过**；前端全组 **251 项通过**。正常类型检查/构建、目标 ruff、结构和差异检查通过，静态产物由正常构建更新，仅有原 bundle 大小提示。隔离真实 HTTP/SQLite 页面验证待处理候选→继续修正→独立复核→写入新版本→跳过主动校验直接确认，刷新后无旧候选；190 字运镜框 `scrollWidth=clientWidth`、`scrollHeight=clientHeight`，没有隐藏横向文字。标准后端首页/脚本 HTTP 200、正常退出 0、无 WAL/SHM；前端开发入口 HTTP 200、停止后端口关闭；验收进程和自有会话已清理。

补充核对：同契约的待处理候选继续整理时，复用其真实错误，先修正再复核，不能重新按按钮让同稿抽到通过；旧契约候选才先按新口径检查。对应继续整理、旧契约、无变化和末次统筹用例再次通过，不重复累计测试数量。

模型均使用明确离线替身，这些证据不等于真实模型语义验收。原工程付费预算累计账本缺失，遵循运行规范不新建预算身份绕过。用户当前服务由 PyCharm 启动，本次未强杀该进程；正常重启后端并刷新才会加载新契约。既有候选和原故事保留，不自动重放付费请求。

## 2026-10-25 故事镜头交接与视频工作台

用户选择小云雀，随后明确“尚未开通或不确定，先完成模块”。本轮完成页面、持久化任务与协议适配，保留本机 `video.enabled=false/provider=mock`，没有开通服务、公开图片或调用付费视频接口。实际可用权限、生成内容质量与真实账单尚未验收。

- UI 复用 `VideoStage.vue` 和现有样式：左侧镜头、右侧播放器、下方提示词输入及生成记录，删除原大段界面壳/演示说明和假进度。支持单镜头、整段故事、填入原导出文字、查询原任务、播放和下载。动画只对应真实提交/等待/取回状态；输入超过 2000 字明确阻止，不截断。未保存故事的按钮和快捷键共用门禁。窄屏镜头列表可横向滚动，正文和播放器不横向溢出。
- 新 `domains/comic/videos.py` 只定义 Comic 交接、确认门禁和工作流节点，复用原 Story、媒体请求认领、GraphRuntime、TaskRunner、Artifact、Budget。视频输入冻结确认时的故事版本、内容 hash、有序镜头、原图版本及字节指纹；参考图按镜头首次出现排序并去重，不按图片存储 ID 排序。视频创建与查询不推进故事/作品内容版本，后续故事编辑不改写旧视频。复用 Work/Conversation 原路由归属与认证，Core Store 仅增加可选通用 workflow 过滤且先过滤再 LIMIT，默认查询不变；无新表或迁移。
- 原 `capabilities/video/service.py` 补齐原子提交认领、完整输入指纹与旧 Mock 只读兼容。缺少任务号的未知提交不释放费用、不重提；有任务号只查询原任务。未知实际费用保留 `None` 和原预占，不结算成零。保存失败可沿原 Run/供应商任务恢复；原图后来不可读不阻止已经付费任务取回。查询按同 Run 去重，终态不重新运行；进程重启后无可核验任务号时显示中断，不能自动提交。
- 新 `capabilities/video/xiaoyunque.py` 沿用原视频 Provider 接口和已安装火山 SDK，只做官方提交、查询、下载与错误转换。只允许指定火山域名的 HTTPS 参考图和视频，拒绝重定向，限制下载容量，付费前核对公网原图 hash。视频按请求指纹保存，用 RuntimeManager 原子禁止覆盖发布，重复取回内容不同则保留原文件并报错。网页 CSP 仅补充同源/blob 视频播放，认证与媒体内容 hash 检查继续使用原网关。
- 新 `frontend/src/domains/comic/videoWorkbench.ts` 分离页面的请求、草稿与服务端投影，避免继续堆积组件。浏览器只缓存按作品/故事/镜头隔离的输入和未决请求编号，结果从原数据库恢复；旧响应及旧媒体不能进入另一个作品。没有新 Runtime、Agent、账本、Prompt 编译器或依赖。旧 Comic 可选 Mock 视频保留；启用小云雀不会顺带让首页/旧图片生产流程发起真实视频。

协议依据：[小云雀 2.0 有参考 API](https://docs.volcengine.com/docs/JimengAI/Xiaoyunque-IntelligentLifeVideoAgent20Reference-InterfaceDocumentation?lang=zh)。未来配置在原 `video` 配置项：先开通服务，将 `provider` 设为 `xiaoyunque`、`model` 设为 `pippit_iv2v_v20_cvtob_with_vinput`，默认从本机 `VOLC_ACCESS_KEY/VOLC_SECRET_KEY` 读取凭证；也可将 `authentication` 设为 `api_key`，从 `VOLC_VISUAL_API_KEY` 读取智能视觉 API Key。通过 `reference_urls` 配置“原图 SHA-256 → 用户自有火山存储 HTTPS 地址”，确认正式费用预估/单次上限后设置 `pricing_confirmed` 并启用。API Key 认证不等于自动配置图片存储或开通服务；没有配置 TOS 时模块不会自动上传本地图片。官方时长是目标档位，页面的镜头秒数也只标“目标”；服务未配置时运行按钮禁用，不能回退 Mock 交付。供应商结果过期或费用未知时保留核对状态，不能据此重新生成。责任归属待人工指定，真实启用仍需配置及单独验收。

后续接入用户已开通的 TOS 时，可配置 `tos_bucket` 与 `tos_region` 自动上传已确认的固定参考图；使用相同 Access Key / Secret Access Key，必须具有存储桶读写及签名权限。图片逐张写入随机对象名，禁止覆盖、指定私有 ACL，并用 12 小时只读预签名链接核对上传内容后再提交视频；不会改变桶级公开权限或自动删除对象。页面输入的密钥仅留在后端进程内，重启需重新输入。TOS 上传失败明确发生在视频提交前时释放视频预占，提交结果不明仍保留对账。TOS 存储与请求可能产生费用；服务价格未核实前继续保持 `pricing_confirmed: false`，不可按示例的 1 分启动付费调用。依赖火山引擎官方 `tos` SDK；没有旧存储上传能力可复用，移除依赖会导致 TOS 自动上传不可用，既有 URL 模式不受影响。

验证：故事、Web、Runtime、Domain Workflow、旧视频 Skill 与新视频的六文件组合 **212 项通过**；最终顺序及并发保存修正后视频专项 **16 项通过**（重叠组，不累加）。新增顺序、未保存门禁及并发覆盖用例先复现失败后修正。首次组合中的视频测试曾过早读取旧 WAITING，已等待原 Worker 完成再断言；旧历史筛选测试有一次时间顺序断言失败，单独及后续整组均通过，未扩大范围改其实现。前端最终全组 **260 项通过**，类型检查/正式构建、目标 ruff、结构检查和差异检查通过，仅有原 bundle 大小提示。

隔离真实 HTTP/SQLite 浏览器检查镜头选择、输入、动态等待、单条结果、原生视频播放和刷新恢复；播放 `readyState=4`、无媒体错误，页面 `scrollWidth=clientWidth`。生成协议使用明确离线替身；播放样本使用 MDN CC0 视频且标记离线夹具，不是小云雀生成结果。仅导出一张界面验收截图到 Codex 可视化目录，测试图片/视频/数据库用完清理。标准 `python -m kantoku serve --no-browser` 首页与最终脚本 HTTP 200、正常退出 0、无 WAL/SHM；`npm run dev` HTTP 200、停止后端口关闭。首次混合启动验收的 npm 批处理退出等待超时，已单独正常中断自有后端、清理自有会话，再独立验证标准后端正常退出；没有强杀用户服务或删除 SQLite 侧文件。浏览器验收服务正常退出 0、自有会话已清理。未提交或推送，用户需正常重启后端并刷新页面加载新构建。

### 同日修正：默认生成完整作品，而非逐镜头动作

用户明确最终目标是“故事编排全部分镜 + 复制的提示词 → 一条完整作品”。原实现默认单镜头，且整段模式仅冻结分镜数据，没有把动作、运镜、转场和故事摘要实际交给视频提供方，属于交接缺失；只改按钮名称不能解决。

- 继续修改原 `videos.py`、`videoWorkbench.ts`、`VideoStage.vue` 与原 API 类型，无新增文件、依赖、表或第二套编译/运行机制。UI 和新请求默认完整故事；左侧点击只查看镜头，保持完整范围、草稿、加载和原作品结果。单镜头入口收进“单镜头试片（可选）”，显式切换才产生试片请求；既有单镜头 API 和历史结果保留。
- Domain 复用原 Story `_text` 导出正文，加入完整连贯作品要求和按镜头首次使用的参考图编号/导出文件名对应关系，再结合用户成片要求。所有分镜的顺序、动作、运镜、目标时长与衔接真正进入 `VideoGenerationRequest.prompt`，目标秒数为全故事合计；一次请求只创建原共享流程的一条视频任务，不在浏览器逐镜生成或伪装合成为作品。原导出正文若是粘贴输入的完整前缀，只剔除这份精确重复文本，保留其后的额外要求；编辑过或无法精确对应的文字完整保留，不作模糊删减或隐式 AI 改写。
- 实际生成指令在提交前冻结为原 Run `snapshot.generation_prompt`，后续编辑、刷新与恢复不重新编排旧付费输入；没有此字段的历史任务仍使用原 `request.prompt`。GET 只增加服务端 `story_context` 和任务 `submitted_prompt` 只读投影，页面可展开查看实际制作指令。界面按故事加用户要求的总长度计数，与提交前门禁保持一致；超过官方 2000 字限制时，在认领 Run/费用前明确拒绝，不截断镜头或把短片标成完整交付。小云雀仍未开通/启用，真实内容完整性、视听质量和供应商指令遵循仍需开通后验收。

验证：新增完整交接、默认全故事与总长度用例先在旧实现失败；故事/视频/旧视频 Skill 组合 **132 项通过**，最终补充“粘贴全文后追加要求”的精确去重后，视频整文件 **20 项通过**（重叠组，不累加）。前端最终 **262 项通过**，包含真实组件、完整范围不被查看镜头改变、整片草稿恢复、最终指令计数、超限不截断及旧试片隔离；类型检查/正式构建、目标 ruff、结构及差异检查通过，仅有原 bundle 大小提示。隔离 HTTP/SQLite 页面验证两个分镜只提交一次完整作品请求（实际指令含两镜、382 字），生成记录只有一条完整作品，刷新后仍为完整模式且可读取原文件，页面无横向溢出。协议与播放仍为明确离线夹具，不是完整故事的真实生成验收；本次只导出界面截图。标准后端首页/最终脚本 HTTP 200、正常退出 0、无 WAL/SHM，自有会话用完清理；生产作品、图片、历史与账单未改写，未提交或推送。正常重启用户后端并刷新才加载当前实现。

### 故事整理交付、容量与完整视频提示词（2026-10-25）

现场错误 `ERR-B2A915CF / trace-8360f65a64dc` 属于 `story.generate` 的第三轮内容修正：原 Run `run-eafe5380a6064e3c9feccba2c3a95cd4` 已保留三次公开稿/审核，随后被视觉入口的 18000 上下文门禁拒绝，未发送这次修正。当前作品仍为 v210、已人工确认，存在三条 warning；这些业务记录只读核对，未由工程脚本改写。旧实现把 warning 当作 AI 整理成功，且修正范围仅允许 error，所以用户得到建议而没有得到完整修改。

- 原 Comic Domain 升级到 `comic-story-6`。AI 整理在原三轮内容修正额度内同时处理错误与可执行优化建议，协调同镜的动作/运镜/时长/转场及已核验关联镜头；图片身份、顺序和用户原始要求保持保护。只有独立审核为 pass 且 findings 为空才保存新故事；无变化或额度用尽时保留原稿和真实未决建议，不能把 warning 候选直接采用为完成。单独主动校验仍只给诊断，不自动编辑人工故事；手写故事的 warning 接受路径保留。旧契约候选需重新核验，已确认历史及原审核不覆盖。
- 修正请求保留当前稿、原用户意图、固定来源全文和全部当前问题，仅去掉问题内重复的 evidence 原文，将三份旧稿/旧引文改为带稿件 hash 的历史问题索引。完整历史仍在原 Run 中，原有回退旧稿与重复抽审核保护继续生效；没有提高容量、删除硬要求或自动重发未知模型调用。用现场公开记录只读重建的文本估算由 18525 降至 14790；另以真实视觉入口配离线模型验证原容量错误先失败、修改后通过，图片准备元数据和原门禁不绕过。
- 原 Story `_text` 现在直接输出完整视频生成提示词：全片目标/总时长、叙事主线、用户要求、跨镜人物/道具/方向/声音连续性、参考图对应和连续时间轴，保留实际动作、运镜与结束衔接。原导出页、复制按钮和 ZIP 的 story.md 使用同一正文；视频工作台直接复用，移除视频侧重复前缀。旧已保存导出和付费任务快照不改写，不追加新的 AI 编译器、模型调用、Schema、表或依赖。提示词是对已确定故事的忠实渲染，不能把有矛盾的故事仅靠套全片说明称作质量合格。
- 原 UI 明确“复制完整视频提示词”，未决提示按全部待修正/优化项计数，避免只有 warning 时显示零处问题。正常整理完成会把修正写进当前故事，仍有真实问题时明确未完成。没有隐藏建议、伪造通过或增加用户费用确认步骤。

验证：四个新增复现先失败后修正，覆盖 warning 自动修改并保存、未修好的建议不交付、三轮修正容量和可直接使用的完整时间轴提示词。首次故事/视频/旧 Skill 组合为 131 通过、5 个旧契约版本断言失败；更新到契约 6 并新增旧契约 5 的重新核验后，最终组合 **137 项通过**。前端最终 **263 项通过**；类型检查/正式构建、目标 ruff、结构与差异检查通过，仅有原 bundle 大小提示。无新增文件、依赖、表或迁移，无生产作品/历史/图片/账单修改，未提交或推送。

隔离真实 HTTP/SQLite 页面验收：AI 整理将镜头 1 的运镜、5 秒时长和转场修正后直接保存 v2；再次主动校验无原建议，确认后导出含 0–8 秒、8–13 秒时间轴和跨镜连续性，视频页同源读到 13 秒完整作品，页面无横向溢出。模型均为明确离线替身，没有真实语义或视频质量的付费复测。页面复制按钮返回成功反馈，但浏览器验收工具的虚拟剪贴板未读取 Web Clipboard 数据，未将此反馈计为操作系统剪贴板逐字对照证明；组件复制的数据来源与错误分支仍由前端回归覆盖。仅导出界面截图，验收标签/服务正常退出 0、自有会话已移除。标准后端首页和最终脚本 HTTP 200、退出 0、无 WAL/SHM，自有启动会话已清理；当前用户服务未自动重启，需正常重启并重新点击 AI 整理加载新契约。

### 故事整理中断：模型账户余额不足（2026-10-25）

现场 `ERR-47A0FA4F / trace-88ad5527a264` 的供应商原始诊断为 HTTP 402、`Insufficient Balance`，错误类型 `insufficient_balance_error`，但通用 code 为 `invalid_request_error`，原页面只追加 code 因而未说明根因。Run `run-8487fe7d779f4d02a706c57a8a0d4a2e` 在生成、检查和一次修正后，于后续引文审核调用被拒绝；此前已有五次完成的模型响应。认证 API 只读确认 Run 为 failed、契约为 `comic-story-6`、候选仍保留，当前故事仍为其预期的 v210，没有把审核未完成的候选写入项目。不能将这次外部计费失败解释为故事已经通过质量验收。

- 最小修改只在原共享 LLM 出口的安全错误文案与 Config 统一错误分类，保持 EX-02 的兼容边界，没有新网关、供应商特判或业务分支。HTTP 402 显示“模型服务账户余额不足或计费不可用，请充值或切换到有可用额度的模型服务后再试”，归类为 `needs_user_action`，保留原 HTTP 状态、错误编号、供应商 code 和异常链。相同 code 的 HTTP 400 不误报余额不足，认证和网络诊断继续保持原语义。
- 既有不重试、不切备用、连接关闭、失败请求不自动重放和候选保留机制继续使用；没有放宽质量门禁或将失败候选直接采用。旧 Run 错误只读保留，不追改历史提示、故事、图片、账单或配置，不重启用户进程。账户需要恢复可用额度后，由用户显式重新整理；更新后的错误提示需正常重启后端加载。
- 新增六项真实 SDK 配离线 HTTP 传输的回归，HTTP 402 在普通、视觉和流式三条入口先复现缺失提示，再修正通过，同时覆盖同 code 的 HTTP 400、单次调用、无自动重试/切备用、连接释放及安全文案。既有故事审核失败用例补充初次审核和修正后审核的余额中断，验证候选保留、当前故事不替换、原请求不再调用模型、未通过候选不能采用和人工稿仍可独立校验确认。

验证：`tests/test_llm.py tests/test_logging.py` **50 项通过**，故事审核失败专项 **8 项通过**，合计 **58**；目标 ruff、结构及差异检查通过。前端正常类型检查/构建通过且静态文件 hash 未变化，仅有原 bundle 大小提示。标准后端入口在隔离 Runtime 启动，首页与最终脚本 HTTP 200、正常退出 0、无 WAL/SHM，自有会话已清理；第一次启动验收因测试会话前缀不合规范在创建前拒绝，改用规定前缀后通过。无新增文件、删除文件、依赖、Schema 或迁移，没有更改默认生产路径、费用或审批，没有提交/推送。未发起真实收费调用，原工程预算累计账本缺失，不另建身份绕过；账户余额恢复和真实整理质量仍待验收。

### 跨图衔接的错误建议、已完成动作误判与前镜修正权限（2026-10-25）

用户新截图对应 `run-984acfd0e1674eaa9df2aa40b01b1bde`，后续 `run-b16d9094251148f089f568446750f91d` 也停在 WAITING，各保留四份真实公开稿/审核；本次没有余额错误。只读核对显示第 4/6 镜来源持剑、第 5 镜来源握刀，但审核曾建议直接把第 4/6 镜改成刀，修正随后违反第 6 镜固定来源；另一次第 4 镜转场已经写明收剑、拾刀，审核仍要求再补相同衔接。还有转场已写换持却要求 action 再重复、时长从 6→7→8 秒逐轮增加以及最后一轮才发现第 3 镜运镜问题的情况。顶部三条 warning 属于未被替换的当前 v210，侧栏才是本次未交付候选，原页面缺少清楚的归属说明。

- 修改限原 Comic Domain、公开反证模型及故事 UI，复用同一个 Story/Run、共享文本/视觉出口和统一日志。契约升为 `comic-story-7`；按“起始帧→动作→转场→下一起始帧”检查，同镜时长覆盖动作和转场，转场内已经发生的事件不必在 action 重复。来源映射显式给出前后镜头身份。审核应一次检查所有镜头及邻接边界，时长建议给出完整动作节拍的可行安排，修正不能照抄会破坏固定来源的建议，不能用“已换持”省略实际取放过程。
- 原一次引文核对同时处理有真实来源依据的跨图 continuity/transition 问题，错误和 warning 均可进入。扩充内部公开反证 Schema：`already_satisfied` 及 `supporting_quotes` 必须引用当前主字段、至少两个不同执行字段（含转场）和每张相关图的有效来源原文；引用受当前字段指纹及当前相关镜头范围约束，缺失、伪造、无关字段、无来源及 model_choice 反证均不能撤销。有效替代要求只引用 effective_requirement，不能恢复旧目标；负向生成词不成为反证事实。真正的 image/requirement 冲突不能借新衔接分支直接撤销。
- 冲突真实但建议错误时保留原 error，只在同样的当前/来源依据齐全时接受 `corrected_suggestion`，再交回原内容修正和独立复核；不是把候选直接改成通过。多条核对原子接受，无效结构/反证保留全部原问题，传输未知直接停止。检查和更正收据沿原 Run 有界保存；每次检查最多共用一次核对，三次内容修正上限不变。触发更多语义核对可能产生额外文本费用，不称为免费，也不增加新的费用审批、重试或预算机制。
- action 处的入镜 continuity 错误可协同修改前一镜的四个执行字段，补齐真正应发生在切镜前的动作；图片、身份、顺序、用户原话及无关镜头仍不能改。仅真实通过且没有未决发现才保存新故事。旧候选继续时必须按新契约核验，旧 Run、当前故事及人工确认记录不追改。UI 明确“当前已保存故事的校验结果”，说明未应用候选与旧建议的归属，候选具体问题继续完整显示。

验证：新增八项先在旧实现复现失败；故事/视频/旧视频 Skill 组合 **149 项通过**。随后补充来源原文伪造及 model_choice 反证的拒绝，最终反证/衔接/完整交付专项 **17 项通过**（含两个新增项，其余与前组重叠）。完整交付用例通过真实隔离存储验证：错误建议被保留问题并纠正→前镜补齐收剑取刀和时长→独立检查→自动追加新版本→直接确认→主动校验复用通过结果；所有模型都是明确离线替身。初次组合命令误写旧视频 Skill 测试文件名，未开始执行；改用 `tests/test_skills_video.py` 后整组通过。

前端最终 **264 项通过**，类型检查/正常构建、目标 ruff、结构及差异检查通过，仅有原 bundle 大小提示。用现场六镜头候选只读重建新增文本核对输入，估计 **13426/18000 token**，原两条问题保持，未调用模型；这只验证输入容量，不代表语义判定通过。隔离真实 HTTP/SQLite 浏览器验证旧故事建议及未应用候选明确分开，原编辑内容仍是旧版本；仅导出一张提示归属截图，临时页面/服务正常退出 0，自有会话已删除。标准后端入口首页及最终脚本 HTTP 200、正常退出 0、无 WAL/SHM。截图初次保存因未组装文件目录被拒绝，补齐原 DatabaseManager 组装后导出成功；停止信号首次登记为跨会话 temp 被拒绝，改用明确的自有导出登记后正常停服，未绕过清理守卫。

没有新增业务文件、删除文件、依赖、数据表、迁移、Provider、Agent 或 Runtime，没有改写生产故事、图片、采用指针、失败历史或账单，未提交/推送或自动重启用户后端。反证的逐字绑定证明它引用了正确版本，语义是否成立仍由模型核对，离线结果不能代替真实模型质量验收。原工程预算累计账本缺失，本次未付费复测、不新建预算身份；用户正常重启后端并刷新后，可在原候选点击“继续 AI 整理”。

### 故事初稿的来源顺序、切镜边界与时长预算（2026-10-26）

现场两张截图对应 `run-5456dc2a7fac447a9ec4a7d27fd53bdd`，契约 7、WAITING，原故事仍为 v210。认证只读核对四份稿件和真实日志：换持建议反复，时长由 6→8→10 秒增加，末次还建议删除切镜前换持并把下一起始图的剑改成刀；其中三次反证因缺少依据或缺项被整体拒绝。来源数组实际按图片内部 ID 排序，不是镜头顺序。这些证据证明本轮存在输入组织和核验契约缺口，不表示截图中每条模型建议都正确，也不表示原稿已经交付。

- **归属与最小修改**：仅原 Comic Domain 的 `stories.py`、`models.py`、原故事测试及本手册，复用同一生成、修正、独立审核、媒体请求、Run 和追加修订。契约升为 `comic-story-8`。不增加修正次数或供应商重试；旧候选继续整理须按新契约核验，同契约已知问题仍先修后审。
- **明确来源和边界**：模型输入的图片目录、来源正文和真实视觉附件统一按镜头首次使用排列，来源直接标明 `applies_to_shots`，重复入镜保留各镜头身份。服务端构造当前字段指纹绑定的 `shot_boundaries`，明确前镜动作/转场/时长、切镜字段和后镜起始状态。必需换持或移位应在进入新起始图之前完成。只调整模型输入，不改持久化图片顺序、来源快照或历史指纹。
- **逐条严格核验**：给每条待核对发现分别列出主字段、允许的现稿字段、缺失引文及每张相关图可引用的来源。结构合法后逐条核验；缺项、重复或假引文只使对应问题保留，不能让其他已经严格核验的结果一起作废。错误和 warning 的过期直接引文使用同一规则，必须有同字段反证才可撤销。真实冲突、无依据、model_choice、错误来源及传输未知继续阻断；已确认改图的结构化授权原文也能作为对应图片的依据。收据保留合法核对及失败条目编号，不保存非法模型正文。
- **时长判断有执行依据**：公开 finding 增加可选 `timing_plan`，默认空列表兼容旧数据。新的语义 `duration_conflict` 必须引用本镜当前 action 和 transition，给出完整节拍时间；服务端核验引用、覆盖、非负有限数值及错误级预算确实超过当前秒数，剪辑切镜可为零秒。缺依据只能走原一次审核结构纠正，持续不合法停止交付，不能把“动作多”直接当成可靠的加秒指令。修正模型收到有效节拍预算后统筹修改，再经独立检查。数值与引文通过只证明契约成立，真实动作耗时仍属模型估计。
- **交付边界保持**：只有实际独立审核 `pass` 且无未决发现的改稿才追加为当前故事；修正用尽、无变化或审核失败保留候选。没有按 AI 来源自动通过，没有直接采用带错候选，也不把旧稿或旧结论重标为新审核。人工稿仍可独立校验，内容变化使旧结论失效。

验证：来源错序、同图重复入镜、两种不同道具的切镜前修正和逐条反证等新增复现先在旧实现失败；首批修复专项 10 项通过。时长缺预算、假引文、漏转场及数值不构成冲突四项也先失败，再由生产修复通过；持续无依据的审核会保留候选、不替换原故事、不允许采用，原请求不重发。故事/视频/旧视频 Skill/图片删除组合 **170 项通过**；随后增加的失败交付及零秒切镜边界单独复跑，历史版本删除 **6 项通过**。各专项有重叠，不重复累计。最初组合命令误写版本删除测试文件名，在收集前退出；改用实际文件后执行。新增失败交付用例初次比较忽略了请求 instruction 的正常原文绑定，修正夹具预期后通过，没有据此更改生产逻辑。

前端全组 **264 项通过**，类型检查及正常构建通过，静态脚本和样式 hash 与本轮开始相同，仅有原 bundle 提示。目标 ruff、结构检查及差异检查通过。标准后端 main 的隔离 HTTP 启动：首页、脚本、作品接口均 200，退出 0，无 WAL/SHM，自有会话已清理。现场六镜头材料的只读文本估算为审核 14460、反证 16178、修正正文 15396 / 18000；修正估算未包含实际视觉传输元数据，不能称为完整视觉请求实测。仅测试输入容量，原四条发现仍保留，没有真实模型调用或语义通过结论。

无新增/删除业务文件、依赖、表、迁移、Provider、Agent 或 Runtime，无生产故事、图片、采用记录、历史请求或账单改写，未提交/推送或重启用户后端。工程预算累计账本缺失，未新建身份绕过、未进行付费质量复测；正常重启后端并刷新后，原候选可显式继续 AI 整理。真实整理质量仍需实际模型和人工验收，不能承诺任意故事绝对零错误。

### 整理中途超容量与审核格式纠正缺少坐标（2026-10-26）

新截图的 `ERR-9744CB73 / trace-fe562fee7a41` 实际属于手动 `story.review`：第一份审核未给出有效时长预算，唯一一次格式纠正后仍未通过 Schema。原始非法响应未保存，不能断言第二次具体错在哪个字段。此前 `run-3e5c9fcf87654c7a9d2dd2aa4076b5aa` 的 `story.generate` 已加载契约 8，但第二轮检查之后，后续修正被真实视觉入口的上下文门禁拒绝，当前 v210 未被替换。上节只量文本、未完整测视觉传输信息和继承的三份历史，确有验收缺口。

- **最小范围与复用**：只维护原 `comic.stories`、原故事测试和本手册，继续复用现有模型出口、Runtime、媒体请求、故事和审核版本。契约升为 `comic-story-9`，旧审核不得冒充当前审核；结构纠正仍最多一次，内容修正仍最多三次。没有新文件、依赖、表、迁移、Provider、Agent 或第二套编排。
- **完整请求容量**：当前稿、用户原始框架、全部当前问题、有效节拍预算和来源全文保留；当前问题不重复携带已投影为修正范围/边界的字段指纹与关联列表。三份历史只提供稿件指纹、结果、问题位置、类别与严重程度，不再重复旧引文、旧预算和旧建议。完整历史仍在原 Run 中。模型输入用等价紧凑 JSON，不提高上限、不截断要求、不丢参考图，也不通过未知请求重发处理容量问题。
- **可定位的格式纠正**：审核解析提供字段位置和错误类型，并把未知键名归为 `unknown_field`；不将非法值、未知字段、原始响应或私有内容送入纠正。只剥离完整包裹单个 JSON 的标准代码围栏，不能从夹杂说明或未知正文中猜取答案。Schema 合法的单条发现仍须经过原引文、来源和时长核验，再在服务端保留；另一条坏结构或错误 status 不能使这些真实问题被第二次的空 pass 抹掉。时长纠正另给必需阶段、引文不匹配的节拍编号及当前时长，减少笼统重试。持续错误与传输未知仍停止交付，不伪造通过。
- **用户操作契约**：整理经过真实修正与独立复核后才替换当前故事；同稿随后主动校验复用这份实际结论。手动改稿后仍重新校验，真实问题必须保留。历史失败请求及未完成候选不追改、不自动付费重放；本次没有通过隐藏旧建议或自动接受候选来制造通过。

新增五项首先在旧实现失败：六图完整视觉容量、另一条坏结构时保留合法错误（三种）及精确节拍字段纠正。修复后这五项与既有纠正保护、整理→格式纠正→实质改稿→独立复核→主动校验无旧问题→确认及同请求复用共七项专项通过。所有模型为明确离线替身，完整流程的审核只有在动作/时长实际改好后才返回 pass。新增测试初版误把上一测试的确认断言留在新函数内，修正测试边界后通过，没有为此更改产品逻辑。

对现场六图、三份继承历史、当前四条发现及原用户框架只读重建，实际走原 `_comic_image_vision_model` 的图片准备和容量检查，原字节共 20042271，传输副本共 4585021。完整准备后的文本门禁估算从 **18344** 降为 **15241 / 18000**，六张图片都保留；上限继续原样执行。图片仅从认证只读 API 核对原 Artifact 后在内存读取，原四条发现没有被改成通过。这证明本次请求可发送，不代表模型已经修好其语义；没有实际模型或付费调用。

最终故事、视频、旧视频 Skill、图片删除与历史版本删除五文件 **185 项通过**，上述七项专项是其中子集。前端本轮 **264 项通过**，类型检查和正常构建通过，静态脚本/样式 hash 未变，仅有原 bundle 提示。目标 ruff、结构及差异检查通过；标准后端 main 在独立 Runtime 启动，首页、脚本、作品接口均 HTTP 200，正常退出 0、无 WAL/SHM，自有会话用后清理。没有改写生产故事、图片、采用指针、历史请求或账单，也没有提交/推送或重启用户服务。累计付费验收账本仍缺失，未绕过该限制；部署需正常重启后端并刷新页面，真实整理质量仍待实际模型验收。

### 一次整理完成优化：时间预算落实与自动修正（2026-10-26，待真实验收）

用户再次明确：一次点击应自动完成实质修改、优化、独立复核和当前故事保存，再校验没有未决错误或建议。隐藏建议、缓存旧通过、恢复带错候选以及只报告工程测试都不能代替这个效果。

只读核对 `run-36829ed82c5543f6ad71f3a347433b04`：契约 9、四份稿、三轮修正后 WAITING，当前仍为 v210。末次审核给第 4 镜完整预算 5.5 秒、第 5 镜 5 秒，但改稿均为 3.5 秒；原服务只将预算作为建议，没有落实到返回稿。随后 `run-3a0b6c90be0d42d8b0efe4962d73d1ed` 校验的是未被替换的 v210，含一条 error、两条 warning。另一次余额失败不构成有效审核。

- 仅维护原 Comic Domain `stories.py`、原故事测试和本手册，复用原生成/修正/审核、请求认领、Run 与追加版本。契约升为 `comic-story-10`，旧结论不直接复用。
- `timing_plan` 经过同一套当前字段、引文、完整阶段及数值校验后，投影为本次改稿的保守时间下界，只在授权镜头的时长字段应用，防止写稿模型再次缩短。图片、身份、顺序及用户原话不变，动作、衔接和硬要求仍须独立审核。假引文、旧编号及超过 60 秒容量的预算不投影。
- 单次自动内容修正由三次增至最多六次，第三次起统筹；每份实际变更稿均独立复核。无变化、循环旧稿、传输未知或余额失败仍停止，不重复抽审核。Run 最多保存七份真实稿/审核，模型只带三份简短历史索引。新增修正可能计费，不称为免费，不改变供应商重试或通用预算。
- 只有实际 pass 且无任何未决发现才一次追加当前故事。手工稿仍可独立校验，等待或失败不构成交付完成。

四项新增复现先在旧实现失败：error/warning 的有效预算被再次缩短，以及六镜须在同一次整理内完成全部修正后再保存。修复后通过；替身故意持续返回 3.5 秒，应用实际写入 5.5 秒，独立审核逐稿检查，六镜全修后才保存一次，再主动校验无原问题。另加旧引文、旧编号和越界预算三项拒绝。最终相关五文件 **193 项通过**，前端 **264 项通过**；类型检查、正常构建、目标 ruff、结构与差异检查通过。标准后端 main 隔离启动首页、脚本、作品接口均 HTTP 200，退出 0、无 WAL/SHM，自有会话已清理。

现场候选只读内存回放：第 4 镜 3.5→5.5 秒、第 5 镜 3.5→5 秒，其余内容全部不变，原三条发现保留；没有真实模型或生产写入，不能称为该故事已通过。无新增/删除业务文件、依赖、表、迁移、Provider、Agent 或 Runtime，无原作品、图片、历史或账单改写，未重启用户服务或提交推送。

本轮仍待真实模型验收，不能据离线结果宣称用户目标已经完成。项目及项目测试目录未找到原累计费用账本，不新建身份或清零旧费用。真实验收应在隔离副本用同一故事执行，先恢复原预算记录并获得明确新增额度授权；如用户明确同意，可把旧额度按已用尽保守处理，未知历史费用不得记零。实际内容修正和无未决建议的独立复核成立后，才能报告真实通过。

### 六图真实验收后的边界与时长修正（2026-10-27，仍待真实验收）

用户明确授权在同一预算身份最多新增 1 元，旧额度按 2 元已用尽保守恢复，不把未知旧费用记零。使用 v210 故事、六张原图及其固定来源的隔离副本进行两次真实验收，合计 22 次有用量回报的调用，按配置单价估算新增 **0.871566 元**，最终供应商账单为准。第一次在时长审核结构上中断；第二次实际经过多轮修改，仍出现第 5 镜起始画面持刀、第 6 镜起始画面持剑未妥善衔接，以及时长预算反复增加。余下 0.128434 元不足以覆盖下一调用的保守预留，因而停止，未取得整理完成和独立复核均无未决发现的证据。生产故事在验收前后均为原版本和原指纹，两个自有验收会话已清理；不能把这两次测试称为通过。

- 在原 Comic 故事服务内，契约升为 `comic-story-11`，旧审核不会自动继承为新结论。真实第 4/5/6 镜的来源事实分别保留，明确要求逐边界处理道具切换，不以统一道具规避固定起始帧。
- 后一镜的画面冲突明确关联前一镜图片时，允许同步修前镜转场和时长，让取放动作在切镜前发生；没有关联的前镜不开放修改，图片身份和顺序不变。公开反证可使用绑定当前字段与来源内容的编号，由服务提取原文，仍须具备相关主字段、转场及每张图的有效来源；旧编号、虚构原文和缺失依据均不能撤销真实问题。
- 时长节拍的引文若仅被标错为同镜另一阶段，且能够从该阶段唯一逐字匹配，则修正字段归属；相机文字、旧稿或不存在的引用依旧拒绝。旧稿的完整时间预算只在动作及转场都未变时作为改稿下界；若动作或转场改变，新稿必须独立复核，不能机械套用旧预算或伪造通过。
- 复用现有验收脚本以同一身份累计费用；快照保留原图字节和来源，失败时保留安全候选诊断。脚本按官方 DeepSeek Flash 图片 token 上界及配置单价保守预留，余额不足或结果未知不重发。

六文件后端回归 **212 项通过**，随后新增的改写时长未解决不得保存、仅关联前镜图片才可修改转场两项专项通过。前端 **264 项通过**；隔离输出的正常构建及类型检查通过，保留已有 bundle 大小提示。目标 ruff、结构和差异检查通过；标准后端入口在隔离 Runtime 启动，首页及作品接口均 HTTP 200，正常退出 0，无 WAL/SHM，验收会话已清理。构建验收最初两次因终端编码处理无法记录结果，改为安全解码输出后正常通过；没有改写生产前端构建目录。

没有新增业务文件、依赖、数据库表或第二套服务，也没有提交或推送。本次只执行离线回归，新增真实模型验收费用为零；上次真实费用和失败保留于同一审计记录。2026-10-27 检查时用户后端未运行，代码需要正常启动后加载；按原批准额度无法完成下一次完整付费复验，真实质量仍未验收。

### 追加一元后的真实故事复验与继续修正（2026-10-27，真实验收未通过）

用户明确在此前额度上再追加 1 元，同一工程预算身份的累计新增上限为 2 元；旧 2 元仍按已用尽保守计算，原历史和未知支出未归零。后端没有运行，验收通过 DatabaseManager 只读核对原 v210 和原 Run，再核对六张图片字节指纹，在自有隔离会话中复现完整整理。三次实际验收分别使用契约 11、12、13，调用 11、2、12 次，配置价格估算分别为 0.439324、0.084654、0.492450 元；均未通过。累计新增估算 **1.887994 元**，剩余 0.112006 元不足以保守预留下一次完整调用。最终费用以供应商账单为准。

- 第一次未通过的具体原因是时长优化 warning 没有节拍表，审核格式门禁阻断了后续改稿。现保留该 warning 并进入正常修复，未修好仍保留未完成状态；真正的时长 error 和已提供的节拍表仍须核对原文、阶段及数值。字段编号错误时，格式纠正附上当前字段的正确编号，不采纳错误编号。
- 第二次未通过是跨图反证请求超出原上下文容量。对原 v210、六张来源和五个待核对边界做只读重建，估算为 18146/18000；去掉在来源清单与反证表中重复的同一原文，改用绑定字段编号引用，估算降到 17079/18000，39 条相关来源仍可逐一还原，来源范围与原始要求仍保留。模型仍需给出合格的反证才可撤销发现。真实契约 13 的同类审核已成功提交，没有再次触发该容量错误。
- 第三次完成多轮真实修改后，仍出现现稿已为 10 秒但审核描述旧的 3 秒、标为 `other` 的跨镜问题未按衔接处理，以及对已明确取放的动作提出重复建议；余额不足以预留下一个修稿调用，未完成交付。现契约升为 `comic-story-14`：对审核声称的当前时长先核对实际字段，再交独立反证；`other` 中明确关联相邻图片的动作问题仍按关联范围修复并核验。非时长类型附带的未核实节拍表不能作为可靠预算传给改稿模型，原问题本身仍保留，不能自动通过。
- 这次真实验收的图片、草稿和数据库仅存在于隔离会话，结束后已清理；只读核对生产故事仍为 v210。费用记录保留原身份、原历史、追加授权与累计估算，没有改动生产故事、图片或历史。

契约 14 尚未做新的完整真实模型验收，不能称为用户的目标已达成；额度不足时没有绕过预留、自动重试或更换供应商。最终故事回归 **187 项通过**，随后新增的旧时长有真实问题时仍保留等三项专项通过；修改过程中的六文件后端组合 **235 项通过**。当前标准后端入口在隔离 Runtime 启动后首页及作品接口均 HTTP 200，正常退出且无 WAL/SHM；前端代码未变化，上次 264 项测试和隔离构建通过。目标 ruff、结构和差异检查通过。无新增依赖、业务表或第二套服务，未提交或推送。

### 前后端开发入口与电商功能移除（2026-10-27）

- 前端源码继续位于 `frontend/`，可在 VS Code 单独打开并运行 `npm run dev`；后端源码位于 `src/kantoku/`，PyCharm 打开项目根目录使用既有 `Kantoku Backend` 配置或 `python -m kantoku serve`。后端仍依赖根目录配置、技能及现有运行路径，因此没有为改名移动数据或复制一套后端。`shells/web/` 是由同一前端源码构建的静态页面。
- 移除 Commerce Domain、平台适配器、技能、专属前端产物组件和相关专用验收脚本；Shell 不再注册工作流，新建、批次、恢复及审批入口拒绝电商执行。界面隐藏电商任务、审批、资产和可通过原 Run/message 身份识别的对话记录，其他聊天消息保留。旧枚举、工作流标识及配置兼容字段用于识别历史数据，不恢复执行功能。
- 未删除或迁移数据库、账单、产物、图片和其他运行数据；未新增依赖、运行时或数据库迁移。此前未提交的代码和旧静态构建文件保留，静态入口更新为当前前端构建。历史孤立消息如果没有任何可核验的领域/Run 关联，不能可靠地按内容猜测并隐藏。

验证：前端全组 264 项通过，类型检查及构建通过；`npm run dev` 返回 HTTP 200。后端首页/快捷域两文件回归、Core Runtime、Core v0.2、会话上下文、Comic Workflow、Skill/Video 及电商停用专项均通过；标准后端入口在隔离 Runtime 返回 HTTP 200 并正常退出。目标 ruff、结构检查和差异空白检查通过。未运行全仓后端完整测试，未调用真实付费模型；浏览器人工验收及物理上独立的 `backend/` 项目目录尚未完成。
