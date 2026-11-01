# 前端页面字段 ↔ 后端接口映射

本文件记录像素风改版后，界面上每个可见区块的数据来源。原则只有一条：**界面上的每一处内容都能在这张表里找到后端字段**；表里没有的，界面就不显示，宁可留空也不填假数据。

## 0. 通用约定

- 前端类型集中在 `frontend/src/types.ts` 与 `frontend/src/services/core.ts`，接口封装在 `core.ts`，视图只消费封装后的类型。
- 所有业务请求经 `apiFetch` 附带 `X-Studio-Token`（取自服务端注入的 `<meta name="studio-token">`）；会话门禁另由 `kantoku_session` Cookie 判定。
- 三类"不确定"必须显式表达，不得退化成 0 或默认值：
  - 余额无法确定 → `TextBalance.unavailable = { code, message }`，`balances` 为空数组
  - 图片实际计费未知 → `CoreRun.image_execution.actual_fen = null` + `billing_status`
  - 凭据未配置 → `ProviderCredentialField.configured = false`

## 1. 认证与账户

| 界面位置 | 接口 | 响应字段 |
| --- | --- | --- |
| 登录页「已有存档 / 尚未建档」、存档位计数 | `GET /api/auth/status` | `registered`、`authenticated` |
| 登录页表单提交 | `POST /api/auth/login`（需 `X-Kantoku-Login: 1`） | 置 `Set-Cookie: kantoku_session` |
| 首次建档提交 | `POST /api/auth/register`（需 `X-Kantoku-Login: 1`） | 同上 |
| 个人中心用户名、昵称、头像 | `GET /api/account/profile` | `username`、`nickname`、`avatar` |
| 个人中心「本次登录」时间 | `GET /api/account/profile` | `session_started_at`（秒级时间戳） |
| 保存昵称/头像 | `POST /api/account/profile` | `nickname`、`avatar`、`remove_avatar` |
| 修改密码 | `POST /api/account/password` | `updated` |
| 模型与供应商「已配置 / 未配置」 | `GET /api/account/provider-credentials` | `providers[].fields[].configured` |
| 视频凭据「当前进程已配置，重启后失效」 | 同上 | `providers[].session_credentials_configured` |
| 文本 / 创意额度 | `GET /api/account/text-balance`、`/creative-balance` | `balances[].{currency, amount}`、`unavailable` |

账户类型「本机独立账户」「用户名 + 密码」是后端的固定事实（本机单账户模型），不是接口字段；它对应 `registered + interaction_mode` 的既定约束，改版未新增账户体系。

## 2. 首页对话

| 界面位置 | 接口 | 响应字段 |
| --- | --- | --- |
| 顶栏「新对话」标题 | `GET /api/conversations/{id}` | `title` |
| 最近对话浮层 | `GET /api/conversations?domain=&q=` | `conversations[].{id, title, updated_at}` |
| 新建对话 | `POST /api/conversations` | `interaction_mode`、`execution_mode` |
| 消息流 | `GET /api/conversations/{id}` | `messages[].{role, type, content, created_at}` |
| 流式回复 | `GET /api/conversations/{id}/messages/stream` | SSE 事件 → `ConversationMessage` |
| 消息内图片 | `GET /api/artifacts/{id}/content` | 二进制；缩略图走 `getTaskImageUrl` |
| 媒体任务进度条 | `GET /api/conversations/{id}` | `media_jobs[].{status, estimate_fen, approval_status}` |
| 内联审批卡 | `GET /api/approvals` | `CoreApproval.{run_id, node_id, decision, request}` |
| 内联运行活动 | `GET /api/runs/{id}/events` | `RuntimeEvent` |

## 3. 资产库

| 界面位置 | 接口 | 响应字段 |
| --- | --- | --- |
| 产物列表、版本号、时间 | `GET /api/artifacts?lifecycle=` | `CoreArtifact.{id, type, version, created_at, metadata}` |
| 领域归类（工作区/图片/提示词组） | `GET /api/runs` 与 `metadata.schema_name` | `CoreRun.domain`、`CoreArtifact.source` |
| 类型筛选 | `GET /api/artifacts` | `CoreArtifact.type`（image/video/prompt/…） |
| 转入回收站 / 恢复 / 彻底删除 | `POST /api/artifacts/{id}/{action}`，body 带 `expected_lifecycle_version` | 返回体 `lifecycle_state` 必须与请求一致，否则前端报"服务未确认" |

## 4. 工作区（Comic）

| 界面位置 | 接口 | 响应字段 |
| --- | --- | --- |
| 作品标题、版本 | `GET /api/comic/conversations/{owner}/works` | `ComicWork.{work_id, title, version, updated_at}` |
| 创作阶段导航（对话/画面方案/生图指令/图片工作台/故事编排/导出/视频） | 前端 `domains/comic` 定义 + 后端可用性 | 阶段本身是产品结构，可用性由 `ComicWork` 是否存在决定 |
| 分镜任务列表 | 同上 | `ComicWork.tasks[].{…}`（`ComicImageTask`） |
| 图片工作台当前图 / 版本 | `GET /api/comic/projects/{pid}/image-workbench?conversation_id=` | `ComicWorkbenchState.{image, edit_image, auto_analyze}` |
| 故事编排校验结论 | `GET /api/comic/works/{id}/story` | `content_sha256`、`findings[].{code, suggestion}` |
| 图片删除 | `POST /api/comic/projects/{pid}/images/{iid}/delete` | 作品版本 +1，产物转回收站 |

## 5. 外壳（HUD 与天空）

| 界面位置 | 数据来源 | 说明 |
| --- | --- | --- |
| `WORLD x-x · 页面名` | `frontend/src/router.ts` 的 `route.name` | 纯前端路由标签，无接口 |
| `本机已连接 / 连接中断` | `GET /api/runs` + `GET /api/approvals` 每 4s 轮询 | 任一失败即置 `coreOnline=false`，把 `error.message` 放进 `title`，不吞原因；进入工作区后暂停轮询，保留最后一次结果 |
| 时钟 | 本机 `Date`，60s 校准 | 仅本机时间 |
| 天空时段（清晨/白天/傍晚/夜晚） | 本机时间 + `localStorage['kantoku-time-of-day']` | 手动锁定优先；`html[data-tod]` 驱动 `--sky-*` 等令牌 |
| 头像首字 | `GET /api/account/profile` 的 `nickname`/`username` | 无账户时显示占位 |

HUD 里刻意没有原型中的「功能对照」「存档位切换」等演示控件——它们在本项目里没有对应后端状态。

## 6. 字段缺失时的界面表现

| 情况 | 界面表现 |
| --- | --- |
| `avatar` 为 `null` | 显示默认占位图，不拼随机头像 |
| `balances` 为空 + `unavailable` | 显示「无法确定」与后端给的 `message`，不显示 0 |
| `actual_fen` 为 `null` | 显示「计费未知」，不折算金额 |
| 会话无消息 | 显示空状态文案与输入框，不渲染示例对话 |
| 账户未注册 | 登录页切到"建档"形态（多一个确认密码），并允许"暂不创建，继续使用本机工作台" |
