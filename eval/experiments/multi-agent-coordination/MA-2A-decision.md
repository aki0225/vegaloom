# MA-2A 单 Slice 运行时桥接 Gate 决策

> 决策日期：2026-07-24<br>
> Gate：`MA-2A`<br>
> 分支：`experiment/ma2-runtime-bridge`<br>
> 冻结基线：`3f553e09328a1b52b76b07bd3bf89fe651a3fd6a`<br>
> 被复审实现：`dcef05ffe4cb5649ebcbc81c2f8b8368b94992cf`<br>
> 决策：`inconclusive`<br>
> 后续授权：`MA-2B not authorized`

## 一、决策摘要

`MA-2A` 不能接受。

现有实现已经证明了一部分有价值的运行时能力：

- 单 Slice 桥接默认未接入 CLI、Loop 或产品成功路径；
- 只调用显式注入的 fake Worker、scope probe 与 verification probe；
- 桥接返回值只有 `blocked` 或 `attempt_recorded`；
- `DelegationAttempt` 引用现有 `execution.json`，没有建立第二套进程状态机；
- run-owned 路径、引用哈希、普通文件、链接、junction、reparse point 与 hardlink 检查较完整；
- 既有 scope、verification、reviewer 与 Assurance 成功语义没有被放松；
- 实现提交的远端 CI 为 `10/10 jobs success`。

但是，独立复审确认当前实现没有回答预注册中的唯一研究问题。它只能证明：

> 调用方注入的 Plan 与调用方注入的 Validation Context 相互一致时，桥接可以启动 fake
> Worker 并收集若干 artifact。

它尚不能证明：

> Plan 与 Validation Context 已经绑定当前真实 task、policy、scope、verification 和
> workspace，且 Worker 无法在执行期间改写控制面事实。

因此不能把现有全绿测试和 CI 解释为 `MA-2A accept`。

## 二、独立复审发现

### Blocker 1：未绑定真实运行时 workspace snapshot

`evaluate_delegation_payload()` 比较的是：

```text
PlanContract.baseline
vs
调用方注入的 DelegationValidationContext.baseline
```

Worker 启动前虽然调用了 `capture_review_workspace()`，但只检查工作区是否干净、快照是否完整，
没有把真实的 `head_sha` 和 `fingerprint` 与计划及 Validation Context 比较。

冻结成功测试使用：

```text
head_sha = aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
workspace_fingerprint = bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
```

测试仓库的真实 Git 身份不可能与以上占位值一致，但结果仍为：

```text
attempt_recorded
worker_calls = 1
```

这与预注册第 6 节第 1 条“HEAD 或 workspace fingerprint 过期、缺失或错绑时 Worker
调用次数为零”冲突。

### Blocker 2：没有从权威运行时事实编译 Validation Context

`DelegationRuntimeBridge` 直接接收完整的 `DelegationValidationContext`。当前没有代码从实时：

- task artifact；
- 项目策略文件；
- scope 配置；
- verification 配置；
- HEAD 与 workspace fingerprint；
- 当前可用 input artifacts；

重新编译并复核权威 Context。

因此 task、policy、scope、verification 和 artifact identity 仍可由调用方共同伪造。当前
实现验证的是两个对象之间的一致性，而不是对象与运行时事实之间的一致性。

### Blocker 3：scope 与 verification artifact 只有状态，没有输入绑定

当前注入式 probe 只需写入：

```json
{
  "schema_version": 1,
  "status": "passed"
}
```

桥接和 Attempt validator 不要求其绑定：

- `plan_id`；
- `slice_id`；
- 执行前后 snapshot；
- verification command；
- shell kind；
- scope policy。

独立探针确认以上未绑定 artifact 仍会产生 `attempt_recorded`。这不能证明 probe 的
`passed` 属于当前 Slice、当前命令和当前 workspace。

### Blocker 4：Worker 可以改写 plan 与 readiness 控制面

plan 和 readiness 在 Worker 启动前写入，但桥接没有保存并复核 pre-Worker hash。
Attempt 的引用哈希在 Worker 完成后才计算。

独立探针让 fake Worker 在执行期间同时改写：

```text
delegation-plan.json
delegation-readiness.json
```

桥接仍返回：

```text
attempt_recorded
issue_codes = []
```

`validate_delegation_attempt()` 也没有比较
`readiness.plan_sha256` 与实际 PlanContract 的规范化哈希。因此 Attempt 会把 Worker
改写后的控制面内容当作权威事实。

### High 1：staged 新文件可以绕过 `max_new_files`

当前预算只计算：

```text
after.untracked_files - before.untracked_files
```

fake Worker 新建文件后执行 `git add`，文件不再属于 untracked。独立探针在
`max_new_files = 0` 时创建并 stage 一个新文件，桥接仍返回 `attempt_recorded`。

### High 2：Reviewer 接受残缺 delegation summary

