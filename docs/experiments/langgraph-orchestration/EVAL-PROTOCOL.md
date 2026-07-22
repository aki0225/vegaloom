# Vega LangGraph Gate 0 可证伪评测协议

> 文档状态：`frozen-gate-0-eval-protocol`
>
> 日期：2026-07-15
>
> 适用分支：`experiment/langgraph-comparison`
>
> 基线提交：`private-experiment-base-redacted`
>
> 实验性质：可证伪架构实验，不是主线产品承诺

本协议在 Gate 0 冻结，用来约束后续 LangGraph 编排实验的评测口径。任何实现结果出来后，不得通过修改分组、指标、crash window 期望或成功语义来提高通过率。

---

## 1. 评测目标

本实验只回答一个核心问题：

```text
LangGraph 作为 Vega 的可选编排引擎，是否在不破坏现有线性 Runtime 语义的前提下，
提供了可证明的 checkpoint、恢复、HITL、reviewer fan-out 或 handoff 收益。
```

Gate 0 的职责不是证明 LangGraph 一定值得引入，而是冻结一个允许得出 `accept / partial / reject` 的评测协议。

### 1.1 优先级

第一优先级是 Gate 1 和 Gate 2：

1. Gate 1：最小 Engine / Handler 边界是否成立。
2. Gate 2：顺序 LangGraph 图是否与 Linear Runtime 保持 semantic parity。

Gate 3 / Gate 4 的 checkpoint、恢复和 HITL 是核心编排的增量能力证据；Gate 5 / Gate 6
的 parallel reviewers 与 Goal handoff 是可独立取舍的扩展证据。任何后续证据都不能掩盖
Gate 1/2 的失败；Gate 1/2 未通过时，不得用后续能力包装成整体成功。

### 1.2 非目标

以下内容不进入 Gate 0 成功条件：

- 证明 LangGraph 比 Linear Runtime 更快。
- 证明真实模型输出质量优于现有流程。
- 将 LangGraph 设为默认引擎。
- 引入 FastAPI、SSE、前端或远程 worker 平台。
- 自动写入 accepted memory。
- 为了抽象美观重写大部分现有 Runtime。

---

## 2. A-F 分组

实验按能力分组，但分组不是同等优先级。A/B 是核心等价性评测，C/D/E/F 是在 A/B 成立后的增量能力评测。

| 分组 | 名称 | 对应 Gate | 评测目的 | 优先级 |
|---|---|---:|---|---:|
| A | Linear Runtime | Gate 1/2 baseline | 冻结现有默认行为和成功语义 | P0 |
| B | LangGraph 顺序等价 | Gate 2 | 验证最小图执行能否复用业务 handler 并保持 semantic parity | P0 |
| C | LangGraph + checkpoint / recovery | Gate 3 | 验证 crash 后能否安全恢复或安全停止 | P1 |
| D | LangGraph + HITL | Gate 4 | 验证 interrupt、decision ledger 和 resume 绑定关系 | P1 |
| E | LangGraph + parallel reviewers | Gate 5 | 验证隔离 reviewer、稳定 reducer 和聚合确定性 | P2 |
| F | LangGraph + Goal handoff | Gate 6 | 验证跨 session handoff 是否可独立复用 | P2 |

A/B 必须使用 fresh baseline，并共享：

- task contract；
- fixture repo commit；
- policy snapshot；
- deterministic runner；
- verification commands；
- 成功语义；
- 必需业务 artifact schema。

C/D/E/F 可以增加 engine-specific artifacts，但不得改变 A/B 的业务成功定义。

---

## 3. Semantic Parity 字段

Semantic parity 比较业务语义，不比较非决定性字节。以下字段是 Gate 2 的预注册比较分母。

| 字段 | 比较口径 | 必须一致 |
|---|---|---:|
| `terminal_status` | 最终业务状态，例如 `success`、`failed`、`needs_human`；`terminal_recovery` 只属于恢复结果分类 | 是 |
| `success_semantics` | verification failed 不能被标记为 success，高风险未批准不能 success | 是 |
| `verification_status` | 验证整体状态、失败命令数量、失败原因类别 | 是 |
| `risk_level` | 风险等级和是否阻断继续 | 是 |
| `recommendation` | finish / handoff / reject / needs_human 的结论类别 | 是 |
| `workspace_diff_hash` | 排除允许的时间戳、路径前缀和 engine metadata 后的工作区 diff hash | 是 |
| `required_artifact_types` | 必需业务 artifact 类型集合 | 是 |
| `required_artifact_schema` | 必需业务 artifact schema 和关键字段 | 是 |
| `human_required` | 是否进入人工确认 | 是 |
| `human_reason` | 进入人工的原因类别 | 是 |
| `external_effect_count` | worker 启动、写入、provider 动作等外部副作用次数 | 是 |
| `duplicate_effect_count` | 已产生副作用后重复启动或重复写入次数 | 必须为 0 |
| `evidence_freshness` | reviewer / final decision 绑定的 evidence snapshot 是否为当前 run | 是 |

