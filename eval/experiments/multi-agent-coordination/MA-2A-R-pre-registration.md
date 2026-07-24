# MA-2A-R 运行时事实绑定修复预注册

> 冻结日期：2026-07-24<br>
> Gate：`MA-2A-R`<br>
> 分支：`experiment/ma2a-runtime-binding-repair`<br>
> 状态：`pre-registered / implementation not started`<br>
> 默认产品行为：不变<br>
> 真实 Planner / Provider：禁止

## 1. 冻结输入

- `baseline_commit`：`c83aa0520339c0e42964b1060c478c0b1d07b428`
- 前序 Gate：`MA-2A = inconclusive`
- 前序决策：
  `eval/experiments/multi-agent-coordination/MA-2A-decision.md`
- 前序决策 SHA-256：
  `b2e3ab11cac2e3c74454dee1d8c4e7d322b09bf97a86dc8e3600cedd6e667a66`
- 独立复审探针：
  `eval/experiments/multi-agent-coordination/ma2a_independent_review_probe.py`
- 独立复审探针 SHA-256：
  `8b20242c5ab02add6325beb225163c87d8d6cf96cb7a24bd8fae01e36dbb3081`
- 研究合同：
  `docs/experiments/multi-agent-coordination/RESEARCH-AND-EXPERIMENT-PLAN.md`
- 研究合同 SHA-256：
  `b9aa7d0e577b468aebc3b69e1eb3f5da70f8d6472d87d092bec61997aa6ed92a`
- 本 Gate 红灯测试：
  `tests/test_delegation_runtime_binding_repair.py`
- 红灯测试 SHA-256：
  `48dc205dfce864e404609f79a23216475a08a382eea836c29e6c78f3a667ffe6`
- task-pack：不适用；只使用测试内本地 Git fixture 和 run-owned JSON artifact
- Worker backend：只允许测试注入的 fake Worker

以上 SHA-256 均按 Git/LF 规范计算：将工作树 CRLF 规范化为 LF，以 UTF-8 无 BOM 字节计算。

本 Gate 从前序 `inconclusive` 决策提交建立，不修改、删除或重新表述既有
`MA-2A-pre-registration.md`、`MA-2A-decision.md` 与独立复审证据。实现期间不 rebase、
不合并 `main`，不借主线变化改变冻结变量。

## 2. 唯一研究问题

在不调用真实 Provider、不改变默认产品成功语义的前提下，Vega 能否从当前真实 Git
workspace、受 Git 跟踪的项目配置和 run-owned task、route policy、input artifact 编译
权威委派 Context，并让单 Slice bridge：

1. 在 Worker 启动前完成 Plan、Context、HEAD、workspace、policy、scope、verification 与
   shell identity 绑定；
2. 向 Worker 只发送由冻结 Plan 确定性编译的 prompt；
3. 在 Worker 返回后发现任何控制面篡改、越界修改或新增文件预算超限；
4. 只接受与当前 Plan、Slice、Context、before/after snapshot、scope policy、命令和 shell
   identity 完整绑定的 scope / verification artifact；
5. 形成可被独立交叉验证的 `DelegationAttempt`，但仍不把 Attempt 当作产品成功证据？

本 Gate 不评价 Planner 质量、真实 Worker 成功率、模型成本、多 Worker 收益、A2A、自动
replan 或 LangGraph 编排收益。

## 3. 允许实现范围

### 3.1 权威 Context source

允许新增最小、严格的 `DelegationContextSource`，它只能声明 run-owned 相对路径：

```yaml
schema_version: 1
task_artifact_path: tasks/TASK-MA2AR.json
delegation_policy_path: policies/delegation-policy.json
input_artifact_paths:
  - inputs/design.json
```

路径只负责定位，不携带 task identity、hash、route、scope、verification 或 workspace
结论。编译器必须重新读取、严格解析并计算实际内容哈希。

允许新增：

- run-owned task artifact schema；
- run-owned delegation route policy schema；
- `CompiledDelegationContext`；
- `compile_delegation_context()`；
- `delegation-context.json`。

