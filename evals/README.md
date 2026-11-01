# evals · 评测集与报告

- 评测集：`*.jsonl`，每行一个对象：`{"id", "input", "expect", "rubric"}`
- 运行：M1 起提供 `uv run python scripts/run_eval.py --suite evals/storyboard.jsonl`
- 报告归档：`docs/evals/Mx-日期.md`；**里程碑打 tag 前必须跑一次**（docs/01 §6）

图片工作台使用 `image-workbench.json` 和 `uv run --no-sync python -B scripts/run_eval.py --kind image-workbench`。此入口只读核验已采集的真实对照结果，不调用模型、不写运行文件；材料不足返回 `pending` 和非零退出码，不能将其称为质量通过。24 个图片槽位和 30 条机器起草需求都待人工确认，不是已经采集的真实素材；12 个改图组也未填写。用户当前没有这些材料，因此不能宣称首次改对率或返工轮次已经改善。

每例人工确认后填写 `human_verified=true`、`reviewer`、固定输入指纹及基线/候选两组结果。图片需填原文件路径、SHA-256、明确问题和应保留内容；相同文件不能充当多张独立素材。路径可相对验收集或为明确的绝对路径。图片结果两组各填 `model`、`input_sha256`、`result_source=real_model`、`correct_suggestions`、`incorrect_suggestions`。30 条需求结果各填 `understood_correctly`、`unauthorized_expansion`、`would_clarify` 三个布尔值，另填人工 `expected`。本工具只接受同输入、同模型及声明为真实调用的成对结果，不替用户作人工标注。

12 组外部改图各填 `before_path/before_sha256`，两组结果各填 `model/input_sha256/result_source=real_external`、`after_path/after_sha256`、`first_fix_success/non_target_damage` 布尔值及 `rounds_to_usable`。保留真实改图前取得的基线实现指纹，填写 `baseline_capture`；当前仅有历史工程验证记录，没有真实画质基线，不能把这项改成已采集。只有至少 24 张不同素材、20 条明确/10 条歧义需求、12 组真实改图均合格，建议准确率达到 90%、明确理解至少 95%、歧义不擅自扩大，且返工轮次实际下降、非目标损伤未增加，才可能返回 `quality_improvement_verified=true`。

固定输入指纹通过现有评测模块的 `image_workbench_input_hash(item, kind)` 计算。它绑定图片哈希、原始需求、约束和参考用途；手动需求还绑定上下文与 `edit_scope`。手动案例补填 `image_path`，参考图条目填写 `image_path/sha256/usage`；文件必须存在且哈希一致，篡改输入或重复输入不能作为新的合格案例。工具只能检查文件和记录的一致性，真实来源与人工评分仍需验收人核实。

真实模型采集继续使用原有付费授权、累计预算身份及账本；账本缺失、额度不足或费用未知时停止，不能通过本入口创建新预算身份。离线替身和合成图片只用于工程测试，不能填成真实验收结果。
