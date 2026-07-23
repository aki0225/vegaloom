# MA-2A 单 Slice 运行时桥接交接

> 日期：2026-07-23
> 分支：`experiment/ma2-runtime-bridge`
> 基线：`3f553e09328a1b52b76b07bd3bf89fe651a3fd6a`
> 状态：本地实现与验证完成，等待远端 CI 最终确认
> 默认产品行为：未改变
> 真实 Planner / Provider：未调用

## 1. 本轮完成内容

新增 `src/vega/delegation_runtime.py`，实现默认关闭、仅由测试显式调用的单 Slice
运行时桥接：

- 只接受注入式 Worker、scope probe 和 verification probe，不创建默认 Runner。
- 每次调用都使用 `evaluate_delegation_payload()` 重新计算 readiness。
- `human_required`、`premium_required`、stale snapshot、缺失 shell kind、错误 Worker tier
  和非法 slice 均在 Worker 启动前 fail-closed。
- 只有 `budget_eligible + budget tier` 可以调用一次 Worker。
- 复用现有 `RunnerExecutionContext` 和 `executions/worker/execution.json`，未建立第二套进程、
  PID、heartbeat、deadline 或恢复状态机。
- plan、readiness、执行前后 snapshot、scope、verification 和 attempt 只能写入真实的
  `workspace/runs/<run_id>` 目录。
- run 根、artifact 路径和引用路径拒绝绝对路径逃逸、`..`、符号链接、junction、
  reparse point、hardlink、非普通文件和非权威同名文件。
- Worker 返回值必须与 `execution.json` 的 `completed + returncode=0` 一致，且不能存在
  `termination_unconfirmed`。
- Worker 不得改变 HEAD、越过 slice 写范围或超过 changed files、new files、diff lines
  预算。
- scope 和 verification 必须形成结构化且通过的 artifact，之后才会写入
  `DelegationAttempt`。
- `validate_delegation_attempt()` 会重新验证 run 根、精确引用路径、文件实际字节哈希以及
  plan、slice、readiness、execution、scope、verification 的语义绑定。
- 桥接结果只可能是 `blocked` 或 `attempt_recorded`，不会返回产品级成功状态。

`src/vega/review_runtime.py` 增加受控 `delegation_summary`：

- 只允许 `plan_id`、`slice_id`、`readiness_status`、`attempt_sha256`、
  `execution_sha256`。
- 字段同时经过类型、格式和脱敏校验。
- `worker_chat`、未知字段、嵌套对象和不合法哈希不会进入 review context 或 review pack。
- `contains_worker_chat` 继续固定为 `False`。

CI 已更新：

- pytest 收集数从 `649` 更新为 `660`。
- `tests/test_delegation_runtime_bridge.py` 已加入 Python 3.12
  `semantics-evidence-review` 分片。

## 2. 本地验证证据

节点收集：

```text
660 collected
```

Windows 本地分组执行全部测试文件：

```text
659 passed
1 skipped
0 failed
```

唯一 skip：

```text
tests/test_runtime_safety_integration.py
仅覆盖 POSIX shell 变量展开语义
```

该节点必须由 Linux CI 的 POSIX 专项实际通过，不能用 Windows 本地结果替代。

关键回归：

```text
tests/test_delegation_runtime_bridge.py: 11 passed
tests/test_delegation_contract.py: 49 passed
tests/test_review_artifact_integrity.py: 18 passed
tests/test_execution_control_safety.py: 15 passed
tests/test_context_boundaries.py: 36 passed
tests/test_assurance_verification_semantics.py: 14 passed
tests/test_assurance_stage1_contract.py: 59 passed
```

额外对抗检查：

```text
4 passed
```

覆盖失败 execution、失败 verification、伪造 run 根、非权威同名 plan，以及 attempt
篡改后的 reviewer 摘要失效。

质量门禁：

```text
ruff check: passed
compileall: passed
repository hygiene --base-ref 3f553e0: passed
repository hygiene --base-ref origin/main: passed
git diff --check: passed
Python 3.12 shard file coverage: 24/24
```

## 3. 明天接续

1. 查看本分支手动触发的 GitHub Actions 结果，重点确认 Python 3.11 全量、Python 3.12
   `semantics-evidence-review`、Windows 和 POSIX 专项全部通过。
2. 若 CI 全绿，新增独立的 MA-2A Gate 结论记录，将结论限制为“单 Slice 运行时桥接具备进入
   下一次预注册的条件”。
3. 再决定是否建立 MA-2B 预注册；不得直接启动真实 Planner × Worker Pilot。

## 4. 停止线

- 不修改 `main`，不 rebase MA-1。
- 不接入默认 CLI、Loop、Finish、Goal 或产品成功路径。
- 不调用真实 Planner、Worker、Provider 或网络模型服务。
- 不把 readiness、Worker 正常退出、attempt 或 reviewer approve 当作 verification /
  Assurance 成功证据。
- 未完成新的独立预注册前，不进入 MA-2B。