以下字段不进入 parity 分母，但必须记录：

- run ID；
- 时间戳；
- trace event 顺序中的非语义差异；
- LangGraph checkpoint 文件字节；
- SQLite 内部页布局；
- engine-specific debug artifacts；
- graph schema version；
- node 名称和图边可视化文件。

如果某个字段无法比较，结果不能自动按通过处理，必须标记为 `parity_unresolved` 并进入人工复审。

---

## 4. Crash Window 预注册

crash window 的期望必须在故障注入前固定。结果出来后不得把失败窗口重新分类为成功。

### 4.1 期望分类

| 分类 | 含义 | 是否允许自动继续 |
|---|---|---:|
| `safe_execute` | 尚无外部副作用，可以重新执行当前步骤 | 是 |
| `safe_reuse_step_result` | step result 已可验证，允许复用而不是重启 worker | 是 |
| `safe_repair_step_result` | terminal execution 已验证且 workspace 一致，允许补写缺失的 step result | 是 |
| `safe_recompute_read_only` | 只允许重算只读检查，不允许重复写副作用 | 是 |
| `safe_resume_from_state` | 权威 `state.json` 已进入下一非终态业务状态，允许据此恢复路由 | 是 |
| `safe_resume_decision` | decision ledger 已存在且绑定仍有效，允许按 decision id 消费一次并继续 | 是 |
| `needs_human` | execution、step result、workspace 或 policy 无法一致解释，必须人工接管 | 否 |
| `terminal_recovery` | 已达到可解释终态，只允许补齐终态记录和索引 | 否 |

`Automatic Resume Rate` 统计所有标记为“是”的分类。`needs_human` 计入 `Safe Stop Rate`，
不得包装成自动恢复成功。

### 4.2 P0 核心不原子窗口

P0 是进入 Core Dogfood 前的硬前置条件。P0 任一窗口出现 unsafe resume、重复外部副作用或 silent workspace drift，当前 Gate 直接失败。

| ID | crash window | 预注册期望 | 必须证明 |
|---|---|---|---|
| P0-1 | execution 创建前崩溃 | `safe_execute` | 无 worker 副作用；恢复后创建唯一 execution |
| P0-2 | worker 已修改 workspace、terminal execution 尚未持久化前崩溃 | `needs_human` | 不重复启动 worker；未知外部副作用统一 fail-closed |
| P0-3 | terminal execution 与 step result 已写、`state.json` 更新前崩溃 | `safe_reuse_step_result` | 通过 content-addressed step result 复用结果；worker start count 不增加 |
| P0-4a | `state.json` 已更新为下一非终态业务状态、graph checkpoint 前崩溃 | `safe_resume_from_state` | 从 state 和证据恢复下一步路由；checkpoint 不覆盖业务状态 |
| P0-4b | `state.json` 已更新为终态、graph checkpoint 前崩溃 | `terminal_recovery` | 只补齐 checkpoint 或索引，不重新执行外部节点 |
| P0-5 | decision ledger 已写、graph resume 前崩溃 | `safe_resume_decision` | 通过 decision id 重放批准消费；不得创建第二个 approval |

P0 统一硬约束：

- `Duplicate Worker Starts = 0`；
- `Duplicate External Effects = 0`；
- `Unsafe Resume Count = 0`；
- `Silent Workspace Drift = 0`；
- graph checkpoint 不得覆盖 execution、step result、workspace 形成的权威业务状态；
- 无法解释时必须进入 `needs_human`。

### 4.3 P1 完整恢复矩阵

P1 用于扩大恢复语义覆盖。P1 失败不一定否定 Gate 1/2，但会阻止进入 Core Dogfood 或降低最终决策等级。

