# Supervisor Agent V1 当前交接

> 日期：2026-08-15
>
> Gate 3A 来源分支：`codex/supervisor-gate3a`
>
> 实施基线：`main@43f0e04`
>
> 状态：`Gate 2B gate-exit-pass / Gate 2C gate-exit-pass / Gate 3A gate-exit-pass / Gate 3B machine-a-insufficient-handoff-opportunity / machine-b-not-started / gate-not-passed / Gate 3C 冻结`

## 当前结论

Gate 0、Gate 1 与 Gate 2A 已进入主线。独立审阅发现并修复了 Observation 发布、
Plan 发布顺序、Recovery 证据引用和中间 Work Item 门禁四类问题。修复后的代码 HEAD
`4180e7e` 已通过 workflow `31718078414` 的 9 项 CI，最终文档 HEAD `8ca75f2` 已通过 workflow
`31718680069` 的 9 项 CI。PR `#57` 已以 `6a5c927` 合并到 `main`，Gate 2A 没有遗留阻断项。

Gate 2B 已获人工批准，并在单一实验分支和专用 Worktree 完成机械合同。真实 Codex Adapter 的
信任边界、两个冻结案例、预算、超时和停止条件见
[`SUPERVISOR-AGENT-GATE-2B-PLAN.md`](SUPERVISOR-AGENT-GATE-2B-PLAN.md)。代码 HEAD
`799bb29` 已通过 PR `#58` workflow `31775697034` 的 9 项 CI。随后真实运行暴露的三项
Adapter 集成问题已分别在 `a213f0e`、`fa99682` 和 `9ed0b62` 修复；可跨机器重建的
`SAG2B-02` 合同记录在 `905b242`。

两个冻结真实案例已经执行：

- `SAG2B-01` 的最终 R4 接通真实 Worker，但新增测试仍是未跟踪文件，现有 Core 在 Verification
  前 fail-closed；Supervisor 形成 `human` 决策，没有启动 Reviewer；
- `SAG2B-02` 在首次允许范围内 tracked Diff 后，通过身份绑定的 `agent stop` 停止 owned
  execution，保留 partial Diff 并进入 `needs_human`；Verification、Risk 和 Reviewer 均未启动。

真实案例、最终 PR CI 与合并前审阅已经满足冻结退出条件，Gate 2B 判定为
`gate-exit-pass`。Gate 3 仍保持冻结，需另行批准；本阶段不再重跑案例或增加 Adapter 能力。

Gate 2C 的 SAG2C-02 已在修正验证入口后完成真实完整成功路径。Worker 只修改三条批准路径，
缺陷复现、`tests/test_requirements.py`（`5308 passed`）、Ruff 和 `git diff --check` 均通过；
Workspace、Scope、Artifact integrity、Evidence freshness、Risk、独立 Reviewer 和 Finish
均形成有效证据，Supervisor 根据机器 Observation 进入 `finalize`。这条结果只证明单
Work Item、低风险、可重建案例的完整控制链，不证明目标补丁已被人工合并、跨机器恢复、
Claude Code Adapter、Memory 或通用修复成功率。

Gate 3A 已实现 Handoff 生产端，并完成同机两个隔离 clone 的机械往返。A 侧生成
Handoff Checkpoint、Resume Capsule、Task Card、manifest 和人工 Git 清单；人工只提交 WIP
与 Task Card。B 侧不复制旧 `runs/`、Trace、SQLite 或聊天，仅凭 Git Task Card 重建新本机
run。PR `#60` 的 9 项 CI 与两轮独立本地审阅均无剩余阻断项，Gate 3A 判定为
`gate-exit-pass`。Gate 3B 已获批准，固定控制器、未知副作用继承和人工副作用裁决门禁均
已通过 PR CI；控制器重新冻结后完成机器 A 正式 attempt，但 Windows 沙箱工具环境阻断，
没有形成可交接 partial Diff。Gate 3B 未通过，机器 B 未启动，Gate 3C 继续冻结。

