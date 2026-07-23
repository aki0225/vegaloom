# MA-2A 单 Slice 运行时桥接预注册

> 冻结日期：2026-07-23<br>
> Gate：`MA-2A`<br>
> 状态：`pre-registered / implementation not started`<br>
> 默认产品行为：不变<br>
> 真实 Planner / Provider：禁止

## 1. 冻结输入

- `baseline_commit`：`3f553e09328a1b52b76b07bd3bf89fe651a3fd6a`
- `origin/main`：`d6e110d7ea168b41a58b4a1fcfc81c41d628bcd4`
- 研究合同：
  `docs/experiments/multi-agent-coordination/RESEARCH-AND-EXPERIMENT-PLAN.md`
- 研究合同 SHA-256：
  `b9aa7d0e577b468aebc3b69e1eb3f5da70f8d6472d87d092bec61997aa6ed92a`
- 红灯测试：`tests/test_delegation_runtime_bridge.py`
- 红灯测试 SHA-256：
  `dce67491716897bb229bb7209fd02612f871ffce50dfdbcf1c89403fd6a15d2f`
- task-pack：不适用；本 Gate 不执行真实任务，不调用真实 Planner 或 Provider
- Worker backend：仅允许测试注入的 fake worker

研究合同哈希按 Git/LF 规范计算：以冻结提交中的 Git blob 内容为准，换行符为 LF，
编码为 UTF-8 且无 BOM，再计算 SHA-256。Windows 工作树中的 CRLF 不构成合同漂移。

本 Gate 从已封存的 MA-1 结果提交建立，`origin/main` 仅记录为冻结时的公开主线位置。
不得通过 rebase、合并主线或替换冻结文件来改变本 Gate 的输入；若实现所需的主线修复改变
本文件冻结的合同、运行时边界或验证变量，必须关闭当前 Gate，并建立新的预注册。

## 2. 唯一研究问题

对于一份预先冻结、仅含一个可写 slice 的 `PlanContract`，Vega 能否在任何 Worker 启动前，
从权威 task、policy、scope、verification 与 workspace snapshot 编译
`DelegationValidationContext`，完成确定性的 `DelegationReadiness` 校验，并且只在结果为
`budget_eligible` 时恰好调用一次注入式 fake worker，随后形成与现有执行、范围及验证证据
绑定的 `DelegationAttempt`，同时不改变任何现有成功语义？

本 Gate 只回答上述运行时桥接是否能够 fail-closed 地成立。它不评价 Planner 质量、模型能力、
真实 Worker 成功率、成本替代效果、多 Worker 收益、A2A 必要性或 MA-2 真实 Pilot 的可行性。

## 3. 允许范围

后续实现只允许建设一个默认关闭、仅供测试显式调用的单 Slice 运行时桥接：

1. 从当前运行的权威 task、项目 policy、读写 scope、verification 配置、HEAD 与 workspace
   fingerprint 编译 `DelegationValidationContext`，不得接受 Planner 或 Worker 自报的权威值。
2. 接受预先冻结的单 Slice `PlanContract`，并在 Worker 启动前复用 MA-1 的严格解析、
   内容哈希与 `DelegationReadiness` 校验。
3. 将 plan、readiness 与 attempt artifact 写入当前 run-owned 目录；所有目标路径和引用路径
   必须在解析、规范化并解析符号链接或重解析点后仍被该目录物理包含。
4. 只通过依赖注入调用 fake worker；只有 `budget_eligible` 可以调用一次，其他结果的调用次数
   必须为零。
5. 桥接自身只允许返回 `blocked` 或 `attempt_recorded`，不得返回 `success`、
   `ready_to_commit` 或其他产品终态；只有既有 Runtime 才能依据完整 scope、verification、
   review 与 Assurance 证据裁决产品状态。
6. 复用现有 Worker 执行控制，使进程事实仍只由既有 `execution.json` 表达。
7. 新增最小 `DelegationAttempt`，绑定 plan、slice、readiness、执行前后 workspace snapshot、
   scope gate、verification artifact 和 Worker tier 的内容哈希或相对引用。
8. 允许给隔离 reviewer 增加 plan、readiness 与 attempt 的受控事实摘要及内容哈希，但必须
   保持 reviewer 的独立只读会话边界。
9. 新增本 Gate 所需的单元测试、集成测试和追加式实验结论文档。

`DelegationAttempt` 只能引用现有 `execution.json`，不得复制其中的进程事实、改写其终态，
也不得建立第二套 execution artifact、PID、heartbeat、deadline 或恢复状态机。引用必须使用
run-owned 相对路径和 SHA-256；引用缺失、越界、内容哈希不匹配或指向非权威文件时均视为
证据无效。

## 4. 禁止范围

本 Gate 明确禁止：

- 调用真实 Planner、真实 Worker、任何真实 Provider、Provider adapter、原生子 Agent 或
  网络模型服务；