| ID | crash window | 预注册期望 |
|---|---|---|
| P1-1 | execution 为 starting、child 尚未启动时崩溃 | `safe_execute` |
| P1-2 | worker 已启动但无明确终态 | `needs_human` |
| P1-3 | workspace evidence 已写、verification 前崩溃 | `safe_recompute_read_only` |
| P1-4 | verification 第一条命令完成后崩溃 | `safe_recompute_read_only` |
| P1-5 | verification 全部完成、risk gate 前崩溃 | `safe_reuse_step_result` |
| P1-6a | reviewer 部分完成、Aggregator 前崩溃，完整 evidence snapshot 和稳定 reviewer identity 均存在 | `safe_recompute_read_only` |
| P1-6b | reviewer 部分完成、Aggregator 前崩溃，snapshot 或 reviewer identity 不完整 | `needs_human` |
| P1-7 | interrupt checkpoint 已写、decision 前崩溃 | `needs_human` |
| P1-8 | graph 已 resume、decision consumption 前崩溃 | `safe_resume_decision` |
| P1-9 | final artifacts 已写、terminal checkpoint 前崩溃 | `terminal_recovery` |
| P1-10 | 父进程退出但 worker 子进程状态未知 | `needs_human` |
| P1-11 | 恢复前 workspace 被人工修改 | `needs_human` |
| P1-12 | 恢复前 policy snapshot 发生变化 | `needs_human` |
| P1-13 | SQLite checkpoint、execution、step result 或 artifact 被篡改 | `needs_human` |
| P1-14 | reviewer 结果绑定旧 evidence snapshot | `needs_human` |
| P1-15 | trace 出现同一 attempt 的重复事件 | `needs_human` |
| P1-16a | terminal execution 已持久化、step result 尚未写入，execution、workspace 和 artifact hash 一致 | `safe_repair_step_result` |
| P1-16b | terminal execution 已持久化、step result 尚未写入，但 workspace 或 artifact 无法一致解释 | `needs_human` |
| P1-17 | reviewer attempt claim 已写、runner-started marker 前崩溃 | `safe_reuse_claim_only` |
| P1-18 | runner-started marker 已写、execution 尚未发布且 owner 消失 | `needs_human / termination_unconfirmed` |
| P1-19a | reviewer terminal execution 已写、runner-result metadata 缺失，owner 或 child 仍存活 | `active_process_alive` |
| P1-19b | reviewer terminal execution 已写、runner-result metadata 缺失，owner 和 child 均消失且 execution/output 可信 | `needs_human / provider_error` |
| P1-20 | Compatibility legacy state/verdict 与 Gate 5 plan/result/execution/aggregate 不一致 | `needs_human` |
| P1-21 | `graph.get_state()` 前后 SQLite 主库或 sidecar 内容快照漂移 | `needs_human` |
| P1-22 | 已发布终态撤销在诊断、eval 或交付报告阶段崩溃 | `needs_human`，重试补齐撤销事务 |

P1-6a/P1-6b、P1-16a/P1-16b 和 P1-19a/P1-19b 必须使用不同 node id，不能运行后
临时选择。

---

## 5. 硬门槛

以下硬门槛适用于所有进入对应能力范围的评测。任一硬门槛失败，相关 Gate 不得标记为通过。

```text
Safety Invariant Pass Rate = 100%
Terminal State Parity for A/B = 100%
Artifact Contract Parity for A/B = 100%
Duplicate Worker Starts = 0
Duplicate External Effects = 0
Unsafe Resume Count = 0
Silent Workspace Drift = 0
Invalid Approve Over Verification Failure = 0
Invalid Success Without Required Human Approval = 0
Checkpoint / Execution / Step Result Identity Mismatch = 0
Required Secret Leakage = 0
Reviewer Context Leak = 0
Reducer Nondeterminism = 0
```

Gate 1 额外硬门槛：

- run 创建后 engine 固定，恢复时不得在 `linear` 与 `langgraph` 间切换；
- 新 run 未指定 engine 时默认 `linear`；
- 旧 `state.json` 缺少 engine 字段时按 `linear` 读取，且 status、continue、finish、recover
  均保持兼容；
- engine 不匹配必须在写入 run artifact 前拒绝；
- handler 不依赖 LangGraph 类型；
- 不复制业务模型；
- 不为第二引擎改变 Linear Runtime 的现有成功语义；
- 不为 LangGraph 大规模重写 artifact schema。

Gate 2 额外硬门槛：

- semantic parity case 全部通过；
- verification failed 不能 success；
- risk high 必须进入安全终态或人工确认；
- Graph state 及其实际序列化输出不含大文本、完整日志、凭证、Authorization header 或
  API key；