既有 `vega do / loop / goal`、Reviewer、Verification、Risk Gate、Finish 的命令行为与成功
语义未改变；打包后的顶层 CLI 仍以 opt-in `vega agent` 暴露实验能力。Graph 只能路由到
`finalizing`，不能自行写入 `ready_to_commit`。

## 2026-08-15 Gate 3A 本地证据

实现增加：

1. `vega agent checkpoint --run <agent-run> --handoff --reason <reason>`；
2. Handoff Checkpoint、Resume Capsule、Git Task Card、manifest、summary 和 Trace；
3. Task Card 独占发布、逐级链接/junction/reparse point 拒绝和本机绝对路径拒绝；
4. active Writer、`needs_human`、未知副作用、Workspace 漂移、错误仓库历史、错误 HEAD、
   不安全 Git index 与 Artifact 发布失败的 fail-closed 门禁；
5. 恢复时只接受当前分支、Git 已跟踪且位于当前 Handoff HEAD 的 Task Card；旧门禁降为
   historical，新 Verification、Risk、Reviewer 从 `not_run` 开始；
6. Vega 不自动执行 `git add`、commit、push、release、删除或长期 Memory 写入。

同机双隔离副本 Dogfood：

```text
A 侧 Agent run：20260815-132909-agent
Handoff 状态：handoff_ready
WIP：src/example.py
人工 Handoff 提交：5e856ab
B 侧新 run：20260815-144839-agent-resume
B 侧 phase：ready
B 侧 current_work_item：W1
B 侧 Trace：task_card_resumed
B 侧 Verification / Risk / Reviewer：not_run
```

B 侧 clone 不包含 A 侧 `runs/`。Task Card、A 侧 manifest/checkpoint、B 侧 State/Trace/Task Brief/
状态卡分别有 SHA-256 记录在追加式运行证据中。该运行只证明同机机械接力，不证明真实跨机器、
真实模型继续执行或日常收益。

本地验证：

```text
CI 同款分片：1239 collected，1227 passed，12 skipped，0 failed
Handoff / Task Card / 恢复定向：19 passed，1 skipped
Ruff：通过
compileall：通过
repository hygiene：通过
architecture growth：通过（C901 35->35，Python 模块 126->128）
git diff --check：通过
```

唯一跳过项是当前 Windows 会话无权创建目录符号链接；实现仍显式拒绝 symlink、junction 和
reparse point，PR Windows CI 仍是合并前权威证据。四分片首次并行运行曾出现一个既有 smoke
节点超时和三个 Git 环境失败；前者低负载重跑通过，后者在按 CI 补齐历史对象并设置进程级
`safe.directory` 后全部通过，未为绿测修改生产语义。

## 2026-08-14 合并前独立审阅

独立审阅没有重跑 `SAG2B-01` 或 `SAG2B-02`，只检查最终分支的并发、执行身份、停止、恢复和
机器证据。审阅确认并修复六项合并阻断：

1. 并发 `agent run` 可能在 Writer binding 串行化之前各自创建 assist child；现在 child 创建与
   binding 位于同一短 mutation 临界区，真实 Worker 执行仍在锁外；
2. 找不到 `codex` 或 output schema 写入失败时原先没有 terminal `execution.json`，会让已发布
   binding 永久无法对账；现在启动前失败也保存匹配 operation 的失败终态；
3. Finish 摘要同时出现 Verification 通过与失败事实时原先优先采用通过；现在任何明确失败事实
   都优先，不能进入完成路由；
4. Supervisor 单 Writer 原先会继承用户或项目配置中的 `workspace-write` 网络和额外可写根；
   现在显式关闭网络并清空额外 writable roots，避免 Plan 外写入和不可确认外部副作用。
5. child 在 Worker 后可能产生更新的 Verification 或 Reviewer execution；Recovery 现在按
   active operation 与 `worker` step 唯一绑定原 Worker 证据，不再使用最新 Core execution。
6. Finish 的 `verification_passed=true` 原先可能先于 Artifact 完整性和新鲜度判定；现在
   不可信证据一律为 `blocked`，通过标记不能覆盖 fail-closed 门禁。