- 接受非注入式 Worker，或通过默认 runner 配置间接启动真实命令；
- 新增多 Slice 调度、并发 Worker、自动 replan、retry、模型升级、mailbox、A2A 或长期
  Memory；
- 修改默认 CLI 入口、命令参数语义或默认 `linear + single reviewer` 行为；
- 修改 Loop、Finish、Goal、Reviewer 或 Assurance 的成功、失败、人工介入和恢复语义；
- 将 `budget_eligible`、Worker 正常退出、reviewer approve、readiness 或 attempt 本身视为
  verification success 或 Assurance 充分证据；
- 让 reviewer 接收 Worker 的完整对话、隐藏推理、中间自述、Provider 私有 trace 或未受控
  运行输出；
- 扩大现有 sandbox、scope、verification、artifact integrity、evidence freshness 或敏感路径
  规则；
- 宣称 `allowed_read_paths` 已提供操作系统级读取隔离；本 Gate 只能验证合同和受控输入，
  不能夸大现有 sandbox 的安全能力；
- 自动 commit、push、release、部署、删除文件或写入长期 Memory。

## 5. Artifact 与会话边界

本 Gate 的所有新增 artifact 必须满足：

1. 只能写入调用方已经拥有的当前 run-owned 目录，不能由 plan、fake worker 或 reviewer
   自行选择根目录。
2. 写入前和读取引用前均执行 containment 检查；绝对路径、路径穿越、符号链接或重解析点
   逃逸、跨 run 引用和仓库外引用必须 fail-closed。
3. 使用 UTF-8、稳定序列化、仓库或 run-owned 相对引用和可复核 SHA-256；不得包含凭据、
   本机绝对路径、私人端点或完整会话。
4. `PlanContract`、`DelegationReadiness`、`DelegationAttempt`、`execution.json`、scope gate
   与 verification artifact 各自保持独立职责，后生成的 artifact 不得原地补写或覆盖前者。
5. reviewer 只能获得完成审查所需的事实摘要、相对引用和内容哈希；必须有自动化证据证明
   review pack 不包含 Worker 完整对话，并继续使用 read-only reviewer sandbox。

## 6. 红灯验收条件

实现前必须先增加针对运行时桥接的测试，并在冻结 baseline 上观察到因能力尚不存在而失败的
红灯。不得用修改断言、跳过、预期失败标记或只测数据模型来伪造红灯。后续实现只有让下列
条件全部转绿，才可形成正向 Gate 结论：

1. task、HEAD、workspace fingerprint、project policy、scope policy、read/write scope、
   verification command 或 shell kind 任一缺失、过期或错绑时，结果为 fail-closed，fake
   worker 调用次数为零。
2. `human_required`、`premium_required`、未知 route、未知字段或不可解析输入均不得启动
   Worker；桥接结果必须为 `blocked`。只有完整有效的 `budget_eligible` 可以启动。
3. 一次有效的 `budget_eligible` 运行只调用一个注入式 fake worker，调用次数严格为一；
   桥接结果只能是 `attempt_recorded`，测试必须证明没有真实 Provider、默认 runner 或网络
   调用路径被触发。
4. plan、readiness 或 attempt 的输出目标位于 run-owned 目录外，或通过绝对路径、`..`、
   符号链接、重解析点、跨 run 引用逃逸时，必须在 Worker 启动前拒绝。
5. `DelegationAttempt` 必须绑定唯一 `plan_id`、`slice_id`、readiness hash、Worker tier、
   执行前后 snapshot、scope gate、verification artifact 及现有 `execution.json` 引用。
6. `execution.json` 缺失、不是现有执行控制生成的权威文件、引用越界或 SHA-256 不匹配时，
   attempt 不得成为可信证据，运行不得进入自动成功。
7. fake worker 即使正常退出，只要出现 out-of-scope diff、snapshot 漂移、scope gate 失败、
   verification 失败、超时或证据不一致，最终状态仍必须是失败或 `needs_human`。
8. reviewer 输入可以包含受控委派事实，但不得包含 Worker 完整对话、隐藏推理或未受控输出；
   reviewer approve 仍不能覆盖 scope 或 deterministic verification failure。
9. Assurance 不得把 readiness、attempt、Worker 退出状态或 reviewer approve 当作
   verification evidence；既有 Assurance fail-closed 测试必须保持通过。
10. 相同冻结输入必须产生相同 readiness、attempt 结构、issue code 和规范化内容哈希；
    不得把时间、临时绝对路径或随机值混入可复核身份。
11. 不显式启用实验桥接时，现有 CLI、Loop、Finish、Goal、Reviewer、Assurance、执行控制
    和恢复路径的行为及测试结果必须与 baseline 一致。
12. 新增 artifact 不得泄漏凭据、本机路径、完整会话或其他 run 的内容，并通过仓库卫生门禁。

