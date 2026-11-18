# Kantoku 可靠性补强与验证记录

基线：`92de03a747e5d522b6f3dd78a0ea7f057394769a`。本轮在独立 `enterprise-upgrade` 工作树实施，原 `main` 克隆、用户 Windows 项目和远端 GitHub 分支均未修改。

## 改动边界

现有 `core/llm.py` 已具备主/备用模型、可恢复错误重试、连接复用、流式输出后不重试、usage 与 trace；`GraphRuntime` 已有节点重试、审批等待、检查点和恢复。本轮没有重复造一套 LLM/重试框架，也没有重写现有运行时。

新增内容集中在：

- `tools/storyboard.py`：新增 `parse_storyboard_response`，在写库前严格校验 JSON、Pydantic 契约及 20 镜数量；原 `generate_storyboard` 主路径已接入， malformed 输出直接拒绝而非隐式修复。
- `structured_evaluation.py`：25 条结构化输出有效/故障注入契约，不调用模型、不写业务数据库。
- `scripts/reliability_benchmark.py`：真实 `GraphRuntime` + 独立 SQLite + 子进程 `os._exit(73)`，覆盖 7 类崩溃点。
- `scripts/offline_quality_gate.py`：将已执行、未运行和待人工验收分开，禁止把离线替身当作真实模型质量。
- `scripts/context_ab_benchmark.py`：完整历史与“摘要 + 按需读取”的确定性工程 A/B。
- `scripts/structured_stress_benchmark.py`：120 组合法/异常输出的真实写库边界验证。
- `scripts/recovery_cost_benchmark.py`：6 个崩溃点 × 5 次同点恢复/从头重跑 A/B。
- `scripts/context_cumulative_benchmark.py`：80 轮显式快速路径与完整理解路径的累计调用和 Token 对照。

## 20 次真实子进程故障注入

报告：`docs/evals/process-recovery-20.json`。

- 汇总 **20/20 passed**；覆盖 prepare 检查点后、external 副作用前/提交后/检查点后、archive 前/提交后/检查点后。
- 每例由崩溃进程和新的恢复进程组成，检查最终状态、完成节点、检查点、事件序列和 SQLite 模拟幂等副作用计数。
- `after_external_commit` 三例的外部副作用执行尝试均为 2 次，但 `effect_count=1`，验证的是提交后崩溃重放下的幂等结果，而不是网络 exactly-once 承诺。
- 这是合成三节点流程的真实进程中断，不代表主机断电、多节点部署、真实付费供应商或分布式共识保证。

复现：

```bash
python scripts/reliability_benchmark.py --runs 20 --output docs/evals/process-recovery-20.json
```

## 离线质量门禁

报告：`docs/evals/offline-quality.json`。

- Prompt 一致性：**20/20**。
- 结构化输出契约：**25/25**。
- 已执行合计：**45/45**。
- 真实模型分镜 10 条、导演 4 条为 `not_run`；图片工作台为 `pending`；`quality_release_ready=false`。

45/45 只表示确定性 Prompt 锚点和结构契约通过，不表示真实模型生成、导演方案、图片质量或约束违规率达到发布标准。

```bash
python scripts/offline_quality_gate.py --output docs/evals/offline-quality.json
```

## 上下文 A/B

报告：`docs/evals/context-ab.json`。五个场景为早期事实、最新纠正、无匹配、跨会话隔离和 240 条长历史，功能探针 **5/5 passed**。模型调用使用可返回合法结构的确定性替身，没有外部模型。

| 口径 | 估算 token |
|---|---:|
| 完整历史最终提示词合计 | 57,992 |
| 摘要 + 按需读取最终提示词合计 | 22,059 |
| 最终提示词减少 | 61.96% |
| 含摘要压缩与理解调用的管理路径总模型输入 | 120,412 |

最后一行说明：**最终轮减少不能写成总成本同比例下降**。Token 使用项目内 `token_size` 字符估算，不是供应商账单；该实验验证有界上下文、来源读取和隔离，不是语义模型召回率。

```bash
python scripts/context_ab_benchmark.py --output docs/evals/context-ab.json
```

## 120 例结构化写库边界压力实验

报告：`docs/evals/structured-stress-120.json`。在现有聊天边界注入确定性输出，20 组合法分镜全部经真实 `generate_storyboard` 工具写入隔离 SQLite；100 组异常全部在数据库创建前拒绝，合计 **120/120 passed**。异常覆盖缺字段、19 镜、镜号重复/不连续、空描述、零或浮点时长、字段类型错误及额外字段等 10 类。这是合成故障注入下的 fail-closed 验证，不是实际模型违规率。

```bash
python scripts/structured_stress_benchmark.py --output docs/evals/structured-stress-120.json
```

## 30 例同崩溃点恢复成本 A/B

报告：`docs/evals/recovery-cost-30.json`。6 个确定性 Provider 测试替身节点，每个崩溃点重复 5 次，共 **30/30 passed**；每例使用真实子进程退出和独立 SQLite，并比较同一点恢复与从头重跑。

| 口径 | 同点恢复 | 从头重跑 | 变化 |
|---|---:|---:|---:|
| 平均副作用节点调用 | 3.5 | 6.0 | 减少 41.67% |
| 平均全部节点调用（含恢复流程节点） | 7.0 | 9.5 | 减少 26.32% |
| 重复外部效果 | 0 | 0 | — |

41.67% 只描述副作用节点恢复调用，不能写成全部模型调用、Token 或账单同比例下降；该实验也不证明网络 exactly-once。

```bash
python scripts/recovery_cost_benchmark.py --repeats 5 --output docs/evals/recovery-cost-30.json
```

## 80 轮累计上下文：两种路径分开报告

功能探针在两条路径均为 **80/80 passed**，但调用拓扑不同，不能混用收益。

| 路径 | 基线总输入 | 管理路径总输入 | 总输入变化 | 最终回答输入变化 | 管理路径调用 |
|---|---:|---:|---:|---:|---:|
| 显式快速路径（`--direct`） | 433,921 | 372,213 | 减少 14.22% | 减少 30.60% | 140（60 摘要 + 80 回答） |
| 完整理解路径 | 433,921 | 919,801 | 增加 111.97% | 减少 29.32% | 220（60 摘要 + 80 理解 + 80 回答） |

报告分别为 `docs/evals/context-cumulative-direct-80.json` 与 `docs/evals/context-cumulative-80.json`。全量历史均为 80 次回答调用；Token 为项目内估算而非供应商账单。快速路径可写“总模型输入减少 14.22%”，完整理解路径则不能宣称总体 Token 或成本下降。

```bash
python scripts/context_cumulative_benchmark.py --turns 80 --direct --output docs/evals/context-cumulative-direct-80.json
python scripts/context_cumulative_benchmark.py --turns 80 --output docs/evals/context-cumulative-80.json
```

## 测试与未验收边界

常规测试仍使用 `uv run pytest`；本轮新增/相关定向测试为 **17 passed，0 skipped**，JUnit 位于 `docs/reports/kantoku-upgrade-focused.xml`。离线报告和全量/定向 pytest 是不同证据，不应相加成一个“总通过率”。

未运行真实模型生成与人工质量评审，未验证真实图片工作台质量提升，未做真实视频 Provider、付费供应商故障、多节点部署或主机断电测试。简历不得恢复早期无证据的 Token 固定降幅、真实召回提升、约束违规率或“企业级认证”表述。