截至该次审阅，状态文档已同步为 Gate 2C `gate-exit-pass`，同时明确 Gate 3 当时仍冻结。

Gate 2C 文档与重建材料的本地验证：

```text
pytest 分片汇总：1224 collected，1213 passed，11 skipped，0 failed
Ruff、compileall、repository hygiene、architecture growth、git diff --check：通过
```

PR `#58` 仍是 Gate 2B 的历史合并证据。Gate 2C 记录分支必须通过其自身面向 `main` 的
Python 3.11/3.12 与 Windows CI 后才能合并；CI 未完整通过时必须保持 fail-closed。
Gate 3 不随 Gate 2C 记录 PR 自动开启。

## Gate 2B 当前实现

1. `vega agent run --run <agent-run> --timeout <seconds>` 启动一个真实 `codex exec` Worker。
2. 首次 attempt 创建现有 assist child，冻结 baseline 后绑定唯一 child、operation 和显式
   execution identity；Reviewer 打回后的 repair 复用同一 child，并为新 operation 保留独立
   execution 目录。
3. Worker 最终消息只解析为 `claimed_status / summary / tests_claimed /
   remaining_questions`，不接受 changed files、测试状态、风险或完成声明。
4. Worker 退出后先检查批准 Plan 的总路径范围，再由现有 child Core 执行 Workspace、
   Verification、Risk、Reviewer 与 Finish；Core 结束后再次检查 Plan 范围，验证或其他
   Core 步骤产生的越界修改同样不能进入成功路由。
5. Adapter 读取 child execution、状态和 Finish 摘要，形成 `machine_reconcile` Observation；
   外部 `agent observe` 仍然只保存 Claim。
6. `blocked` Risk/Reviewer/Verification 直接进入人工；明确失败才按范围选择 repair 或 replan。
7. active child 的 `stop` 只向与 Agent operation 身份匹配的 owned execution 写入停止请求；
   `recover` 能读取 sibling assist child 的 execution 并在 Agent run 内保存摘要引用。
8. 单个 Work Item 最多两次 dispatch；Plan 外路径修改不进入 Core，直接形成 replan 或人工结果。
9. 本轮没有修改 `loop_runtime.py`、Verification、Risk、Reviewer、Finish、默认
   `do / loop / goal` 或自动 Git 行为。
10. Gate 2B Adapter 当前只接受一个未完成 Work Item。现有 assist child 会拒绝把旧 tracked
    diff 当作新 child baseline，因此多 Work Item 累计 Diff 归因仍未证明；本轮没有为通过测试
    而放宽该门禁。
11. 首次真实 Worker 要求干净 Workspace；repair Worker 如果没有产生新的 Workspace 变化，
    不运行 Core，也不把上一 attempt 的 Diff 重记为当前修复证据。
12. 批准 Plan 和跨机器恢复会在冻结 Workspace 前准备 Vega 自己的
    `.tmp/vega-verification` 根目录，避免首个 assist child 把受控运行目录误判为用户漂移。
13. Agent operation 使用与 Windows Job 兼容的 UUID 十六进制身份，并与
    `execution.json.execution_id` 保持一致。
14. Supervisor Adapter 固定单 Writer，真实 Codex Worker 显式禁用目标项目可能启用的
    `multi_agent` 与 `multi_agent_v2`，关闭 `workspace-write` 出站网络并清空额外可写根目录；
    其他 `CodexExecRunner` 调用保持原行为。
15. 创建 assist child 与提交 Writer binding 位于同一个 Agent run mutation 临界区，避免并发
    `agent run` 先创建多个孤立 child；真实 Worker 启动前已释放该锁，`stop / recover` 不会被
    整段模型执行阻塞。
16. `CodexExecRunner` 在找不到可执行文件或 output schema 写入失败时也写入匹配 operation 的
    terminal `execution.json`；Adapter 可以形成机器 Observation 并释放 binding，而不是留下
    永久无法对账的 Writer。Finish 摘要与 iteration 出现矛盾时，Verification 失败证据优先。

