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

## 2026-08-26 Amendment：Core state 初始化前无法停止 Worker

首次 `VALID01-INTERRUPT` 使用 Agent run `20260826-224617-agent`、child
`20260826-224630-801046-bug-loop`。Worker 于 `2026-08-26T14:46:37Z` 启动；管理 Worktree
在 `2026-08-26T14:47:21Z` 首次出现 `src/text_rules.py` 的 partial Diff。测试驱动在约
1.33 秒后执行 identity-bound `agent stop`，命令返回退出码 `2`：

```text
active child 存在，但无法验证其 assist loop 身份
```

停止请求没有写入 child。Worker 继续运行并于 `2026-08-26T14:49:11Z` 完成，随后 Candidate
被冻结。Core Verification 因案例测试总时长超过 60 秒而 `timed_out`；Risk 和 Reviewer
均未运行，父 Agent 最终回到 `planning`，只允许 `replan` 或 `human`，没有进入
`ready_to_commit`。

失败原因是 Agent 已预留 child 和不可变 operation 身份，Worker execution 也已落盘，但
普通 Core `state.json` 要等 Worker 返回并冻结 Candidate 后才创建。`agent stop` 在这段真实
执行窗口错误地要求 Core state 已存在，导致已绑定的 Worker 无法被停止。

允许做一次最小 Runtime 修复：

1. Core state 不存在时，只接受 `operation_kind=worker`；
2. child 目录内必须存在与 active operation 完全一致的 Worker execution；
3. execution 的 `run_id` 必须等于 active child；
4. Core state 已存在但损坏时继续 fail-closed；
5. `verification_retry` 仍必须依赖已初始化的 Core state；
6. 修复后使用相同题目建立全新 run，并在首次 partial Diff 后再次发出 stop。

首次失败 run、未生效的 stop 和后续 Verification timeout 都保留，不计作中断案例通过。

## 2026-08-26 Amendment：Replan 后的状态卡缺少历史提示

四个 Case 完成后进行第一次限时阅读。Repair、Review 未完成和 Worker 中断只读状态卡即可判断；
Replan 的最终状态卡却把 Verification、Risk 和 Reviewer 显示为“尚未运行”。这个表述只对应
Contract revision 2，不能复用 revision 1 的旧门禁，因此没有错误放行；但它没有说明旧
Candidate 实际已经运行过门禁，容易让人工误判为“整个任务从未验证”。

允许做一次展示层修复：

1. 当前 Checkpoint 没有可采用 Observation、但保留 `failed_attempts` 时，状态卡显示历史提示；
2. 如果 Contract 或 Execution Plan 已修订，提示当前门禁只对应新 revision，旧结果不能作为
   通过证据；
3. 不把旧 Verification、Risk 或 Reviewer 重新投影成当前通过；
4. 不扫描目录寻找未绑定的历史“成功”文件。

修复后，真实 Replan run 的状态卡显示：

```text
保留 1 个历史失败 attempt；当前门禁只对应 Contract r2 / Plan r2，
旧结果不能作为本 revision 的通过证据。
```

## VALID-01 结果

### `VALID01-REPAIR`：通过

- Agent run：`20260826-125149-agent`。
- 第一次 child `20260826-125223-161378-bug-loop` 通过 Verification 和 Risk，但 Reviewer
  发现 `src/tag_rules.py` 没有移除输入自带的首尾连字符，返回 `request_changes`。
- Supervisor 生成结构化 Fix Packet，自动恢复 Candidate 为 WIP，并启动第二个 child
  `20260826-125428-356235-bug-loop`。
- 第二次 Worker 修复实现并补充边界测试；Verification、Risk、Reviewer 全部通过，最终
  `ready_to_commit`。
- 全程人工上下文转贴数为 `0`，旧 child、旧 Review 和 Fix Packet 均保留。
- 从 `change_run_started` 到 `agent_completed` 为 `290.164` 秒；其中可由 execution Artifact
  直接归属到 Worker、Verification、Reviewer 的时间合计 `173.132` 秒。

### `VALID01-REPLAN`：通过

