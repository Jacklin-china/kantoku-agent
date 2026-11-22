# 文档索引

本目录按“读者要解决的问题”分区，而不是按写作时间堆叠。第一次进入仓库时，
先读根目录 [README](../README.md) 建立整体印象，再按下表跳转。

## 按目的查找

| 你想做什么 | 先读 | 再看 |
|---|---|---|
| 判断这个项目值不值得用 | [README](../README.md) · [当前边界](../README.md#当前边界) | [验证记录](enterprise-validation.md) |
| 把项目跑起来 | [README 快速开始](../README.md#快速开始) · [启动与环境](development-startup.md) | [CONTRIBUTING](../CONTRIBUTING.md) |
| 改代码，先确认边界 | [AGENTS](../AGENTS.md) · [AI 编码规则](engineering/AI_CODE_RULES.md) | [工程手册](engineering/README.md) |
| 定位某个页面或服务在哪 | [代码导航](engineering/code-navigation.md) | [架构说明](architecture/) |
| 改存储、日志、清理逻辑 | [运行存储规范](engineering/runtime-policy.md) | [前端字段映射](engineering/ui-field-mapping.md) |
| 做产品或功能取舍 | [产品原则](product/product-principles.md) | [交付计划（历史）](archive/delivery-plan.md) |
| 改界面或做产品设计 | [下一阶段设计](design/next-stage-design.md) | [账户·密钥·套餐设计](design/account-plan-key-design.md) · [前端设计规范](design/frontend-design-spec.html) |
| 核对一个数字是怎么测出来的 | [验证记录](enterprise-validation.md) | [评测原始报告](evals/) · [JUnit](reports/) |
| 查询历史架构决策 | [架构说明](architecture/) | [架构例外清单](architecture/exceptions.md) |

## 目录说明

| 目录 | 内容 | 维护要求 |
|---|---|---|
| `architecture/` | 逐模块设计、上下文锁、评审治理与架构例外清单 | 设计与实现不一致时以代码为准，并回改文档 |
| `engineering/` | 编码规则、分层边界、运行存储规范、代码导航 | 改动涉及边界时同步更新 |
| `product/` | 产品原则与取舍依据 | 只写仍生效的原则 |
| `design/` | 产品与界面设计规格、交互原型、设计评审稿，以及开题报告配图源（`design/kaiti/`） | 与实现同步；被取代的设计稿移入 `archive/`，不要留在 `design/` |
| `evals/` | 离线评测记录与基准原始 JSON（含日期命名的阶段记录） | 数字必须能由 `scripts/` 复现 |
| `reports/` | pytest JUnit 与失败诊断原始输出 | 不删除不利结果，只追加 |
| `images/` | README 使用的界面截图 | 与当前界面保持一致 |
| `archive/` | 早期 10 周计划文档，**仅作历史参考** | 冻结，不再更新 |
| `archive/personal/` | 简历、证件照、面试材料等**个人资料**，已列入 `.gitignore`，仅本地保存 | 不进仓库、不进交付包；与项目无关的东西一律放这里，不要散在根目录 |

## 命名约定

- 文件名使用小写英文 kebab-case，例如 `runtime-policy.md`；不再新增 `01-`、`02-` 这类序号前缀。
- 阶段性评测记录允许保留日期，例如 `evals/M3-2026-09-12.md`，用于时间线定位。
- 新文档先判断归属：属于长期约束进 `engineering/`，属于设计决策进 `architecture/`，
  属于一次性记录进 `evals/` 或 `reports/`，属于过时材料进 `archive/`。