## Gate 2A 已进入主线的能力

1. 对同一 Agent run 的批准、计划修改、dispatch、observe、recover、pause、resume、steer 和
   stop 使用 mutation lock 串行化。
2. dispatch 在发布 `acting` State 前，以 run-local write-once marker 登记 operation identity；
   同一 run 不允许复用旧 `operation_id`。随后原子持久化唯一 child/operation binding，并保守
   标记 operation 可能已经开始；该标记表示已越过不可自动重试边界，不等于证明真实进程已经启动。
3. CLI 输入的 Observation 永远按外部 Claim 保存，不能伪造完成状态或 Gate 结果；受信
   Observation 必须绑定当前 Work Item、child 和 operation；Observation ID 不能包含路径，
   外部 Claim 与 Recovery 机器对账均使用 write-once Artifact，不能复用后覆盖旧证据。
4. 旧 binding 尚未被可靠核销时一律禁止第二 Writer；Worker 仍存活、execution 缺失或损坏、
   execution identity 与当前 operation 不一致、Trace 损坏等情况都会保留原
   child/operation binding。
5. 当前 dispatch 后不存在可依赖的 `worker_reserved` 中间态或第二次 `confirm_started`。
   只有未来由受信 Adapter 在同一原子边界内证明“进程未启动”时，才可评估自动释放旧 binding；
   Gate 2A 对缺少此类证据的现场保持 fail-closed。
6. 旧版两阶段 dispatch 留下的 `operation_started=false` 只表示未取得启动确认，不能作为
   operation 未启动证明；升级后恢复一律保留原 binding 并交给人工。
7. Recovery 只有在 Checkpoint 成功落盘后才提交解除 Writer 的新 State；Checkpoint 写入失败时
   保留原 binding，避免崩溃窗口错误释放 Writer。
8. partial diff、数据库/支付/部署/外部 API 副作用、损坏状态、未知 schema 和不完整现场均
   fail-closed 进入人工处理。
9. SQLite Graph checkpoint 丢失时仍从 Agent State、Checkpoint 与真实 Workspace 对账。
10. 增加 `pause`、`resume-local`、`stop` 和结构化 `recover` 命令；不执行自动回滚、提交或推送。
11. Plan revision 写入前先撤销旧批准和 dispatch 权限；批准、恢复和 `next / repair` 只有在当前
    Checkpoint 与 Task Brief 成功落盘后，才发布可 dispatch 的 State。
12. dispatch 前重新校验 State、批准 Plan、safe Checkpoint 与 Task Brief manifest 属于同一
    revision、Work Item 和 Workspace，拒绝 stale Plan 或 stale Task Brief。
13. 跨机器 Task Card 恢复只有在 Checkpoint、Task Brief、Trace 和状态卡全部写入成功后，才
    最后发布可 dispatch 的 State；任何向调用方报告的写入失败都不会留下隐藏 ready run。
14. 同步实施计划、状态权威 ADR、Roadmap、文档导航和 CI core-cli 分片。
15. 受信 Observation 使用独占创建；Checkpoint 和下一轮 Task Brief 成功后才发布推进后的 Plan，
    State 继续作为最后的调度安全闩。中间任一步失败都保留旧 active Writer，且不会覆盖旧证据。
16. Recovery Observation 显式引用 operation marker 和可用的 `execution.json`；中间 Work Item
    若 Verification、Risk 或 Reviewer 证据缺失或过期，一律转人工，不启动下一 Writer；
    明确失败或阻断仍按既有规则进入 `repair / replan / human`。
17. 增加打包 CLI 回归，确认新增 opt-in `agent` 后，既有 `do / loop / goal` 命令仍然存在。

## Gate 2B 本机机械证据

通过：

