# Gate 2 顺序 LangGraph 等价图结果

> 状态：`gate-2-pass`
>
> 日期：2026-07-15
>
> 分支：`experiment/langgraph-comparison`
>
> Gate 0 开始前 HEAD：`private-gate-0-contract-redacted`
>
> 代码实验基线：`private-experiment-base-redacted`

---

## 1. Gate 2 范围

Gate 2 只验证顺序 LangGraph 图能否复用现有业务步骤，并与 Linear Runtime 保持业务语义等价。

本轮完成：

- 增加可选依赖 `langgraph>=1.2,<1.3`；
- 使用 `StateGraph + Command` 实现顺序图和确定性 routing；
- Linear 与 LangGraph 共用 `LoopStepProgramDriver` 和九类结构化 Step 指令；
- graph run 使用独立 run，不允许从 linear run 切换 engine；
- Graph State 只保存薄引用，不复制 Vega 业务状态；
- Graph State 与 `state.json`、policy snapshot、terminal artifact 交叉校验；
- Graph success 必须同时具备可信业务状态和可信 Graph State；
- Graph State 最终写入失败时，原业务 success 使用 append-only 补偿事件撤销；
- 单轮和五轮 semantic parity、外部副作用计数、artifact schema、evidence freshness
  和最终报告结论进入自动验证。

明确未做：

- 未实现 SQLite checkpointer；
- 未实现 graph recovery、step result manifest 或 crash resume；
- 未实现 HITL、decision interrupt / resume；
- 未实现并行 reviewer subgraph；
- 未运行真实 worker / reviewer；
- 未启动 Gate 3。

## 2. 共享业务程序

Linear 与 LangGraph 不维护两套业务 Runtime。两者都执行同一份结构化程序：

```text
prepare_run
capture_workspace
execute_worker_epoch
reconcile_workspace
run_verification
run_reflect
evaluate_risk
dispatch_review
finalize_run
```

Linear 直接顺序消费指令；LangGraph 为每类指令建立节点，并由 `Command.goto` 路由到下一
业务步骤。节点不复制 Brief、worker、verification、risk、review 或 finalize 业务逻辑。

Graph recursion limit 根据 `max_iterations` 和节点数量推导。五轮 request_changes 场景取得
明确终态，未再命中默认递归上限。

## 3. Graph State 契约

Graph State 的 canonical authority refs 固定为：

```text
state.json
loop-plan.md
project-policy-snapshot.json
```

最终 Graph State 还可引用当前权威 terminal artifact，但不能保存完整 prompt、diff、日志、
凭证、进程信息或业务状态镜像。

读取和写入边界包括：

- 固定字段集合和 16 KiB 上限；
- 严格 JSON 字段类型；
- 拒绝重复 key；
- policy snapshot SHA-256 校验；
- Graph run identity、engine 和业务终态交叉校验；
- canonical relative ref 校验；
- 路径越界、symlink、junction 和 reparse point 拒绝；
- 原子替换，失败时保留旧文件并清理临时文件；
- 基础模块不导入第三方 `langgraph` 包。

Gate 2 的 future-gate 字段必须为空：

- `latest_step_result_id`
- `pending_human_decision_id`
- `review_results`

## 4. Graph Success 信任规则

Graph 业务 finalize 先写入权威 `state.json`，Graph State 在图完成后写入。跨两个文件无法形成
原子事务，因此不能假设补偿写一定成功。

最终信任规则是：

```text
langgraph + success
= 完整合法的 LoopAutomationState
+ 完整合法且与业务状态交叉一致的 graph/graph-state.json
```

以下消费者都执行相同的 fail-closed 规则：

- `run_status_payload`
- terminal `run_loop_eval`
- loop artifact integrity
- loop evidence freshness
- semantic parity comparator

如果 Graph State 缺失、JSON 损坏、identity 冲突、字段类型错误或包含重复 key，消费者拒绝
采用 success，并且不修改现场。

若 Graph State 写失败但业务 success 已落盘，正常补偿路径会：

1. 把业务状态改为 `needs_human / graph_evidence_failed`；
2. 写入 `graph-failure-report.md`；
3. 保留原 `run_finished(success)`；
4. 追加结构化 `run_terminal_revoked`；
5. 重新计算 terminal eval。

如果连补偿状态写入也失败，磁盘可能保留旧 success，但所有可信读取和评测入口仍会因缺少
可信 Graph State 而拒绝该 success。

## 5. Semantic Parity

单轮 A/B case：

- success；
- verification failure；
- high-risk human review；
- reviewer failure。

五轮 case：

- reviewer 连续 request_changes；
- 第五轮进入 `needs_human`；
- worker 启动 5 次；
- worker workspace 写入 5 次；
- reviewer 调用 5 次；
- verification 调用 0 次；
- `duplicate_effect_count = 0`；
- 外部动作总数 15；
- child run 数量固定为 brief 1、reflect 5、review 5。

比较分母包括：

- terminal status 和 current step；
- verification、risk、verdict 和 human reason；
- workspace diff hash；
- 必需 artifact 类型和 JSON schema；
- artifact integrity 与 evidence freshness；
- 最终 `final-report.md` 结论；
- worker、provider、verification、业务写入和 child-run 数量；
- duplicate external effect。

