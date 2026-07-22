# Gate 5 Phase 3：Artifact、Graph State v2 与可变 N 路 Fake Fan-out

> 阶段状态：`pass`
>
> Gate 5：`in progress`
>
> Gate 5.5：`not started`
>
> 日期：`2026-07-17（星期五）`
>
> 时区：`Asia/Shanghai`
>
> 实现起点：`<source-experiment-phase-3-start>`
>
> 真实 provider 调用：`0`

---

## 1. 阶段结论

本阶段已经完成 topology 无关的 Reviewer 控制面基础：

```text
真实 run artifact -> evidence snapshot hash
ReviewPlan append-only artifact
review execution / result / aggregate artifact
artifact hash 读取复核
Graph State v2 窄 result ref
LangGraph Send 动态 1/2/3 路 fan-out
deterministic aggregate node
fake reviewer canary / completion-order matrix
```

因此：

```text
Gate 5 Phase 3 = pass
artifact persistence = pass
Graph State v2 review ref = pass
variable-N fake fan-out = pass
real isolated reviewer adapter = not implemented
Gate 5 = not passed
Gate 5.5 = not started
```

这里的 `pass` 只说明确定性基础设施成立，不说明多 Reviewer 比单 Reviewer 更好，也不说明
真实 provider 隔离和质量已经成立。

## 2. Artifact 合同

新增：

```text
src/vega/parallel_review_artifacts.py
tests/experimental/langgraph_engine/test_parallel_review_artifacts.py
```

### 2.1 实际 evidence hash

`build_review_evidence_snapshot_from_artifacts()` 不再只接受调用方提供的 hash，而是从当前
run 内的真实文件读取：

```text
policy snapshot
verification result
risk result
acceptance evidence manifest
```

任一文件内容变化都会改变 `evidence_snapshot_sha256`。所有 ref 必须是 run 内 POSIX 相对
路径，链接、junction、reparse point 和越界路径均拒绝。

### 2.2 Append-only 发布

ReviewPlan、fake execution、单路 result 和 aggregate 使用：

```text
临时文件
-> flush + fsync
-> 同文件系统 hardlink 独占发布
-> 目标存在时不覆盖
```

相同 identity 与相同规范化内容可幂等复用；相同 identity 与不同内容 fail-closed。这里的
append-only 是协议约束，不声称本地文件物理不可修改。

### 2.3 Windows 路径预算

最初使用完整 plan/result identity 作为目录和文件名时，真实 Windows pytest 临时路径触发
`WinError 3`。最终路径只使用 SHA-256 的 96-bit 前缀作为目录或文件短名，完整 identity
仍保存在 artifact 内容和窄引用中。

短路径发生前缀碰撞时，独占写入和完整 identity 校验会拒绝覆盖，不会静默合并。

### 2.4 Result 读取复核

每次读取 Reviewer result 都重新校验：

```text
artifact_ref
artifact_sha256
result_id
review_plan_id
run_id
iteration
reviewer_role
evidence_snapshot_sha256
attempt_id
execution_ref
execution_sha256
execution run / step / attempt / replay_class / terminal status
```

execution、result 或 ref 任一被修改，Graph State 不得继续信任。

## 3. Graph State v2

`src/vega/loop_graph_state.py` 显式升级为：

```text
schema_version = 2
engine_version = gate5-review-v1
```

v2 的 `review_results` 只保存 `ParallelReviewResultRef`：

```text
schema_version
result_id
review_plan_id
reviewer_role
evidence_snapshot_sha256
attempt_id
artifact_ref
artifact_sha256
```

不保存 summary、finding 自由文本、process output 或 reviewer 私有 canary。

历史 v1 仍可读取，但继续执行旧安全边界：

```text
schema_version = 1
engine_version = gate3-checkpoint-v1
review_results 必须为空
```

因此 Gate 3/4 的历史合同没有被静默放宽。

## 4. 可变 N 路 LangGraph Fan-out

新增：

```text
src/vega/parallel_review_graph.py
tests/experimental/langgraph_engine/test_parallel_review_graph.py
```

图结构为：

```text
dispatch_reviewers
  -> Send(execute_reviewer, role) x ReviewPlan.required_roles
  -> merge narrow result refs
  -> aggregate_results
  -> END
```