```text
Codex Adapter 定向回归：11 passed
active child stop/recover 定向回归：3 passed
显式 execution identity 定向回归：通过
Agent 机器 Observation、blocked Risk 与 CLI 定向回归：通过
完整测试节点收集：1216 collected
architecture growth：通过（C901 35->35，Python 模块 122->126）
Ruff：通过
compileall：通过
repository hygiene --base-ref origin/main：通过
git diff --check：通过
PR #58 代码 HEAD 799bb29 CI：9/9 success
workflow：31775697034
SAG2B-01 R4：真实 Worker completed；Workspace Gate 因 1 个未跟踪测试文件阻断；
Verification=blocked，Risk/Reviewer=not_run，Supervisor=human
SAG2B-02：75.112 秒出现 tracked partial Diff；身份绑定 stop 成功；
execution=stopped，termination_unconfirmed=false，Verification/Risk/Reviewer=not_run，
Supervisor=human
```

当前本机使用 Python 3.14，首次加载 LangGraph/LangChain 依赖较慢，并出现其已知 Pydantic V1
兼容性警告；定向测试按小集合串行执行。项目 PR CI 使用 Python 3.11/3.12，仍是本轮合并前
必须取得的权威自动化证据。

SAG2B-01 前三次登记运行也保留在本机证据中：首个 child 运行目录漂移、Windows operation
identity 格式错误，以及目标项目 Codex 多代理配置冲突。前三个现场分别发生在真实 Worker
启动前、owned process 创建前和模型 turn 开始前；均没有目标 Diff。每次后续执行都使用新
Agent run 和新隔离目标，没有覆盖旧记录。

SAG2B-02 的外部轮询脚本读取 Agent State envelope 时漏取 `data` 字段，因此停止原因使用了
更保守的“Writer 活性无法同时确认”。Vega 自身的停止命令仍在活动 binding 上验证并写入相同
execution ID，最终 lease 为 `stopped`；该偏差不需要补跑，已写入正式结果说明。

## 已有 Gate 2A 证据

通过：

```text
Agent 状态、恢复、Task Card 与锁定向回归：62 passed
审阅修复后完整测试节点收集：1192 collected
旧代码 HEAD 完整测试节点收集：1188 collected
旧代码 HEAD PR #57 CI：9/9 success
审阅修复代码 HEAD 4180e7e PR #57 CI：9/9 success
workflow：31718078414
最终文档 HEAD 8ca75f2 PR #57 CI：9/9 success
最终文档 workflow：31718680069
主线合并提交：6a5c927
Ruff：通过
compileall：通过
architecture growth：通过（C901 35->35，Python 模块 104->122）
repository hygiene --base-ref origin/main：通过
CI YAML 解析与 core-cli 分片：通过
git diff --check：通过
UTF-8 BOM 检查：通过
```

项目 CI 使用 Python 3.11/3.12。本轮审阅修复后的 Agent 回归已取得明确终态：Contract 19、
Recovery 20、Runtime 19、Task Card 4，共 `62 passed`。Runtime 拆为 10/9 节点，分别用时
34.62 秒与 30.49 秒；Recovery 拆为 10/10 节点，分别用时 33.18 秒与 33.65 秒。整文件 Runtime
和 Recovery 的首次命令超过 60 秒，只能记录为超时未验证，不能计为失败或通过。

审阅修复代码 HEAD `4180e7e` 已完成 workflow `31718078414` 的 9 项 PR CI。当前完整节点
收集为 `1192 collected`，比旧代码 HEAD 增加 4 个审阅回归。最终文档 HEAD `8ca75f2` 也已完成
workflow `31718680069` 的 9 项 CI，并以 `6a5c927` 合并到 `main`。

## 后续接力

1. 不重跑 `SAG2B-01` 或 `SAG2B-02`，保留既有冻结案例和负结果现场。
2. 日常仍以 `vega do / loop / goal` 为默认入口；`vega agent` 继续保持 opt-in。
3. Gate 3A 已判定为 `gate-exit-pass`，不再扩大其机械接力范围。
4. Gate 3B 保留 `SAG3B-01` 的
   `insufficient-handoff-opportunity / environment-blocked` 结论，不重跑该 Case。