`_controlled_delegation_summary()` 逐字段过滤，而不是校验完整摘要。仅提供合法
`plan_id` 时，review context 仍会暴露：

```json
{
  "plan_id": "PLAN-MA2A"
}
```

残缺摘要可能让 reviewer 误以为已经存在完整、可信的委派证据链。五个受控字段应全部合法
后才允许输出，否则返回空对象。

### High 3：Worker prompt 没有冻结身份

`run(prompt=...)` 接受任意字符串，Worker 实际只收到该字符串。prompt 没有由
PlanContract 确定性编译，也没有形成受哈希绑定的 artifact 或进入
`DelegationAttempt`。

该问题不会单独改变本 Gate 的 `inconclusive` 结论，但在未来 A/B/C 对照实验前必须关闭，
否则无法证明不同 Worker 条件消费了同一冻结任务输入。

## 三、可复现证据

新增独立复审探针：

```text
eval/experiments/multi-agent-coordination/ma2a_independent_review_probe.py
```

执行：

```powershell
python eval/experiments/multi-agent-coordination/ma2a_independent_review_probe.py
```

2026-07-24 的实际结果为：

```text
result = current_gate_gaps_reproduced

live_workspace_snapshot_not_bound
  observed_status = attempt_recorded
  worker_calls = 1

worker_control_plane_tamper_accepted
  observed_status = attempt_recorded
  issue_codes = []

staged_new_file_bypasses_max_new_files
  configured_max_new_files = 0
  observed_status = attempt_recorded

partial_delegation_summary_is_exposed
  controlled_summary = {"plan_id": "PLAN-MA2A"}

status_only_verification_artifact_is_accepted
  verification_artifact = {"schema_version": 1, "status": "passed"}
  observed_status = attempt_recorded
```

探针只使用本地临时 Git fixture、现有冻结测试辅助代码和注入式 fake Worker，没有调用真实
Planner、Worker、Provider、默认 Runner 或网络服务。

## 四、为什么不能在当前 Gate 内改成 `accept`

当前问题不是简单补几行断言即可关闭。

冻结成功测试明确要求一份携带虚构 `head_sha` 和 `workspace_fingerprint`、且没有权威 task、
policy、scope 与 input artifact 来源的计划可以启动 Worker。若按预注册合同加入真实
runtime binding，该冻结成功测试必须转红。

要让严格实现与测试同时成立，至少需要：

1. 改写冻结测试 fixture，使计划和 Context 从真实 fixture 仓库及 run artifacts 编译；
2. 新增明确的 Context compiler 输入合同；
3. 定义 scope 与 verification 的绑定 artifact schema；
4. 改变当前 Attempt schema 或引用闭包；
5. 重新冻结红灯、变量和验收条件。

以上都会改变当前 Gate 的冻结测试和运行时输入，违反
`MA-2A-pre-registration.md` 的冻结边界。不能通过事后修改测试把原实现包装成通过。

因此本 Gate 选择预注册允许的 `inconclusive`，而不是：

- `accept`：因为关键红灯条件没有被真实覆盖；
- `reject`：因为问题可通过新的、独立预注册进行有界修复，不需要放松产品成功语义，也不
  需要真实 Provider、多 Worker 或 A2A。

## 五、后续修复 Gate

下一步不是 `MA-2B`，而是建立独立的 `MA-2A-R` 事实绑定修复 Gate。新 Gate 至少应冻结：

1. 由真实 fixture repo 与 run-owned task/input artifacts 编译
   `DelegationValidationContext`；
2. Worker 启动前比较真实 HEAD、workspace fingerprint、project policy、scope policy、
   verification command 和 shell kind；
3. 保存 plan、readiness 与 prompt 的 pre-Worker hash，Worker 返回后先复核再继续；
4. scope 与 verification artifact 必须绑定 plan、slice、before/after snapshot、命令、
   shell kind 与 policy hash；
5. `DelegationAttempt` validator 必须交叉验证 readiness 的 `plan_sha256` 和
   `context_sha256`；
6. 新文件预算同时覆盖 untracked 与 staged/unstaged 的新增 tracked 文件；
7. delegation summary 五个字段必须全有或全无；
8. 仍然只使用注入式 fake Worker，不接入真实 Provider、默认 CLI、多 Worker、A2A、
   retry 或自动 replan。

只有 `MA-2A-R accept` 后，才可以讨论是否预注册 `MA-2B` 的真实
Planner × Worker 对照实验。

## 六、最终裁决

`MA-2A = inconclusive`。

远端 CI 的全绿结果仍然是真实的，但它证明的是现有测试集合通过，不足以证明预注册的实时
事实绑定命题成立。当前分支不得宣称已经获得 LangGraph、多 Agent 或低成本 Worker 的质量、
成本、速度或恢复收益，也不得启动真实 Provider Pilot。
