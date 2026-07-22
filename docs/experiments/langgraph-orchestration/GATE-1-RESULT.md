# Gate 1 实现与独立复审结果

> 状态：`gate-1-pass`
>
> 日期：2026-07-15
>
> 分支：`experiment/langgraph-comparison`
>
> Gate 0 开始前 HEAD：`private-gate-0-contract-redacted`
>
> 代码实验基线：`private-experiment-base-redacted`

---

## 1. Gate 1 范围

Gate 1 只建立最小 Engine / Step Handler 边界，不安装或实现 LangGraph，不进入 Gate 2。

本轮完成：

- 新 loop 默认持久化 `engine=linear`；
- 旧 run 缺少 engine 字段时按 `linear` 读取；
- `langgraph` 在 Gate 1 创建入口被写前拒绝；
- continue、recover、finish 在写 artifact 前校验持久化 engine 所有权；
- status 严格校验 engine 和完整 `LoopAutomationState`；
- linear Runtime 通过结构化 Step Service 调用：
  - `prepare_run`
  - `capture_workspace`
  - `execute_worker_epoch`
  - `reconcile_workspace`
  - `run_verification`
  - `run_reflect`
  - `evaluate_risk`
  - `dispatch_review`
  - `finalize_run`
- 删除把整段 `_run_auto_iterations()` 包装成单一 handler 的黑盒边界；
- linear 成功语义、artifact 结构和既有安全门禁保持不变。

明确未做：

- 未安装 LangGraph；
- 未增加 graph module、graph state、checkpoint 或 recovery handshake；
- 未移植其他分支的 Scope Gate 或 Selective Memory；
- 未运行真实 worker / reviewer；
- 未 commit、push 或发布。

## 2. Engine 所有权

写入口使用两阶段校验：

1. 在完整 Pydantic schema 读取前，只读预检 raw `state.json` 的 engine 所有权；
2. schema 读取成功后，再校验持久化 engine 与请求 engine 一致且为 `linear`。

以下情况均在写 artifact 前拒绝：

- 持久化 `engine=langgraph`，请求 engine 省略；
- 持久化 `engine=langgraph`，请求 `engine=langgraph`；
- linear run 请求切换为 `langgraph`；
- engine 为 null、空字符串、未知值或非字符串；
- state 中存在重复 engine 字段；
- 截断 JSON 仍声明 `engine=langgraph`；
- state 文件存在但不可读；
- schema 其他部分损坏，但仍可识别为 graph run。

旧 state 完全缺少 engine 字段时继续按 `linear` 兼容。旧 linear state JSON 损坏且没有
engine 声明时，仍保留既有 recovery / finish diagnostic 行为。

## 3. Step Handler 边界

`LoopStepServices` 不依赖 LangGraph 类型，也不复制业务状态模型。请求对象只传递当前步骤
所需的路径、配置、runner、execution context 和业务状态引用。

最终实现不再让 linear 业务方法直接调用 Brief、workspace baseline、worker、verification、
reflect、risk、review 或 terminal finalize 的底层实现。默认 adapter 仍复用现有 Runtime 和
函数，因此 Gate 1 没有改变业务结论或 artifact schema。

动态测试覆盖：

- constructor 注入的 Step Services 确实被 Runtime 使用；
- auto 成功路径九个步骤的调用顺序和请求字段；
- worker interruption 后不会调用 workspace reconcile、verification、reflect、risk 或 review；
- finalize 收到当前 run、同一 state 引用、终态和 current step；
- Runtime、engine 和 step module 均不导入 LangGraph。

## 4. 独立复审

第一次 Gate 1 代码复审：

```text
Blocker: 1
High: 1
verdict: fail
```

发现：

1. 持久化 graph run 仍可被部分 linear continue / recover / finish 路径修改。
2. 初版 handler 只是整段 linear Runtime 的黑盒包装，没有节点级边界。

完成 fail-closed 和 Step Service 重构后，独立复审继续发现并推动关闭：

- 损坏、重复或不可读 state 的 engine 所有权含糊；
- workspace baseline 绕过 Step Service；
- status 未执行完整 loop state schema 校验。

第三次最终独立只读复审：

```text
Findings: none
Blocker: 0
High: 0
verdict: pass
```

最终 reviewer 明确确认：

- engine 所有权检查在写前 fail-closed；
- `capture_workspace` 已进入结构化 Step Service；
- continue、recover、finish 不会修改 Gate 1 未实现的 graph run；
- status 不会把非法 state 降级展示；
- 所有 linear 终态经 `finalize_run` 收口；
- 业务方法没有绕过 Step Service；
- Gate 1 模块没有 LangGraph 强依赖。

## 5. 最终快照验证

最终快照环境仍未安装 LangGraph：

```text
langgraph_installed=false
```

静态检查：

```text
python -m compileall -q src
ruff check src tests --cache-dir .tmp/ruff/cache/gate1-review-3
git diff --check
```

结果：

```text
compileall: passed
ruff: passed
git diff --check: passed
```

最终快照测试：

```text
tests/experimental/langgraph_engine: 43 passed
tests/test_cli_recovery_hardening.py: 35 passed, 1 skipped
tests/test_p0_regressions.py: 12 passed
tests/test_success_semantics.py: 27 passed
Gate 1 smoke node 集合: 7 passed
finish 损坏 linear state diagnostic: 1 passed
```

合计唯一 node：

```text
125 passed
1 skipped
0 failed
```

全仓收集：

```text
375 tests collected
```

smoke 七节点首次整组命令超过 60 秒，按契约不计通过或失败；随后拆为 `4 passed` 和
`3 passed`，七个预注册 node 均取得明确终态。Gate 1 没有把任何 timeout 计入通过分子。

本轮更早还完成过 finish artifact、evidence freshness、runtime safety、review artifact、
execution control 和 path/config 的广泛回归；由于后续继续收紧 engine/status/capture 边界，
这些较早结果不计入上面的最终快照 `125 passed`。

## 6. Gate 1 退出判定

| 退出条件 | 结果 |
|---|---|
| linear 核心回归通过 | pass |
| 现有终态和 artifact contract 无意外变化 | pass |
| 新 run 默认 `linear` | pass |
| 旧 run 缺 engine 时 status、continue、finish、recover 兼容 | pass |
| engine 不匹配在写 artifact 前拒绝 | pass |
| handler 不依赖 LangGraph | pass |
| 不复制业务模型 | pass |
| 不改变 linear 成功语义 | pass |
| 独立 reviewer 无 Blocker / High | pass |

最终结论：

```text
Gate 1 = pass
Gate 2 = ready, not started
```