任何红灯无法在不放松现有产品合同的前提下转绿时，本 Gate 结论不得为 `accept`。

## 7. Fail-closed 停止线

出现以下任一情况时立即停止实现，不扩大范围，不启动真实 Provider：

- 需要真实 Planner、真实 Worker 或 Provider 返回才能证明桥接成立；
- 需要让 plan、Worker 或 reviewer 自报 task、policy、scope、snapshot、route 或验证事实；
- readiness 失败后仍会构造 Worker 命令、分配外部执行或产生可被误认作成功的 attempt；
- 无法在启动 Worker 前完成 artifact containment、snapshot、policy、scope 和 shell kind
  校验；
- 需要复制、改写或替代现有 `execution.json` 才能建立 Attempt；
- 无法证明 attempt 引用与真实 execution、scope gate、verification artifact 属于同一 run；
- 需要向 reviewer 暴露 Worker 完整对话、隐藏推理或 Provider 私有 trace；
- 需要把 readiness、attempt、Worker 退出或 reviewer approve 提升为成功证据；
- 需要修改默认 CLI，或放松 Loop、Finish、Goal、Reviewer、Assurance、scope、
  verification、freshness、敏感路径或仓库卫生语义；
- 测试只能依赖真实网络、真实 Provider、非确定性模型输出或超过 60 秒的等待；
- 发现单 Slice 无法隔离变量，必须同时实现多 Slice、重试、升级或多 Worker 才能工作。

触发停止线后，合法结论只能是 `reject` 或 `inconclusive`。不得把实现未完成记为验证成功，
也不得以人工批准覆盖缺失或冲突的确定性证据。

## 8. 验证命令

后续实现必须在单次 60 秒上限内分组执行测试；超时、卡死、未收集或进程被终止均不视为
通过。最终 Gate 至少执行：

```powershell
python -m compileall src scripts/check_repository_hygiene.py
python -m pytest tests/test_delegation_contract.py
python -m pytest tests/test_delegation_runtime_bridge.py
python -m pytest tests/test_execution_control_safety.py tests/test_context_boundaries.py
python -m pytest tests/test_assurance_verification_semantics.py
python scripts/check_repository_hygiene.py --base-ref origin/main
python -m pytest
ruff check src tests scripts/check_repository_hygiene.py
git diff --check
```

若最终测试文件名与预注册名称不同，必须在首次实现提交前以追加说明冻结替代路径，且不得减少
上述语义覆盖。测试证据必须记录 collected、passed、skipped、failed 和超时情况；只有明确
通过的节点可以计入正向证据。

## 9. Gate 结论口径

本 Gate 只允许以下结论：

- `accept`：全部红灯先在 baseline 上成立，最小桥接实现后全部转绿；只使用注入式 fake
  worker；所有 artifact 均受 run-owned containment 约束；Attempt 只引用现有
  `execution.json`；Reviewer 隔离和既有成功语义均未改变；专项、回归和静态门禁全部通过。
- `reject`：实现必须违反禁止范围或 fail-closed 停止线，或者出现任何可接受越界写入、
  stale evidence 后继续、无验证成功、完整对话泄漏或第二套 execution 事实源。
- `inconclusive`：测试环境、现有接口或可观测证据不足以在冻结变量下判断，且不能通过有界、
  确定性的测试消除不确定性。

`accept` 只表示“单 Slice 运行时桥接具备进入下一次独立预注册的条件”，不表示 MA-2 真实
Planner × Worker Pilot 已获授权，不表示可以调用真实 Provider，也不改变默认产品行为。
如要进入真实 Pilot，必须另行冻结 task-pack、ground truth、Provider / model manifest、
独立 worktree、随机顺序、成本与 token 采集、停止线及新的 Gate 决策。

## 10. 冻结时红灯观察

在未新增任何生产实现的冻结 baseline 上执行：

```powershell
python -m pytest tests/test_delegation_runtime_bridge.py -q --tb=short
```

原始计数为：

```text
11 collected
3 passed
8 failed
0 skipped
```

失败分布：

- 7 个节点因 `vega.delegation_runtime` 尚不存在而失败，分别锁定非准入 route、stale
  snapshot、缺失 shell kind、单次 fake worker 调用、run-owned containment 与 Attempt
  hash 绑定；
- 1 个节点因 reviewer context 尚未包含受控 `delegation_summary` 而失败；
- 未使用 `xfail`、skip、真实 Provider、网络调用或默认 runner。

已通过的 3 个节点确认 baseline 仍保持：

- out-of-scope diff 不能进入成功；
- verification failure 不能被 reviewer 或 Worker 正常退出覆盖；
- readiness artifact 不能替代 Assurance 的结构化 verification evidence。

同一工作树上的 Ruff、compileall、仓库卫生检查和 `git diff --check` 均通过。该结果只证明
预注册红灯已真实出现，不表示 MA-2A 实现完成。
