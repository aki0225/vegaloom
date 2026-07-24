# MA-2A-R 运行时事实绑定修复 Gate 决策

> 决策日期：2026-07-24<br>
> Gate：`MA-2A-R`<br>
> 分支：`experiment/ma2a-runtime-binding-repair`<br>
> 冻结基线：`c83aa0520339c0e42964b1060c478c0b1d07b428`<br>
> 预注册与冻结红灯：`18c80e34af364806e893fb796c7ac443908ae0ac`<br>
> 最终被评估实现：`9b4c34336911abcf6bb27af7baa9e131d79791e7`<br>
> 决策：`accept`<br>
> 后续授权：`MA-2B pre-registration authorized / implementation not authorized`

## 一、决策摘要

`MA-2A-R` 接受。

本 Gate 已经关闭前序 `MA-2A = inconclusive` 暴露的事实绑定缺口，并证明：

> Vega 可以在不调用真实 Provider、不接入默认产品路径的前提下，从当前 Git workspace、
> 受跟踪项目配置和 run-owned artifacts 编译权威委派 Context；单 Slice bridge 可以把
> Plan、Context、prompt、workspace、policy、scope、verification 与 Attempt 绑定，并在
> 关键事实缺失、过期、被篡改或发生工作区漂移时 fail-closed。

该结论只接受“单 Slice 委派事实绑定桥接”这一项能力，不表示：

- budget Worker 已经带来质量、成本或速度收益；
- LangGraph、多 Worker、A2A、retry 或自动 replan 已经有价值；
- Attempt、probe 或 reviewer 结论可以替代产品确定性验证；
- 当前实验分支可以整体合入 `main`。

因此，下一步只授权起草独立的 `MA-2B` 预注册。真实 Provider 调用、task-pack 执行和
Planner × Worker Pilot 仍未获授权。

## 二、冻结条件逐项裁决

| 冻结条件 | 结果 | 关键证据 |
|---|---|---|
| 真实 Context、确定性 prompt 与 Attempt 引用绑定 | 通过 | `DelegationContextSource`、`CompiledDelegationContext`、`delegation-context.json`、`delegation-prompt.json`、`context_ref` 与 `prompt_ref` 已形成闭包 |
| Plan 编译后 workspace 漂移时 Worker 不启动 | 通过 | Worker 启动前重新捕获并比较当前 workspace；漂移返回 `workspace_changed_before_worker` |
| task artifact 缺失或不可读时 Worker 不启动 | 通过 | Context 编译只读取 run-owned 权威路径，缺失、逃逸、链接或非普通文件均阻断 |
| 只含 `status=passed` 的 probe artifact 无效 | 通过 | scope / verification artifact 必须绑定 Plan、Slice、Context、前后 snapshot、policy、命令、shell 与 oracle |
| Worker 改写控制面时 Attempt 不成立 | 通过 | Worker 前冻结 Plan、Context、readiness、prompt 与 source artifacts，Worker 返回后逐项复核 |
| staged 新文件计入 `max_new_files` | 通过 | 新文件预算同时覆盖 untracked 和新增后进入 index 的 tracked 文件 |
| Attempt validator 交叉验证 readiness 哈希 | 通过 | `readiness.plan_sha256` 与 `readiness.context_sha256` 必须匹配实际规范化内容 |
| Reviewer delegation summary 全有或全无 | 通过 | 五个受控字段任一缺失或不合法时，整个摘要返回空对象 |
| run control directory 与目标 repo 重叠时不启动 Worker | 通过 | 编译阶段与 bridge 入口均执行路径重叠检查 |

实现后又补充了四个未改变冻结研究问题的安全加固节点：

1. 控制面冻结后、Worker 真正启动前再次核对 workspace；
2. run policy 不能放宽项目 `.vega.yaml` 的预算；
3. scope probe 返回后发现 workspace 漂移时不记录 Attempt；
4. verification probe 返回后发现 workspace 漂移时不记录 Attempt。

这些加固缩小了关键检查之间的 TOCTOU 窗口，没有引入目标仓库全局锁、第二套执行状态机或
新的产品成功路径。

## 三、实现边界

最终实现保持了预注册边界：

- 仍然只支持一个预先冻结的 Slice；
- Worker、scope probe 和 verification probe 仍由测试显式注入；
- Worker prompt 只从 PlanContract 与选定 Slice 确定性编译；
- Attempt 继续引用现有 `executions/worker/execution.json`，没有复制 PID、heartbeat、
  deadline 或恢复状态；
- bridge 只返回 `blocked` 或 `attempt_recorded`；
- 默认 CLI、Loop、Finish、Goal、Reviewer 调度与 Assurance 成功路径均未接入；
- 没有真实模型、网络 Provider、原生子 Agent、多 Worker、A2A、retry、replan 或长期
  Memory。

