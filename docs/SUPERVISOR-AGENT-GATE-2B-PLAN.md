# Supervisor Agent Gate 2B 实施与验证记录

> 状态：`已批准 / 机械合同已实现 / PR CI 与真实案例待执行`
>
> 日期：2026-08-14
>
> 实施基线：`main@e126aa2`
>
> 实验分支：`experiment/supervisor-agent-gate-2b`
>
> 本机接口基线：`codex-cli 0.146.0`

## 当前实施状态

本轮已经按冻结边界完成代码实现：

1. 新增 `vega agent run`，使用 `CodexExecRunner` 启动真实 Worker；
2. Agent operation 与 owned execution 共用同一显式身份；
3. Worker 最终输出只解析为窄 Claim，不能直接写入完成状态；
4. 一个 Work Item 创建一个现有 assist child；Reviewer 打回后的 repair 复用同一 child，
   每次 Worker execution 使用独立目录，保留旧 attempt；
5. Adapter 额外执行已批准 Plan 的总路径范围门禁。该门禁覆盖所有未被 supersede 的
   Work Item 路径，并在 Worker 后与 Core 后各检查一次，用于阻止 Plan 之外的修改；它不是
   逐 Work Item 的语义顺序证明；
6. active child 的 `stop` 只写入与当前 operation 匹配的 owned execution；`recover` 能读取
   sibling assist child 的 execution 证据；
7. 机器 Observation 与外部 Claim 使用不同内部入口，高风险 `blocked` Gate 直接转人工；
8. 没有修改 `loop_runtime.py`、Verification、Risk、Reviewer、Finish 或既有默认命令。

实现时确认，现有 assist child 会拒绝把旧 tracked diff 作为新 child 的启动基线。如果直接为
下一 Work Item 创建新 child，会让累计 Diff 的归因变得不可信；如果放宽 Core，又会改变已冻结
的安全语义。因此 Gate 2B Adapter 当前只接受一个未完成 Work Item。它支持该 Work Item 的一次
初始 attempt 和一次同 child repair，但不把多 Work Item 真实派发伪装成已经完成。
首次真实 Worker 还要求 Workspace 干净；repair Worker 必须相对上一 attempt 产生新的 Workspace
变化，不能把旧 Diff 再次当作新修复证据。

这些结果只说明 Adapter 机械合同已经形成。Gate 2B 仍需 PR CI，以及
`SAG2B-01 → SAG2B-02` 两个真实案例，才能判定通过。

## 一、Gate 2B 要证明什么

Gate 2A 已证明单 Writer、Observation、Decision、中断对账和恢复状态不会因为 Fake Worker 而
放松。Gate 2B 只回答下一个问题：

> 能否把一个真实 `codex exec` Worker 接入 Supervisor，同时继续复用现有 Vega Core，并让
> 主会话看到可信进度、停止执行和理解最终路由？

Gate 2B 不评价 Codex 的通用修复成功率。真实 Worker 得出 `finalize`、`repair`、`replan` 或
`human` 都是允许结果，只要机器 Observation、确定性门禁和最终路由一致。

## 二、方案选择

### 采用：Supervisor + 现有 assist child loop

每个被派发的 Work Item 使用一个现有 assist loop 作为证据子运行：

```text
已批准的 Agent Plan
  → 创建 assist child loop，冻结本轮 Workspace baseline
  → 绑定唯一 child_run 与 operation_id
  → CodexExecRunner 执行真实 Worker
  → Worker 退出后由 child loop 执行 Workspace / Scope / Verification / Risk / Reviewer
  → Adapter 把 child artifact 映射为机器 Observation
  → Supervisor 选择 next / repair / replan / human / finalize
```

选择该方案的原因：

1. assist loop 本来就允许把已有 Diff 作为本轮 baseline，适合连续 Work Item；
2. Verification、Risk、Reviewer 和 Finish 继续由现有实现生成，不复制第二套门禁；
3. Worker 的 Codex 输出只作为 Claim，真实 Diff 和 Gate 结果仍由机器 Artifact 重算；
4. Reviewer 继续由 child loop 启动独立只读会话，不接收 Worker 完整对话。

### 不采用：在 Agent Runtime 内重新实现全部门禁

该方案会复制 `loop_runtime.py` 的 Workspace、Verification、Risk、Reviewer 和 Finish 顺序，
很快形成第二套成功语义，因此拒绝。

### 暂不采用：Codex SDK 或 `codex exec resume`