## 6. 独立复审

复审不是一次性确认。reviewer 在多轮只读审查中先后发现并推动关闭：

- 默认 recursion limit 无法覆盖五轮；
- `run_finished(success)` 与后置 Graph evidence failure 语义冲突；
- 外部副作用 parity 计数不完整；
- graph failure status 指向不存在的 Graph State；
- Graph State 写失败后，补偿状态写也失败可能遗留磁盘 success；
- status / eval 已拒绝残留 success，但 integrity / freshness 仍可能采用；
- Graph State 接受 `schema_version=true`、falsey 非 dict `review_results` 或重复 key；
- 显式 `engine=langgraph` 的损坏 state 可通过 kind 推断绕过完整 loop schema。

全部 High 关闭后，最终两路独立只读复审为：

```text
Architecture review
Blocker: 0
High: 0
Medium: 1
Low: 1
verdict: pass

Test adequacy review
Blocker: 0
High: 0
Medium: 1
Low: 3
verdict: pass
```

## 7. 最终快照验证

基础解释器：

```text
langgraph_installed=false
```

基础环境全量 node：

```text
experimental base: 61 passed
path / config / workspace: 33 passed
redaction / security: 42 passed
CLI / recovery: 36 passed
P0 regressions: 12 passed
execution safety: 12 passed
runtime safety: 18 passed
review artifact integrity: 18 passed
success semantics: 27 passed
evidence freshness: 19 passed
finish artifact integrity: 18 passed
smoke: 97 passed
```

合计：

```text
393 passed
0 failed
```

基础环境直接运行 experimental 目录：

```text
61 passed
1 skipped
```

安装可选 extra 的隔离环境：

```text
langgraph_version=1.2.9
experimental core: 61 passed
semantic parity and graph failure matrix: 15 passed
```

合计：

```text
76 passed
0 failed
```

静态检查：

```text
python -m compileall -q src
ruff check src tests --cache-dir .tmp/ruff/cache/gate2-final5
git diff --check
```

结果均为 passed。仓库根目录和 `runs/` 没有 pytest basetemp；测试与 Ruff 产物均位于被忽略的
`.tmp/`。

一次 8-node success semantics 分片超过外层 60 秒限制。该结果未计入通过分子，残留测试
进程退出后使用两个独立目录拆为 `4 passed + 4 passed`，取得明确终态。

## 8. 非阻断项

以下问题不影响 Gate 2 退出，但应保留到后续加固：

- 持久化 `eval.md` 在 Graph State 之前生成；可信消费者会重新执行 terminal eval，但磁盘
  eval 本身不包含最终 Graph State PASS；
- worker interruption 和 verification interruption 尚未增加专门的 Linear / Graph A/B
  配对 case，现有共享业务 generator 和 Linear 安全测试降低了实现分叉风险；
- 非 UTF-8、读侧 reparse 和临时文件初始写失败已有 fail-closed 实现，但缺少直接回归测试。

这些项目不能在 Gate 3 中被解释为 checkpoint / recovery 已完成。

## 9. Gate 2 退出判定

| 退出条件 | 结果 |
|---|---|
| semantic parity case 全部通过 | pass |
| verification failed 不能 success | pass |
| risk high 进入安全终态 | pass |
| Graph State 不含大文本和凭证 | pass |
| Graph State 与业务状态、policy 和 terminal artifact 交叉校验 | pass |
| graph success 缺少可信 Graph State 时所有消费者 fail-closed | pass |
| 基础环境未安装 LangGraph 时 Linear 能力保持可用 | pass |
| 安装 optional extra 后 graph 路径可执行 | pass |
| duplicate external effect 为 0 | pass |
| 独立 reviewer 无 Blocker / High | pass |

最终结论：

```text
Gate 2 = pass
Gate 3 = ready, not started
```

## 10. 明日接续：Gate 3

下一步只进入 Gate 3，不同时启动 HITL 或并行 reviewer。

建议顺序：

1. 先读取 `RECOVERY-CONTRACT.md`、`STATE-OWNERSHIP.md` 和本文件；
2. 先写 `test_step_result_manifest.py`、`test_checkpoint_resume.py` 和 P0 crash window
   故障注入，不先写 happy-path demo；
3. 扩展并复用现有 execution evidence，冻结 attempt / step result identity；
4. 增加 SQLite checkpointer，但保持 `state.json` 为权威业务状态；
5. 实现 checkpoint、execution、step result、workspace 和 policy reconciliation；
6. 逐项关闭 P0-1 至 P0-4b，证明 duplicate worker / external effect 为 0；
7. 完成独立复审并输出 Gate 3 结果后，才允许进入 HITL。

Gate 3 停止条件：

- checkpoint 能覆盖 `state.json` 业务终态；
- 无法解释的外部副作用被自动重放；
- graph recovery 需要猜测 workspace 现场；
- 为恢复而复制第二套业务状态；
- 单个 pytest 分片超过 60 秒且未拆分取得明确终态。