- 未安装 LangGraph 的基础依赖环境中，`linear` 模块导入、run 创建和 Gate 1 回归必须通过；
- 安装项目可选 LangGraph extra 后，graph 模块导入与顺序图测试必须通过。

Gate 3 额外硬门槛：

- P0-1～P0-4b 全部符合预注册期望；
- 持久化 checkpoint 不含大文本、完整日志、凭证、Authorization header、API key 或
  reviewer 私有消息；
- checkpoint、execution、step result、state 和 artifact identity 全部一致；
- duplicate worker、duplicate external effect、unsafe resume 和 silent workspace drift
  均为 0。

Checkpoint 体积、运行开销和自动恢复率只记录真实结果，不设“必须优于 linear”的硬门槛。

---

## 6. 指标定义

所有指标必须同时记录原始计数、分母、比例、样本列表和证据路径。

| 指标 | 定义 | 分母 | 通过口径 |
|---|---|---|---|
| `Terminal State Parity` | A/B 的 `terminal_status` 和成功语义一致率 | semantic parity cases | Gate 2 必须 100% |
| `Artifact Contract Parity` | 必需业务 artifact 类型、schema 和关键字段一致率 | required artifact checks | Gate 2 必须 100% |
| `Safety Invariant Pass Rate` | 所有硬安全不变量通过率 | active safety invariants | 必须 100% |
| `Duplicate Worker Starts` | 已有不可证明可重放副作用后再次启动 worker 的次数 | crash cases | 必须为 0 |
| `Duplicate External Effects` | 重复写入、命令执行、provider 动作或外部系统动作次数 | crash cases | 必须为 0 |
| `Automatic Resume Rate` | 所有预注册为“允许自动继续”的分类中自动安全继续比例 | auto-resumable crash windows | 记录，不单独作为通过门槛 |
| `Safe Stop Rate` | 预期 `needs_human` 的窗口正确停止比例 | needs_human windows | P0 必须 100%，P1 记录并用于决策 |
| `Unsafe Resume Count` | 预期停止却自动继续的次数 | all crash windows | 必须为 0 |
| `Silent Workspace Drift` | workspace fingerprint 变化后仍继续的次数 | drift cases | 必须为 0 |
| `Execution / Step Result / Checkpoint Consistency` | execution、step result、checkpoint、artifact 引用同一 attempt 和内容 hash 的比例 | recovery cases | P0 必须 100% |
| `Interrupt Consistency` | interrupt、decision ledger、resume 消费关系一致率 | HITL cases | Gate 4 必须 100% |
| `Reviewer Context Leak` | reviewer canary 泄漏到其他 reviewer 或 parent shared state 的次数 | reviewer isolation cases | 必须为 0 |
| `Reducer Determinism` | reviewer 完成顺序变化后聚合结果一致率 | reducer permutations | Gate 5 必须 100% |
| `Compatibility Provenance Consistency` | legacy review state/verdict 与 Gate 5 plan、result、execution、aggregate 重放一致率 | compatibility reader cases | Gate 5 必须 100% |
| `Reviewer Marginal Findings` | 多 reviewer 相对单 reviewer 的有效新增 finding 数 | dogfood tasks | 记录，用于 partial/accept 判断 |
| `Handoff Consistency` | handoff 与权威业务状态、workspace、policy snapshot 一致率 | handoff cases | Gate 6 决策依据 |
| `Checkpoint Size` | SQLite 与序列化 graph state 大小 | checkpoint samples | 记录，评估维护成本 |
| `Runtime Overhead` | 相对 linear 的 wall time、I/O、artifact 数量增量 | paired A/B runs | 记录，评估成本收益 |
| `Core Change Footprint` | 对稳定主线文件的侵入范围 | changed files | 记录，评估是否过度重构 |
| `Test Wall Time` | 每个分片实际耗时和 timeout 情况 | test shards | timeout 不计通过 |

---

## 7. 测试分片规则与 timeout 口径

### 7.1 目录和缓存

测试运行必须遵守项目工作区卫生规则：

- pytest 临时目录放在 `.tmp/pytest/runs/`；
- 每个分片使用独立 `--basetemp`；
- pytest cache 放在 `.tmp/pytest/cache/`；
- Ruff cache 放在 `.tmp/ruff/cache/`；
- 不把 pytest 临时结构写入 `runs/`、`.local-validation/` 或仓库根目录。

### 7.2 分片规则

默认分片按能力边界组织：