Context 必须从以下实时事实编译：

- 当前 Git HEAD 和完整 workspace fingerprint；
- 当前受 Git 跟踪的 `.vega.yaml` 或 `.vega.yml`；
- 当前 scope 与 verification 配置；
- 当前运行平台的 verification shell kind；
- run-owned task、route policy 与 input artifact 的实际字节哈希。

项目 scope 第一版只支持非空、精确的仓库相对路径。若配置使用当前 MA-1
`DelegationValidationContext` 无法无歧义表达的 glob 或 forbidden pattern，必须
fail-closed，不在本 Gate 扩展 scope 语言。

### 3.2 控制面与 prompt

允许新增：

- `delegation-prompt.json`；
- `DelegationAttempt.context_ref`；
- `DelegationAttempt.prompt_ref`；
- Worker 前后控制面哈希复核。

Worker prompt 必须由 PlanContract 和选定 Slice 确定性编译。运行时不得接受调用方提供的
任意 prompt，也不得把完整 Worker 对话、隐藏推理或 Provider trace 写入 artifact。

Worker 启动前必须冻结并在返回后复核：

- task、route policy 与 input artifacts；
- Plan；
- Compiled Context；
- readiness；
- before snapshot；
- prompt identity。

任何文件缺失、替换或字节变化都必须阻断 Attempt。

### 3.3 Probe 与 Attempt

scope 和 verification probe 仍只允许显式注入，但 artifact 必须严格绑定：

- `plan_id`、`slice_id`；
- Plan 与 Context 规范化哈希；
- before/after workspace fingerprint；
- scope policy hash；
- allowed write paths 或 verification commands；
- verification shell kind 与 oracle。

只含 `status=passed` 的 artifact 必须无效。Attempt validator 必须交叉验证
`readiness.plan_sha256`、`readiness.context_sha256`、Context、prompt、snapshot 与两个
probe artifact，不能只验证单文件自身格式和引用哈希。

### 3.4 新文件与 Reviewer

`max_new_files` 必须同时覆盖：

- 新增 untracked 文件；
- 新增后被 `git add` 的 tracked 文件。

Reviewer 的 delegation summary 继续只允许五个受控字段，但必须五项全部合法才输出；任一
缺失或不合法时整体返回空对象。

## 4. 禁止范围

本 Gate 禁止：

- 修改公开 `main` 或把实验分支整体合入主线；
- 调用真实 Planner、Worker、Provider、网络模型、Provider adapter 或原生子 Agent；
- 接入默认 CLI、Loop、Finish、Goal、Reviewer 调度或产品成功路径；
- 多 Slice 调度、并发 Worker、重试、模型升级、自动 replan、mailbox、A2A 或长期 Memory；
- 建立第二套 execution artifact、PID、heartbeat、deadline 或恢复状态机；
- 把 readiness、Worker 退出、Attempt、probe status 或 reviewer approve 当作 verification /
  Assurance 成功；
- 让 Plan、Worker 或 Reviewer 自报 task、policy、scope、workspace、route 或 verification
  权威事实；
- 允许 run control directory 与目标 repo 重叠；
- 通过修改断言、`xfail`、skip、特殊占位值、测试环境识别或后门分支让红灯转绿；
- 修改既有前序 Gate 的 `eval/` 记录。

旧 `tests/test_delegation_runtime_bridge.py` 可以在本 Gate 实现阶段迁移到严格 Context source
API，但不得删除其安全语义覆盖。前序 Git 提交中的冻结版本继续作为 MA-2A 历史证据；本
Gate 的正向结论只由新的冻结红灯和最终完整回归共同决定。

## 5. 红灯验收条件

本 Gate 冻结 9 个测试节点：

1. 真实 Context 编译后，两个独立 run 对同一 Plan 产生相同 Worker prompt 与 prompt
   artifact；Attempt 同时包含 `context_ref` 和 `prompt_ref`。