远端环境曾因 Ruff 新版本自动启用项目历史上未纳入门禁的 `B008`、`I001`、`FLY002`、
`DTZ005` 而产生工具默认值漂移。最终在 `pyproject.toml` 显式冻结既有门禁：

```toml
[tool.ruff.lint]
select = ["E4", "E7", "E9", "F"]
```

该修改把原有隐式规则变成版本化配置，没有为本 Gate 新增代码豁免，也没有修改无关历史代码
来伪造全绿。

## 四、验证证据

### 4.1 远端权威闭环

最终远端 CI：

```text
Actions run：30067501824
Commit：9b4c34336911abcf6bb27af7baa9e131d79791e7
Branch：release/ma2ar-ci-20260724
Result：11/11 jobs success
```

运行详情：
`https://github.com/aki0225/vegaloom/actions/runs/30067501824`

远端实际覆盖：

- 静态检查、仓库卫生与 `673` 个测试节点收集；
- Python 3.11 全量测试；
- Python 3.12 全部分片；
- 独立 `delegation-binding` 分片；
- Windows 专项与 wheel smoke；
- POSIX 专项；
- wheel 构建与安装。

其中 `delegation-binding` 分片明确执行
`tests/test_delegation_runtime_binding_repair.py`，所以九个冻结节点已在最终被评估提交上
完整通过。

### 4.2 本地 Windows 证据

本地专项通过：

```text
tests/test_delegation_contract.py：49 passed
tests/test_execution_control_safety.py：15 passed
tests/test_context_boundaries.py：36 passed
新增四项 hardening：全部通过
```

九个冻结节点与四个新增加固节点逐节点执行的结果为：

```text
13 nodes
12 passed
1 timed out
0 assertion failures
```

唯一超时节点：

```text
tests/test_delegation_runtime_binding_repair.py::
test_live_compiled_context_records_bound_attempt_and_prompt
```

该节点在当前本地 Windows 文件系统环境中超过 `58s`，因此本地结果不得表述为全绿。完整
本地分片还发现多个既有 Assurance 节点在同一环境中单节点连续超过 `58s`，该轮已主动终止并
记录为非通过证据。

本 Gate 的完整回归结论来自最终远端 CI，而不是把本地超时当作通过。冻结测试不包含真实网络、
模型调用或显式长等待；同一节点已在远端 `delegation-binding` 分片实际通过。因此该本地
性能差异不构成语义失败，但仍作为 Windows 本地验证成本风险保留。

### 4.3 最终静态与卫生检查

在最终实现提交上已确认：

```text
compileall：passed
repository hygiene --base-ref origin/main：passed
pytest collect-only：673 tests collected
Ruff：远端 passed
```

本地验证产物仅保存在被 Git 忽略的 `.local-validation/` 与 `.tmp/`，未进入提交。

## 五、仍然存在的边界

### 1. 没有目标仓库 OS 级原子锁

bridge 会在 Worker 与两个 probe 的关键边界重新捕获 workspace，能够发现已发生的漂移，
但无法让外部进程在所有检查与写入之间绝对停止。最终复核缩小了 TOCTOU 窗口，不等于提供
文件系统事务或全局锁。

### 2. Probe 写入是事后检测，不是权限层禁止

scope / verification probe 若错误修改 workspace，bridge 会阻断 Attempt 并保留失败事实，
但当前注入接口本身不是操作系统只读 sandbox。是否需要更强隔离，必须由后续真实运行证据
决定。

### 3. 尚未进入默认产品路径

该 runtime 仍是实验 API。没有证据表明用户应在日常 `vega do`、Loop、Finish 或 Goal 中
承担这些新增 artifact 和恢复成本。

### 4. 尚未验证模型经济性

fake Worker 只能证明 harness 的事实绑定和 fail-closed 语义，不能证明 premium Planner
可以让 budget Worker 达到可接受质量，也不能证明总 token、费用、延迟或人工步骤下降。

## 六、最终裁决与下一步

`MA-2A-R = accept`。

该结论授权：

1. 以本 Gate 的接受提交为新实验输入；
2. 起草独立、可审阅的 `MA-2B` 预注册；
3. 在预注册中冻结 task-pack、ground truth、Provider / model manifest、A/B/C treatment、
   预算、随机顺序、worktree 隔离、评价指标和停止线。

该结论不授权：

- 在预注册接受前使用 API key 或启动任何真实模型调用；
- 直接实现多 Worker、原生子 Agent treatment、A2A、自动 retry 或 replan；
- 修改 `main`、rebase 当前实验或整体合并本分支；
- 把本 Gate 包装成 LangGraph 或低成本 Worker 已产生业务增益。

下一步必须先形成 `MA-2B-pre-registration.md`，经独立复审或 owner 明确接受后，才能决定是否
执行真实 Planner × Worker Pilot。