| 分片 | 覆盖范围 | 示例 node 集合 |
|---|---|---|
| `gate1-engine-boundary` | engine selection、旧 run 兼容、handler 边界、linear 回归 | `test_engine_selection.py`、`test_legacy_run_compatibility.py`、`test_handler_boundary.py` |
| `gate2-semantic-parity` | A/B 顺序等价和 artifact contract | `test_linear_graph_semantic_parity.py`、`test_graph_state_contract.py` |
| `gate3-crash-p0` | P0 crash injection | `test_crash_windows.py::test_p0_*` |
| `gate3-crash-p1` | P1 recovery matrix | `test_crash_windows.py::test_p1_*` |
| `gate4-hitl` | interrupt、decision binding、resume | `test_interrupt_resume.py`、`test_decision_binding.py` |
| `gate5-reviewers` | reviewer isolation、adapter recovery、reducer determinism、Compatibility provenance | `test_parallel_review_runner_adapter.py`、`test_parallel_review_resume.py`、`test_parallel_review_artifacts.py`、`test_parallel_review_graph.py` |
| `gate6-handoff` | checkpoint handoff、goal cross session | `test_checkpoint_handoff.py`、`test_goal_cross_session.py` |

实际文件名可以随实现调整，但分片名称、覆盖目标和 node id 列表必须在运行前记录。不得在失败后移动 node id 来规避 timeout 或失败。

### 7.3 timeout 口径

- 单次 pytest 运行超过 60 秒时，必须按预注册 node id 集合继续拆分。
- timeout 不是通过，也不是业务失败；它是 `timeout-unresolved`。
- `timeout-unresolved` 不得进入通过分子。
- 如果 timeout 发生在 P0 或 Gate 2 分片，相关 Gate 状态为 `blocked`，不得标记为通过。
- 分片完成后必须核对 collected tests 与各分片 node id 的并集一致。
- 只有 pytest 明确输出 `passed`、`failed`、`skipped`、`xfailed`、`xpassed` 等计数时，才可作为测试证据。
- 文档-only 修改阶段可以只运行 `git diff --check`，但必须说明未运行代码测试的原因。

### 7.4 建议验证命令

完整实现阶段默认验证：

```powershell
python -m compileall -q src
ruff check src tests
git diff --check
pytest -q --require-langgraph tests/experimental/langgraph_engine --basetemp .tmp/pytest/runs/langgraph-engine
pytest -q tests/test_success_semantics.py --basetemp .tmp/pytest/runs/success-semantics
pytest -q tests/test_evidence_freshness.py --basetemp .tmp/pytest/runs/evidence-freshness
pytest -q tests/test_runtime_safety_integration.py --basetemp .tmp/pytest/runs/runtime-safety
pytest -q tests/test_finish_artifact_integrity.py --basetemp .tmp/pytest/runs/finish-artifact
pytest -q tests/test_execution_control_safety.py --basetemp .tmp/pytest/runs/execution-control
pytest -q tests/test_review_artifact_integrity.py --basetemp .tmp/pytest/runs/review-artifact
```

---

## 8. Fake Runner 与真实 Runner 分离

fake runner 和真实 runner 的证据必须分开记录、分开解释、分开进入决策。

### 8.1 Fake Runner

fake runner 用于 deterministic safety：

- semantic parity；
- crash injection；
- checkpoint / recovery；
- interrupt / decision binding；
- reviewer isolation；
- reducer determinism；
- secret redaction；
- timeout / provider error 语义。

fake runner 可以证明编排语义、安全门槛和恢复协议，但不能证明真实模型能力、真实任务质量或 provider 稳定性。

Gate 5 的退出允许真实 provider 调用仍为 `0`，前提是 role-specific Runner adapter、进程级
execution evidence、crash recovery 和 legacy reader provenance 已通过确定性测试与独立复审。
真实模型质量、topology 边际收益和 provider 成本只属于 Gate 5.5，不能反向包装为 Gate 5
已经证明。

### 8.2 真实 Runner

真实 runner 只用于 dogfood 和成本收益评估：

- 真实模型延迟；
- provider error 与 retry 行为；
- token / 上下文成本；
- reviewer finding 质量；
- HITL 体验；
- artifact 可读性；
- 人工接手成本。

真实 runner 运行前必须预注册：

- 任务说明；
- 模型；
- 预算；
- 数据出站边界；
- 成功标准；
- 允许的外部副作用；
- stop 条件；
- 是否允许网络请求。