`single`、两路 adaptive 和 `fixed_three` 均使用同一个图；并发上限由
`ReviewPlan.max_parallelism` 控制。

Graph State 只接收窄引用。Reviewer 完整输出先落独立 artifact，aggregate node 再从磁盘
重新读取并复核 hash，不能直接信任分支返回的自由文本。

## 5. Fake Reviewer 的边界

`DeterministicFakeReviewer` 会生成明确标记为：

```text
runner_identity.kind = deterministic-fake-reviewer
replay_class = read_only_replayable
```

的 execution artifact。它不调用模型、不读取凭证、不修改目标 workspace。

本阶段证明：

- 计划选择 1/2/3 路时 fan-out 数量正确；
- 三路按两种相反完成顺序执行时 aggregate 完全一致；
- timeout 结果不能聚合为 approve；
- 缺少必需 executor 时在 fan-out 前失败；
- 每路 result 只包含自己的私有 canary；
- parent Graph State、aggregate 和 InMemory checkpoint 不包含 reviewer 私有 canary；
- fake result ref 可直接通过 Graph State v2 的实际 artifact 复核。

本阶段没有证明：

- 独立 OS 进程或 provider session 之间已经完成真实上下文隔离；
- 真实模型 prompt 不会泄漏；
- provider timeout、parse error 和停止语义已经接入现有 Reviewer Runtime；
- 多 Reviewer 有真实边际收益。

## 6. 自动化验证

### 6.1 Gate 5 新增与直接合同

在安装 `dev` 与 `langgraph` optional dependencies 的隔离环境中：

```text
parallel review contract
parallel review artifacts
parallel review graph
Graph State contract
step result contract

86 passed in 3.50s
```

### 6.2 Gate 1～4.5 与 Runtime 回归

`tests/experimental/langgraph_engine/` 共收集 229 项。本轮按完整测试文件或完整 node id
分片运行，最终：

```text
229 passed
0 failed
0 skipped
```

其中包括：

- Gate 4.5 Core Dogfood harness：48 passed；
- checkpoint：5 passed；
- crash windows：8 passed；
- decision binding：15 passed；
- HITL / interrupt：5 passed；
- engine / handler / legacy：47 passed；
- linear / graph semantic parity：15 passed。

`test_five_round_linear_and_graph_programs_remain_equivalent` 单个 node 用时
`119.17s`，无法继续按 node 内部拆分。其他超过 60 秒的组合均拆为完整 node id 后重新运行；
超时的组合命令没有计入通过证据。

### 6.3 Optional dependency 与静态验证

未安装 LangGraph optional dependency 的默认 Python 环境会跳过 fan-out 图测试，而不是在
collection 阶段失败：

```text
79 passed
1 skipped
```

最终静态验证：

```text
python -m compileall -q src = pass
ruff check src tests = pass
git diff --check = pass
UTF-8 / BOM / 敏感标记 / 相对文档链接审查 = pass
```

### 6.4 禁止过度声明

本轮没有运行全仓 `python -m pytest`，因此不能把 229 项实验测试写成“全仓测试通过”。

## 7. Gate 5 尚缺内容

Gate 5 仍缺：

1. 将现有真实只读 Reviewer Runner 适配为 role-specific executor；
2. 为三个角色生成同一公共 evidence package 加独立角色 prompt；
3. 真实 execution、provider error、parse error、stop 与 timeout 接线；
4. 与现有单 reviewer `review-verdict.json` 和 Loop 终态的兼容边界；
5. 部分 fan-out 完成后的 checkpoint / resume 复核；
6. 真实进程级 canary 隔离；
7. Gate 5 独立复审，无未关闭 Blocker / High。

上述内容通过前：

```text
Gate 5 不得标记 pass
Gate 5.5 不得启动真实 topology 收益评估
简历不得声称真实并行 Multi-Agent Reviewer 已完成
```

## 8. 下一步

下一阶段只做真实执行适配，不立即运行收益评估：

```text
role prompt contract
-> existing read-only Runner adapter
-> real execution/result parser
-> fake failure matrix
-> checkpoint/retry boundary
-> Gate 5 independent review
```

完成 Gate 5 后，再按 `GATE-5-TOPOLOGY-EVAL-CANDIDATE.md` 预注册 Gate 5.5 的
`single / adaptive / fixed_three` 真实对照实验。