2. Plan 编译后 workspace 发生变化，Worker 调用次数为零。
3. task artifact 缺失或不可读，Worker 调用次数为零。
4. 只有 `status=passed` 的 verification artifact 不能形成 Attempt。
5. Worker 改写 Plan 等控制面 artifact 时必须阻断 Attempt。
6. 新文件创建后即使被 `git add`，仍计入 `max_new_files`。
7. Attempt validator 必须发现 readiness 的 Plan hash 被语义替换，即使引用哈希同步更新。
8. Reviewer 的残缺 delegation summary 必须整体为空。
9. run control directory 与目标 repo 重叠时，Worker 调用次数为零。

实现不得减少以上节点，也不得把失败路径变成只记录 warning 后继续。

## 6. 冻结红灯观察

在 `baseline_commit` 上加入冻结测试、未新增生产实现时执行：

```powershell
python -m pytest tests/test_delegation_runtime_binding_repair.py -q --tb=short
```

实际结果：

```text
9 collected
0 passed
0 skipped
9 failed
```

失败分布：

- 8 个节点因 `DelegationContextSource`、`compile_delegation_context` 与新 bridge API 尚不存在
  而失败；
- 1 个节点证明当前 reviewer 会接受只有 `plan_id` 的残缺 delegation summary；
- 未使用 `xfail`、skip、真实网络、真实 Provider、默认 Runner 或时间等待。

加入红灯测试后，完整收集数为：

```text
669 tests collected
```

## 7. Fail-closed 停止线

出现以下任一情况时，当前 Gate 只能 `reject` 或 `inconclusive`：

- 必须接受调用方注入的完整 Validation Context 才能保持成功路径；
- 必须把虚构 snapshot、通配占位哈希或 pytest 环境识别当作权威事实；
- 无法在 Worker 启动前完成真实 Context、workspace 与 policy 编译；
- 无法在 Worker 返回后发现控制面字节变化；
- probe artifact 无法绑定当前 Plan、Context、snapshot、命令和 shell；
- staged 新文件无法在不修改 Git index 的前提下被识别；
- 需要复制或替换现有 `execution.json`；
- 需要放松 scope、verification、reviewer 隔离、Assurance 或产品成功语义；
- 测试必须依赖真实模型、网络、非确定性输出或超过 60 秒等待；
- 必须同时实现多 Worker、A2A、retry 或 replan 才能证明单 Slice bridge。

## 8. 验证要求

最终至少执行：

```powershell
python -m compileall src scripts/check_repository_hygiene.py
python -m pytest tests/test_delegation_runtime_binding_repair.py
python -m pytest tests/test_delegation_runtime_bridge.py
python -m pytest tests/test_delegation_contract.py
python -m pytest tests/test_execution_control_safety.py tests/test_context_boundaries.py
python -m pytest tests/test_assurance_verification_semantics.py
python scripts/check_repository_hygiene.py --base-ref origin/main
ruff check src tests scripts/check_repository_hygiene.py
git diff --check
```

完整 pytest 若超过 60 秒，必须按测试文件或完整 node id 分片；每个分片使用独立
`.tmp/pytest/runs/<name>` 与 cache。超时、未收集和被终止均不计入通过。

## 9. Gate 结论口径

- `accept`：9 个冻结红灯全部转绿；旧安全语义完整迁移；真实 Context、prompt、控制面、
  probe、Attempt 与新增文件预算全部绑定；专项、完整回归、静态、卫生和远端 CI 全绿。
- `reject`：实现必须违反禁止范围或 fail-closed 停止线，或仍存在 stale fact、控制面篡改、
  未绑定 probe、预算绕过或成功语义放松。
- `inconclusive`：冻结接口、测试环境或现有 artifact 无法在不改变研究变量的情况下给出
  可复核结论。

即使 `accept`，也只表示单 Slice 事实绑定桥接具备进入下一次独立预注册的条件。它不授权
直接调用真实 Provider，不证明低成本 Worker 有收益，也不授权多 Worker 或 A2A。