真实 runner 结果不能覆盖 fake runner 中已经失败的硬安全门槛。fake runner 通过也不能宣称真实 runner 质量已经成立。

---

## 9. 证据记录

每次评测至少记录：

- branch；
- HEAD；
- fixture repo commit；
- runner 类型；
- engine；
- graph schema version；
- policy snapshot hash；
- workspace fingerprint；
- test shard；
- pytest node id 列表；
- timeout 设置；
- run id；
- execution id；
- step result hash；
- artifact index；
- checkpoint identity；
- decision id；
- metric 原始计数；
- 结论状态。

证据路径必须位于项目内允许目录。正式 run artifacts 放入 `runs/`；人工验证最终日志、检查报告和复现脚本放入 `.local-validation/`；pytest 中间目录放入 `.tmp/pytest/runs/`。

不得把 `.env`、API key、Authorization header、provider token 或其他凭证写入 evidence、trace、artifact、fixture、日志或文档。

---

## 10. Gate 状态与决策

每个 Gate 必须输出一个明确状态：

| 状态 | 含义 |
|---|---|
| `pass` | 该 Gate 所有硬门槛通过，指标和证据完整 |
| `partial-pass` | 非核心扩展能力部分成立，但存在明确限制 |
| `blocked` | timeout、证据缺失、环境问题或 reviewer blocker 导致无法判断 |
| `fail` | 硬门槛失败或语义不成立 |

`blocked` 不能当作 `pass`。如果阻塞来自测试超时或证据缺失，必须保留原始失败记录，并在修复后重新运行同一预注册分片。

---

## 11. Accept / Partial / Reject 决策

最终决策必须基于本协议记录的证据，而不是基于实现投入量。

### 11.1 Accept

满足以下条件才允许 `accept`：

- Gate 1/2 全部 `pass`；
- 所有适用硬安全门槛通过；
- P0 crash windows 全部符合预注册期望；
- 没有重复 worker 或重复外部副作用；
- 没有 unsafe resume 或 silent workspace drift；
- Linear Runtime 核心成功语义未被迫改变；
- LangGraph 明确改善 checkpoint、HITL、parallel review 或 handoff 中至少一个能力；
- 真实 runner dogfood 至少证明安全闭环，不夸大模型能力；
- 维护成本、checkpoint 体积和 core change footprint 与收益匹配。

### 11.2 Partial

出现以下情况时应优先考虑 `partial`：

- Gate 1/2 通过，但顺序图本身收益有限；
- checkpoint、interrupt、reviewer fan-out 或 handoff 中只有部分能力有明确价值；
- 需要保留 experimental adapter，暂不成为正式 engine；
- P0 通过，但 P1 或 dogfood 证据不足；
- 真实 runner 证据不足，但 deterministic safety 已成立；
- handoff 能力更适合独立于 LangGraph 复用。

`partial` 必须写清楚保留什么、删除什么、哪些能力不能合入主线。

### 11.3 Reject

出现以下任一情况应 `reject`：

- Gate 1 或 Gate 2 无法通过；
- 需要复制或重写大部分业务 Runtime；
- Linear Runtime 必须改变核心成功语义才能适配 graph；
- Graph 与 Vega 形成两套业务真相源；
- 恢复主要依赖猜测，而不是 execution、step result、checkpoint 和 workspace 对账；
- 出现重复 worker 或重复外部副作用；
- 旧 approval、旧 evidence 或漂移 workspace 可以继续；
- verification failed 可以被标记为 success；
- 未批准高风险动作可以进入 success；
- 多 reviewer 只增加成本，没有新增有效信息；
- 大多数 crash window 只能达到现有 recover 水平，且没有改善表达或审计；
- 维护和测试负担显著增加，但没有可证明收益。

`reject` 不等于实验失败。清楚证明“LangGraph 不值得作为 Vega 编排引擎引入”同样是有效架构结论。

---

## 12. Gate 0 退出检查

Gate 0 完成时必须满足：

- 本协议已冻结；
- A-F 分组和 Gate 1/2 优先级明确；
- semantic parity 字段已预注册；
- P0/P1 crash window 和期望分类已预注册；
- 硬门槛已预注册；
- 指标定义、分母和通过口径已预注册；
- 测试分片和 timeout 口径已预注册；
- fake runner 与真实 runner 的证据边界已预注册；
- accept / partial / reject 决策规则已预注册；
- 独立 reviewer 没有未关闭 Blocker / High。

只有 Gate 0 退出检查通过后，才能进入 Gate 1 实现。
