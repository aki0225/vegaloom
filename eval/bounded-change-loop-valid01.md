# Bounded Change Loop VALID-01 验收记录

本文记录 `VALID-01` 的固定协议和追加结果。它回答一个实际问题：Vega 把 Plan、Worker、
Verification、Risk、Reviewer 和恢复串起来以后，是否真的减少人工中转，同时继续在证据不足时
停下。

本文件遵守 `eval/AGENTS.md`：协议提交后只追加结果，不改写已经记录的失败、耗时和裁决。

## 2026-08-26 固定协议

### 运行基线

- Vega Runtime 代码基线：`2d5d08cf3c0d0b7e6497f50e79d18a97c1ba718d`。
- 目标仓库均为无远端的临时 Git 仓库；不读取用户目录、凭据或其他项目。
- Writer 与 Reviewer 使用目标仓库 `.vega.yaml` 声明的 `codex-exec`。
- 新增真实运行只保存在 ignored 的 `.local-validation/`；Git 只追加脱敏摘要、计数和必要哈希。
- 不因结果不理想换题、覆盖旧 attempt 或放宽 fail-closed 条件。
- 验收只在真实运行暴露产品缺陷时修改 Runtime；模型实现质量差异不自动归因于 Vega。

### 案例矩阵

| Case | 目的 | 固定输入或来源 | 允许终态 |
|---|---|---|---|
| `VALID01-REPAIR` | 普通 Reviewer finding 自动返回新 Writer 修复 | 已冻结的 AUTO-02 标签规范化真实 run | `ready_to_commit` 或预算耗尽后 `needs_human` |
| `VALID01-REPLAN` | 实际 Diff 命中未授权风险时要求 Contract revision | 新建最小支付规则仓库；风险路径固定为 `src/payments/**` | `awaiting_approval`、`needs_human` 或人工批准后的安全停止 |
| `VALID01-INTERRUPT` | Worker 留下 partial Diff 后按身份停止并对账 | 新建最小文本处理仓库；首次观察到允许路径 Diff 后请求 stop | `needs_human` 或 `stopped` |
| `VALID01-REVIEW-INCOMPLETE` | Review Queue 未完成时不能采用部分结果 | 已冻结的 AUTO-03 软预算失败真实 run | `needs_human` |

`VALID01-REPAIR` 与 `VALID01-REVIEW-INCOMPLETE` 已在对应功能实现时真实运行。本轮固定复用它们，
不为获得更好数字选择性重跑；当前主线通过定向回归和完整测试确认这些路径没有被后续改动破坏。

### `VALID01-REPLAN` 输入

目标仓库只包含：

- `src/payments/idempotency.py`；
- `tests/test_idempotency.py`；
- `.vega.yaml`；
- `AGENTS.md`。

任务要求同一订单的重试复用稳定键，并通过 pytest。`.vega.yaml` 将
`src/payments/**` 配置为 `payment` 必审风险；初始 Change Contract 不授权该风险。

固定步骤：

1. 创建并批准 Contract revision 1；
2. 运行真实 Worker、Verification、Risk 和 Reviewer；
3. 保留真实 Diff，提交只改变 Execution Plan 的 revision 2；
4. 确认 Vega 因实际风险路径缺少授权而保持 `needs_human`；
5. 提交 Contract revision 2，仅增加 `authorized_risk_reviews=["payment"]`；
6. 确认 revision 等待人工批准，批准后仍不把高风险代码自动标成可提交；
7. 停止本案例，不执行自动 push、merge 或高风险放行。

### `VALID01-INTERRUPT` 输入

目标仓库包含两个短小 Python 模块和对应测试。任务要求同时实现多项文本规范化行为，保证 Worker
有机会先产生 partial Diff。

固定步骤：

1. 创建并批准 Contract；
2. 后台启动真实 `vega agent run`；
3. 每秒只读检查管理 Worktree；
4. 首次观察到允许路径 tracked Diff 后，立即执行 identity-bound `vega agent stop`；
5. 等待控制进程退出并读取 Agent、child、execution、Verification、Risk、Reviewer 和 Finish；
6. 如果超时前始终没有 Diff，如实记录为 `no-partial-diff`，不重跑同一案例。