- 成功复测 Agent run：`20260826-224019-agent`，child
  `20260826-224032-604479-bug-loop`。
- Worker 只修改 `src/payments/idempotency.py`；Verification 通过，Risk 命中 `payment`
  高风险，Reviewer 发现裁剪订单号可能导致不同输入生成相同幂等键。
- 仅修改 Execution Plan 时，Vega 将 Candidate 同内容恢复为 WIP，但因旧 Contract 未授权
  `payment` 保持 `needs_human`。
- Contract revision 2 只增加 `authorized_risk_reviews=["payment"]`，明确进入
  `awaiting_approval`；第二次人工批准后仍没有沿用旧门禁或生成 `ready_to_commit`。
- 案例最后显式 stop，保留高风险 WIP。测试驱动曾提交两次格式错误的 revision 命令，均在
  状态写入前被拒绝；实际产品路径为 7 条命令，测试驱动总计 9 条命令。
- 从 `change_run_started` 到 `agent_stopped` 为 `295.396` 秒；Worker、Verification、
  Reviewer execution 合计 `96.751` 秒。其余时间包含两次人工批准、修订输入和测试驱动错误，
  不能解释为纯 Runtime 开销。

### `VALID01-INTERRUPT`：通过

- 成功复测 Agent run：`20260826-230715-agent`，child
  `20260826-230922-231794-bug-loop`。
- 首个 partial Diff 出现在 `src/text_rules.py`；`agent stop` 精确绑定 operation
  `4241d7d86e524efc8eafc6f6683af89c`，命令退出码为 `0`。
- execution 从 `running` 进入 `stopped`，没有 Candidate commit，没有第二个 Writer；
  Verification、Risk 和 Reviewer 均未启动。
- 从 stop request 写入到父 Agent 的 `supervisor_human` 稳定 Checkpoint 为 `4.847` 秒。
- 最终 `needs_human`，原因是 partial Diff 的外部副作用状态未知。测试驱动在完整取证后人工
  移除隔离 Worktree；Vega 本身没有回滚或删除现场。

### `VALID01-REVIEW-INCOMPLETE`：通过

- 复用 Agent run `20260826-184314-agent`，child
  `20260826-184349-310171-bug-loop`。
- Worker 修改 `src/slug_rules.py` 和 `src/text_rules.py`；Verification 与 Risk 通过。
- 测试配置把 Reviewer diff 软预算压到 `1000` 字符，单个不可拆分文件组仍超预算；
  Review Queue 保持 `blocked`，覆盖数为 `0/2`。
- Core Finish 和父 Agent 均为 `needs_human`，没有把 Verification、Risk 通过解释成整体成功。
- 从 `change_run_started` 到 `supervisor_human` 为 `153.999` 秒；人工上下文转贴数为 `0`。

## 人工操作与理解成本

| Case | 人工批准 | 人工边界决定 | 人工上下文转贴 | 最小产品命令 |
|---|---:|---:|---:|---:|
| Repair | 1 | 0 | 0 | 3 |
| Replan | 2 | 2 | 0 | 7 |
| Interrupt | 1 | 1 | 0 | 4 |
| Review incomplete | 1 | 0 | 0 | 3 |

说明：

- Replan 的两项边界决定是授权 `payment` 风险和最终停止案例。
- Interrupt 的边界决定是观察到 partial Diff 后请求停止。
- `config check`、轮询 Diff、读取 Artifact 和测试驱动内部命令不伪装成最终用户命令。
- 四张状态卡和决定性 Trace 的一次限时阅读，从开始读取到写出“改动、验证、风险、下一步”
  四项摘要耗时 `22.209` 秒。
- Artifact 无法把确定性编排、Brief/Reflect、人工等待和测试驱动延迟完全拆开，因此“纯 Runtime
  开销”记为 `unknown`；保留机械墙钟和 execution 可归属时间，不做推算。

## 失败语义

以下情况均未进入 `ready_to_commit`：