5. `SAG3B-02` 已形成 `machine-a-handoff-ready`：真实 Worker 只修改两个批准文件，
   identity-bound stop 成功，外部副作用裁决为 none，并生成 `handoff_ready` Task Card。
6. `SAG3B-02` 机器 A 使用提交前控制快照；提交前架构修复使最终控制提交与该快照不再字节
   一致，因此不得继续把机器 B 结果计为正式 Gate 3B。
7. 不把 Claude Code、多 Work Item 或日常价值观察混入同一运行。

## 下一 Gate 的边界

Gate 3B 只应复用当前：

- Git Task Card 与 Resume Capsule；
- Plan 批准、Task Brief 和单 Writer；
- Workspace、分支、Handoff HEAD 与 WIP digest 对账；
- 新机器的 Checkpoint、Trace 和状态卡；
- Verification、Risk、Reviewer 与 Finish。

Gate 3B 不新增多 Worker、Provider 平台、服务端、自动重试、自动 commit/push/release、
Claude Code Adapter 或第二套成功裁决。

## 未完成事项

- 尚未证明多 Work Item 的真实 Adapter 累计 Diff 归因；Gate 2B 当前 fail-closed 拒绝该形态。
- 尚未验证跨机器 Task Card 接力，它属于 Gate 3B。
- 尚未验证真实模型在新机器继续当前 Work Item，也未观察恢复时间与再次使用意愿；它们分别属于
  Gate 3B 和 Gate 3C。
- Claude Code Supervisor Adapter 不属于 V1 Gate 3，V1 完成后再单独评估。
- 尚未决定 `v0.2.0` 发布时点。
- 受信 Observation 已经 write-once；若其后的 Checkpoint 写入失败，State 会保守保留 active
  Writer，但重试需要新的 Observation ID。该路径不会开放第二 Writer，后续是否需要事务化
  Observation/Decision/Graph 由 Gate 2B 真实 Adapter 故障注入决定。

## 2026-08-16 Gate 3B R2 交接状态

当前开发分支为 `codex/supervisor-gate3b-r2`，基线
`main@d2c28103d352f251f1bf20d89758e666dba086ed`。控制器修复已固定为
`5d252d4b366e7a1bed1eb8370a4c599401055a21`，只修改 Adapter、Worker Claim、显式
verification 下传及其回归，共 6 个文件；没有修改默认 `do / loop / goal`。

SAG3B-02 机器 A：

```text
Agent run: 20260816-121500-agent
child: 20260816-121529-270617-bug-loop
operation: e44ed6747d70430d8388b58d82aa5d0d
result: machine-a-handoff-ready
changed files: 2
external side effects: none
handoff: ready
machine B: not started
```

active Writer 时的第一次 `agent stop` 只发送身份绑定请求；原 `agent run` 返回后，必须再次
执行 `agent stop` 固化 `operation_started=false` 的静止 Checkpoint，之后才能裁决 unknown
副作用。该操作顺序已补入 Gate 3B 正式计划。

最终审阅发现机器 A 的 `control-runtime-local-r3` 与上述提交在
`agent_codex_adapter.py`、`agent_codex_evidence.py`、`loop_runtime.py` 三个控制文件上
不再字节一致。差异属于架构门禁要求的等价整理，但正式协议禁止事后用“行为等价”替代同一
`control_source_commit`。因此 SAG3B-02 收紧为
`machine-a-handoff-ready / formal-gate-nonconforming / machine-b-not-run`。

当前不能宣布 Gate 3B 通过，也不能把目标 clone 的 WIP 复制进控制分支。下一步：

1. 推送当前分支并让 PR CI 覆盖全部四个 Python 3.12 分片；
2. 合入主线后，从同一主线提交预注册新的 `SAG3B-03`；
3. 新 Case 的机器 A/B 必须分别从同一控制提交重建固定控制器；
4. 只有物理换机恢复并重新通过 Verification、Risk、Reviewer 和 Finish，才通过 Gate 3B。