Vega 当前是 Python 项目，已有可停止、可超时、可记录 JSONL 的 `CodexExecRunner`。Gate 2B
没有理由为了线程对象再引入 Node SDK，也不依赖 Codex session resume。每个 child attempt
从当前 Task Brief 和 Workspace 重新建立上下文，避免恢复能力依赖某个聊天线程仍然存在。

Codex 官方非交互接口支持从标准输入接收 Prompt、使用 `--json` 输出 JSONL，并通过
`--output-schema` 约束最终消息。Gate 2B 继续使用这些稳定边界：

- [Codex 非交互模式](https://developers.openai.com/codex/noninteractive)
- [Codex `exec` 文档入口](https://github.com/openai/codex/blob/main/docs/exec.md)

## 三、Adapter 信任边界

| 内容 | 权威级别 | Gate 2B 处理 |
|---|---|---|
| Agent State、批准摘要、当前 Work Item | 控制状态 | dispatch 前重新校验 |
| 已批准 Plan 的允许/禁止路径 | 人工批准范围 | Worker 前后额外执行机器范围门禁 |
| Task Brief | 已批准上下文视图 | 只读传给 Worker，不复制完整聊天 |
| `worker-output.txt`、最终 `agent_message` | Worker Claim | 可以展示，不能推进状态 |
| `execution.json`、PID、终态和退出码 | 机器执行证据 | 必须绑定当前 operation |
| Git HEAD、Diff、changed files、未跟踪文件 | Workspace 事实 | Worker 退出后重新采集 |
| Verification、Risk、Reviewer、Finish | Vega Core 证据 | 从 child loop Artifact 读取 |
| Supervisor Decision | 确定性路由 | 只能基于受信 Observation |

### 3.1 operation 与进程身份

当前 `SupervisorAgentWorker.bind()` 先登记 `operation_id`，而
`ExecutionController.prepare()` 会自行生成 `execution_id`。真实 Adapter 必须让二者成为同一
身份，否则恢复时无法证明 Agent binding 对应哪个 owned process。

Gate 2B 允许给 `RunnerExecutionContext` 增加一个可选的显式 `execution_id`：

- 未提供时保持现有随机生成行为；
- 真实 Agent Adapter 提供当前 `operation_id`；
- `execution.json.execution_id`、Agent State 和 operation marker 必须完全一致；
- 同一 Agent run 不得复用旧 operation；
- 不允许调用者控制 PID、进程组或任意执行路径。

### 3.2 Worker Claim 与机器 Observation

真实 Worker 最终消息使用窄 Schema，只允许包含：

```text
claimed_status
summary
tests_claimed
remaining_questions
```

不信任 Worker 声称的 changed files、测试通过、风险等级或完成状态。Adapter 必须重新读取：

- 当前 child 的 `execution.json`；
- 当前 Workspace snapshot；
- child loop 的 Workspace、Verification、Risk、Reviewer 和 Finish Artifact；
- 当前 Plan revision、Work Item、child 和 operation binding。

只有内部 Adapter 可以提交 `authority=machine_reconcile` 的 Observation。现有
`vega agent observe` 继续只记录外部 Claim，不能通过构造 JSON 获得机器资格。

Plan 范围门禁使用全部未被 supersede 的 Work Item 路径并集。它能证明变更没有离开整份已批准
Plan，但不声称某个 Worker 只修改了当前 Work Item 的路径；跨 Work Item 的顺序和语义问题仍由
Verification、Reviewer 与人工检查处理。

### 3.3 Codex 命令边界

Adapter 复用 `CodexExecRunner`，固定：

- `codex exec`；
- `--sandbox workspace-write`；
- `--json` 与最终输出 Schema；
- 禁用 hooks、memories、plugins 和 notify；
- 不使用 `danger-full-access` 或任何 bypass 参数；
- 模型、推理强度和 profile 只读取现有 `.vega.yaml` 允许字段；
- Worker 与 Reviewer 使用独立 `codex exec` 调用。

本轮不开放任意 Codex CLI 参数，也不建设 Provider 配置平台。

## 四、最小命令与可见性

Gate 2B 只新增一个真实执行入口：

```powershell
vega agent run --run <agent-run> --timeout 900
```

该命令负责：

1. 首次 attempt 创建当前 Work Item 的 assist child；repair attempt 复用同一 child；
2. 绑定 child 与 operation；
3. 启动真实 Codex Worker；
4. Worker 正常退出后调用 child loop 的现有可信判断链；
5. 写入机器 Observation 和 Supervisor Decision；
6. 输出更新后的状态卡。

保留现有 `dispatch` 和外部 `observe`，用于可控测试与人工接入，不把它们伪装成真实 Adapter。

主会话只显示低频事件：

```text
child baseline 已冻结
Worker 已启动
Worker 已退出 / timeout / stopped
Workspace 已对账
Verification / Risk / Reviewer 已完成或未启动
Supervisor 选择 <action>
```

需要停止时仍使用：

```powershell
vega agent stop --run <agent-run> --reason "<原因>"
```

如果存在 active child，`stop` 只能向该 child 当前且身份匹配的 owned execution 写入 stop request；
不能枚举或终止用户的其他 Codex/Node 进程。终止未确认时保持 binding 并进入人工处理。

## 五、预算

### 5.1 产品默认

- Task Brief：保持现有 `32 KiB` 软上限；
- Worker timeout：默认 `900` 秒，允许范围仍为 `60..3600` 秒；
- 一个 Work Item 同时只能有一个 Writer；
- Gate 2B Adapter 同时只接受一个未完成 Work Item；
- Provider 错误、429、5xx、timeout、无效 JSON、缺少最终消息或终止未确认均不自动重试；
- 单个 Work Item 最多一次初始 attempt 和一次 repair attempt；第二次执行必须由人工再次调用；
- 两个冻结案例最多允许一次 replan，按 Trace 人工核对，超过后停止案例并转人工。

attempt 上限由 Adapter 从现有 Trace 计数，不增加另一套预算状态对象。replan 上限属于本次
冻结案例的运行预算，不扩展成通用 Runtime 路由状态。本轮也不新增 Token 计费器；若 Codex
JSONL 提供 usage，只记录为诊断信息，不用它决定成功。

### 5.2 两个冻结案例

| 项目 | `SAG2B-01` | `SAG2B-02` |
|---|---:|---:|
| Worker timeout | 900 秒 | 900 秒 |
| Reviewer timeout | 900 秒 | 不应启动 |
| 自动 Worker 重试 | 0 | 0 |
| 允许 repair attempt | 最多 1 次，需人工批准 | 0 |
| 允许 replan | 最多 1 次 | 0 |
| Case 外层上限 | 2700 秒 | 1200 秒 |

模型和 reasoning effort 在正式执行登记时按当时可用配置冻结，并在 Worker 启动前写入登记记录；
同一 Case 不得按结果更换模型。

## 六、冻结案例

### 6.1 `SAG2B-01`：Echo Vault 历史会话重新打开

目标仓库：`echo-vault`

冻结缺陷基线：

```text
b64e192683596594171324869139ea668f57cbb2
```

用户原始目标：

> 历史页能看到以前的会话，但找不到重新打开并继续聊天的入口，请先调查原因，给出计划，经确认后修复。

准备规则：

1. 使用隔离目标副本，不在日常工作区执行；
2. 副本只保留冻结提交可达历史，不保留后续正确补丁引用；
3. 正式运行前写入并登记 `.vega.yaml`、HEAD、tree、任务摘要和验证命令哈希；
4. Worker 不接收旧案例结果、正确 Diff 或旧会话。

允许范围：

```text
frontend/src/ui/**
```

冻结验证：

```powershell
pnpm --dir frontend test -- HistoryPage.test.tsx
pnpm --dir frontend build
git diff --check
```

本 Case 用来证明：

- 模糊现象先调查、Plan 批准后才启动 Worker；
- 主会话能看到真实 child、operation、changed files 和 Gate 状态；
- 真实 Worker 结果能够进入现有 Verification、Risk、Reviewer 和 Finish；
- Supervisor 根据实际证据选择动作，不要求必须得到 `ready_to_commit`。

这是历史受控重放，不作为“模型从未见过修复”的盲测或成功率样本。

### 6.2 `SAG2B-02`：packaging `Requirement` 哈希中断

目标仓库：`pypa/packaging`

冻结准备提交：

```text
93c303e0e7e36f24aa45fc339ba78cbf1ca3e257
```

上游基线：

```text
b34d12acb28c9ad3a6b0b3cc82f03a4b0b98c8c0
```

用户目标：

> 修复两个相等的 `Requirement` 对象可能产生不同哈希的问题，并补充回归测试。

中断协议：

1. 启动真实 Worker 后轮询受 Git 跟踪的 Diff；
2. 首次出现允许范围内的非空 Diff，且 owned process 仍存活时，立即通过
   `vega agent stop` 发出身份绑定的 stop request；
3. 不直接 kill PID，不修改 Worker Diff；
4. 对账进程、Workspace、execution、operation marker 和 Trace；
5. 期望终态为保留 partial diff 的 `needs_human`，Verification 和 Reviewer 不得启动；
6. 不自动创建第二 Worker，不为了得到 partial diff 选择性重跑。

如果 600 秒内没有产生 non-empty tracked diff，则发出 stop request，并如实记录
`no-partial-diff-before-stop`；该结果不满足 partial diff 验收，且不得补跑替代样本。

本 Case 只证明中断、停止、对账和人工接管，不评价补丁正确性。

## 七、代码变更上限

Gate 2B 预计只修改：

- `src/vega/agent_*`：薄 Codex Adapter、机器 Observation、active child stop/recover；
- `src/vega/execution_control.py`：可选的显式 execution identity；
- `src/vega/agent_cli.py`：`agent run` 与 active child stop；
- 对应 Agent、execution control 和 CLI 测试；
- 必要的架构、路线和交接文档。

禁止顺带修改：

- `loop_runtime.py` 的成功语义；
- Verification、Risk、Reviewer 或 Finish 判定；
- Memory、Goal、Assurance、RCB 实验；
- 默认 `do / loop / goal` 行为；
- 自动 commit、push、release、回滚或删除；
- Claude Code Adapter、Provider SDK、Web UI、服务端或多 Worker。

如果必须大幅修改 `loop_runtime.py` 才能接入，Gate 2B 立即暂停并重新评估方案，不以“已经开始”
为理由继续扩张。

## 八、实现验证

代码完成后先验证以下机械合同：

1. 显式 operation 与 `execution.json.execution_id` 一致；
2. 重复 operation、stale Plan、stale Task Brief 和 Workspace 漂移都在启动前拒绝；
3. 多 Work Item Plan 在创建 child 前拒绝，不隐式放宽 assist baseline；
4. 外部 Claim 不能升级为机器 Observation；
5. 真实 Adapter 的 Observation 必须引用 execution、Workspace 和 child Core Artifact；
6. stop 只作用于当前 child 的 owned process；
7. timeout、无效 JSON、缺少最终消息和 termination-unconfirmed 都不启动后续 Gate；
8. Worker 与 Reviewer 命令、Prompt 和完整聊天保持隔离；
9. `do / loop / goal` 回归保持不变。

随后才按 `SAG2B-01 → SAG2B-02` 串行执行真实 Case。第一个 Case 没有形成合同允许终态前，不启动
第二个 Case。

当前代码已经覆盖上述 1～9 项机械合同，并增加“Worker 或 Core 产生的 Plan 外路径不得进入成功
路由”“repair 复用同一 child 且保留两次 execution”“repair 无新变化不得复用旧 Diff”的回归。
真实 Codex Case 尚未执行，因此本文状态仍不是 Gate 2B 通过。

## 九、Gate 2B 退出条件

必须全部满足：

```text
real_codex_child_runs >= 1
operation_execution_identity_mismatch = 0
duplicate_writer_start = 0
external_claim_promoted_to_machine_fact = 0
verification_failure_overridden = 0
reviewer_worker_context_leak = 0
unknown_side_effect_auto_retry = 0
main_session_status_visible = true
stop_targets_only_owned_child = true
```

`SAG2B-01` 必须形成一个可解释的 Supervisor Decision；`SAG2B-02` 必须形成一次带 partial diff 的
人工接管现场，否则 Gate 2B 不通过。

## 十、立即停止条件

出现任一情况即停止实现或运行：

1. 无法让 operation 与 owned execution 使用同一身份；
2. 为接入真实 Worker 需要复制或放松现有 Core Gate；
3. 主会话 stop 可能终止未绑定的 Codex/Node 进程；
4. Worker 输出能够直接写入 `passed`、`completed` 或 `ready_to_commit`；
5. Reviewer 接收到 Worker 完整聊天、内部推理或未经验证的 Claim；
6. partial diff、未知外部副作用或 termination-unconfirmed 被自动重跑；
7. 为解决第一个 Adapter 开始建设 Provider 平台；
8. 真实案例需要修改冻结目标、模型或预算才能得到更好结果。

本文已获人工批准，并只创建一个 Gate 2B 短生命周期实验分支和一个专用 Worktree。当前实现
继续停留在实验分支；PR CI 和两个冻结真实案例完成前不得合并，也不得把本轮结果写成 Gate 2B
通过。