- 首次 Replan 无法写回 Contract；
- 首次 Worker stop 无法命中尚未初始化 Core 的 child；
- 真实 Verification timeout；
- 未授权 `payment` 风险；
- Reviewer 报告支付幂等键碰撞风险；
- Review Queue 覆盖未完成；
- Worker 在 partial Diff 后被停止。

最终四个通过 Case 中，错误放行数为 `0`。

## 机制决定

保留：

- Change Contract 与 Execution Plan 分层：Replan 证明它能把“实现调整”和“风险授权”分开；
- 单 Writer、operation identity 和隔离 Worktree：中断案例证明可以只停止当前 Worker；
- Git Candidate 与 WIP 恢复：Repair 和 Replan 都复用了同一实际 Diff，没有重造代码快照；
- Fix Packet 自动 Repair：真实 Reviewer finding 在不转贴聊天的情况下交给第二次 Worker；
- Review Queue 的 fail-closed 覆盖检查：部分审查不能冒充完整审查；
- 状态卡和低频 Trace：四个案例可在一次短阅读内判断，但历史门禁只作提示，不重新变成当前证据。

限制：

- Review Queue 仍只在输入超过预算时启用，不扩展成默认多 Reviewer；
- Replan 只自动接受合同内实现调整，冻结字段变化继续要求人工批准；
- Worker stop 后外部副作用未知时不自动重跑；
- Trace 只用于解释路由，不成为第二套成功状态。

本轮不新增：

- Evidence Bundle、独立验证器或新的证据递归；
- 多 Worker 并行、Reviewer 投票、Web UI；
- 自动 push、merge、release 或长期 Memory。

## 仓库验证

- Core 分片：`340 passed`；
- Core-heavy 分片：`143 passed`；
- Supervisor 分片：`293 passed`；
- Security 分片：`423 passed, 2 skipped`；
- Experimental 与 CRWP 控制：`291 passed`；
- 节点收集：`1492 tests collected`；
- wheel 与 sdist 构建成功；
- 干净 wheel 安装、`pip check`、CLI 版本和包内 `engineering-change` loop 通过；
- `agent` extra 安装、`agent capabilities` 和 `SqliteSaver` 独立导入通过；
- Compileall、Ruff、仓库卫生和 `git diff --check` 通过。

Package smoke 的第一次测试脚本误把 `sqlite_checkpoint.available` 当作 capabilities 字段，实际
CLI schema 只公开 `langgraph: true`。该断言没有暴露产品失败；复测改为检查公开 schema，并从
干净环境直接导入 `SqliteSaver`。发布清单和 CI 已同步这一检查方式。

## 状态卡修复后的增量验证

上述完整测试分片在最后一项状态卡展示修复前执行。该修复新增两项定向测试，分别覆盖 legacy
历史 attempt 提示和 Contract/Plan revision 变化后的旧门禁提示；随后执行：

- `tests/supervisor/test_agent_recovery.py` 与
  `tests/supervisor/test_agent_runtime.py`：`84 passed`；
- 当前节点收集：`1494 tests collected`；
- Compileall、Ruff、计划状态检查、仓库卫生、CI YAML 解析和 `git diff --check`：通过。

最终提交的完整分片终态由同一 SHA 的 PR CI 给出；如果 CI 未通过，本完成事件不能进入主线。

## 2026-08-26 Amendment：PR CI 拒绝状态卡模块继续增长

PR #91 的首次 `pull_request` 运行 `32990928442` 在静态检查阶段失败，后续 Job 均被跳过。
失败原因是新增历史提示把 `src/vega/agent_status_card.py` 从 478 行推到 502 行，越过既有
500 行模块门槛；这不是功能测试通过，也不计作 PR CI 通过。

本轮没有放宽架构门禁。历史提示被移到 `agent_status_history.py`，状态卡模块回到 480 行；
本地复测结果为：

- 架构增长门禁通过：C901 `32 -> 32`，Python 模块 `185 -> 186`；
- 两项历史提示定向测试通过；
- Ruff、Compileall、计划状态检查、仓库卫生和 `git diff --check` 通过。

修复后的最终 SHA 仍必须重新执行完整 PR CI。