### 度量口径

- **人工批准数**：`agent approve` 的次数。
- **人工边界决定数**：Contract revision、stop、恢复或最终放弃等必须由人作出的决定次数。
- **人工上下文转贴数**：人把 Worker 输出复制给 Reviewer，或把 Reviewer finding 再复制给
  Worker 的次数。由 Vega Artifact 自动传递记为 `0`。
- **人工命令数**：为推进产品流程必须手工输入的 Vega 命令数；测试驱动脚本内部命令单独列出，
  不伪装成最终用户操作。
- **恢复耗时**：从 stop/异常被记录，到稳定的 `stopped` 或 `needs_human` Checkpoint 的时间。
- **最终理解耗时**：从开始读取状态卡与 Finish，到能写出“改了什么、验证如何、风险和下一步”
  四项摘要的实测时间。
- **运行开销**：Agent 总墙钟时间减去 Worker、Verification 和 Reviewer 已记录执行时长。无法
  从 Artifact 可靠拆分时写 `unknown`，不做估算。
- **错误放行**：任一 Verification 失败、未授权风险路径或未完成 Review 进入
  `ready_to_commit`，本项立即失败。

### 对照口径

不把假设的“原生 Review + CI + 胶水脚本”包装成实测基线。本轮只给出同一事实链的机械下界：

- 没有 Vega 时，至少需要人工准备 Reviewer 输入、转贴 finding、重新绑定修复后的 Diff 和测试，
  再整理最终判断；
- Vega 的目标不是比模型更会写或更会审，而是让这些传递由 Commit、Artifact 和状态机完成；
- 如果最终仍频繁要求人工复制上下文，或者状态卡不能快速回答最终四项摘要，相关机制判定为没有
  达到日常价值。

### 完成条件

1. 四个 Case 均有不可改写的真实结果；
2. Repair 自动产生新 Writer attempt，过程中人工上下文转贴为 `0`；
3. 合同边界 Replan 产生明确的 Contract Diff 和批准节点；
4. Worker 中断不启动第二 Writer，不继续 Verification、Risk 或 Reviewer；
5. Review 未完成、验证失败或风险越界均不能进入 `ready_to_commit`；
6. 完成 package smoke、完整测试分片、仓库卫生检查和 PR CI；
7. 根据度量明确列出保留、降级或删除的机制，不以“以后可能有用”为理由继续扩张。

## 2026-08-26 Amendment：高风险 Candidate 无法进入 Replan

首次 `VALID01-REPLAN` 使用 Agent run `20260826-222337-agent`、child
`20260826-222349-405477-bug-loop`。真实 Worker 只修改
`src/payments/idempotency.py`；Verification 通过，`payment` 必审 Risk 和 Reviewer 完成，
Core 保持 `needs_human`，没有进入 `ready_to_commit`。

随后按固定协议提交 Execution Plan revision 2 时，CLI 返回“当前状态不能修订 ChangeRun”。
原因不是风险判断本身，而是 Core 已冻结 `active_candidate_sha`，revision 入口禁止任何 active
Candidate。第二次 Contract revision 和 approve 同样未生效；验收脚本最后显式 stop，run 变为
`stopped`。这次失败不计作合同边界 Replan 通过，也不覆盖原 Artifact。

该结果暴露了产品控制缺口：Vega 能要求人处理高风险，却没有让人把处理结果写回 Contract 的
可达路径。允许做一次最小 Runtime 修复：

1. `agent replan` 先按旧 Contract、旧 Execution Plan 和 Candidate ref 校验 Git Candidate；
2. 校验通过后，把 Candidate 同内容还原为 parent 上的 WIP，保留旧 Candidate ref；
3. 未授权风险仍保持 `needs_human`；
4. 只有新增风险授权的 Contract revision 才进入 `awaiting_approval`；
5. 高风险 Core 的人工检查语义保持不变。

修复后使用相同目标、测试、风险规则、模型配置和 timeout 建立全新 run。首次失败与新 run 都写入
最终结果；不更换题目，也不把失败 attempt 改写成成功。
