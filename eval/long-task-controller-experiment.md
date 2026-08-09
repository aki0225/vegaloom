# Long Task Controller 单 Checkpoint 实验

> 日期：2026-08-09
> 分支：`codex/long-task-controller-experiment`
> 当前结论：`single-checkpoint-real-model-pass / parent-reconcile-gap`
> 机制阶段结论（真实运行前）：`mechanism-pass / real-model-unverified`

## 假设

在不引入后台服务、数据库或新的 Agent 框架的前提下，Goal 可以把一个明确 checkpoint
交给现有 auto loop，并满足：

1. Goal 在 child 创建后立即记录 child run 身份。
2. Worker、Verification 和 Reviewer 的安全阶段事件可通过 run-local `progress.jsonl` 查看。
3. child 成功且证据资格通过时，Goal 停在 `checkpoint_done`。
4. child 状态损坏、失败或证据不足时，Goal 保留现场并停在 `needs_human`。
5. 不自动串联下一 checkpoint，不自动 commit、push、回滚或写长期 Memory。

## 实验设置

- 使用仓库内 pytest 临时目录创建可丢弃 Git repo。
- 使用 fake Worker 修改一个 tracked `README.md`。
- 使用真实 `LoopAutomationRuntime` 执行 workspace-check、确定性 verification、risk gate、
  Reflect、独立 fake Reviewer 和 loop eval。
- verification 命令为固定的本地 Python 成功命令。
- 另建损坏 child state 场景，验证 Goal 是否 fail-closed。
- 在任务与 Runner command 中放入 fake key，检查全部 run artifacts。

该设置验证编排和证据合同，不验证模型的代码能力。

## 结果

### 成功路径

- child loop 状态为 `success`。
- Goal 记录 `active_child_run`，child 结束后清空该字段并保存 `last_child_run`。
- checkpoint 证据通过 repo、kind、artifact integrity、verification 和新鲜度校验。
- Goal 最终状态为 `checkpoint_done`。
- `progress.jsonl` 包含 child 创建、Worker、Reviewer、child 结束和 checkpoint 完成事件。

### 失败路径

- child `state.json` 损坏时，Goal 不会停留在模糊的 `running`。
- Goal 最终状态为 `needs_human/checkpoint_blocked`。
- `checkpoint-blocked.md`、Goal state、trace 和 progress 均完整保留。
- 不自动重试、回滚或进入下一 checkpoint。

### 安全与兼容

- fake key 未出现在 Goal 或 child run artifacts。
- `max_checkpoints > 1` 被拒绝。
- 缺少 `progress.jsonl` 的旧 run 仍可使用 `vega watch`。
- 并发读取遇到尚未写完的最后一行时，watch 忽略该尾行；中间损坏行仍然报错。

### 本地验证

- 新增长任务与 CLI 进度专项：`9 passed`。
- 运行安全相关专项：`2 passed`。
- 既有 Goal P0 smoke：`17 passed`。
- Goal 证据身份、新鲜度和防篡改：`16 passed`。
- 合计：`44 passed`。

两次较大的并行测试组合因本机资源争用超过 60 秒，没有被记为通过；改用更小的完整 node
集合后，上述相关测试均得到明确通过结果。

## 能证明什么

- 单 checkpoint 控制器可以复用现有 auto loop，而不创建第二套执行与证据系统。
- Goal 能解释 child 在哪里、当前为何停止、应由谁接管。
- 安全进度能降低“完全黑盒”的体验，同时不暴露模型正文、内部推理或原始工具参数。
- child 证据损坏时会 fail-closed，不会把未知状态升级为成功。

## 机制阶段当时不能证明什么

- 未运行 P1 真实 Codex 模型实验。
- 未证明模型能稳定完成数小时或数天任务。
- 未证明自动 checkpoint 比人工拆分多个普通 loop 更省 Token 或成功率更高。
- 未实现多 checkpoint 自动串联、Goal wall-clock timeout 或原模型会话自动重连。

## 真实模型运行前门槛

只有在一个真实但可丢弃的目标仓库上预先固定以下内容后，才进行 P1 真实模型实验：

1. 一个 15 至 30 分钟可完成的单 checkpoint。
2. 精确允许路径、最大文件数、最大 diff 和固定验证命令。
3. Worker/Reviewer Runner 配置与超时。
4. 成功、停止和人工接管条件。
5. 禁止按运行结果临时扩大任务或重跑。

该机制阶段要求真实实验结束后再决定 P1 是否值得保留；当时不进入 `main`。

## P1 真实 Codex 追加记录

> 日期：2026-08-09
> 结论更新：`single-checkpoint-real-model-pass / parent-reconcile-gap`

本轮在仓库内 `.tmp/long-task-controller-real-20260809/` 创建可丢弃目标仓库，预注册一个
Decimal 退款比例分摊任务。任务只允许修改 `settlement/allocator.py`，固定运行 8 个测试，
不允许修改测试、配置或依赖。

### 第一次运行

- Goal：`20260809-113454-goal`
- child：`20260809-113522-002019-feature-loop`
- Worker 成功实现任务，固定验证为 `8 passed`。
- Python 运行产生的 ignored 文件改变了 workspace fingerprint。
- Vega 按 fail-closed 规则停在 `needs_human/workspace_check_failed`，未把代码结果误报为成功。

### 第二次运行

- Goal：`20260809-114030-goal`
- child：`20260809-114053-200351-feature-loop`
- 新目标副本触发 Git `safe.directory` 所有权保护。
- Worker 未产生 tracked diff，Runner 返回失败。
- Goal 保留 child 身份和阻塞证据，停在 `needs_human/checkpoint_blocked`。

### 第三次运行与恢复

- Goal：`20260809-122345-goal`
- child：`20260809-122407-371255-feature-loop`
- Worker 成功，workspace-check、pre/post verification scope gate 与固定 `8 passed` 均通过。
- 首次 Reflect 因 Windows CRLF 被误判为 trailing whitespace，child 停在
  `needs_human/reflect_failed`，Reviewer 未启动。
- 修复 `git diff --check` 的 CRLF 输入语义后，使用 `vega loop continue` 继续同一 child，
  没有创建第四个 Goal，也没有覆盖第 1 个 iteration。
- 第 2 个 iteration 再次通过 workspace-check、三阶段 scope gate、固定 `8 passed`、
  Reflect 与 low-risk Gate。
- 独立 Reviewer 返回 `approve`，findings 为 0；child 最终为 `success/done`。

### 真实结论

本轮能够证明：

- 一个真实 Codex Worker 可以在单 checkpoint 边界内完成受限修改。
- child 在确定性 Reflect 失败后可以保留原始证据，并由 `loop continue` 进入新 iteration。
- 真实运行的 `progress.jsonl` 能看到 verification 与 Reviewer 回合；该运行也暴露了缺少
  loop 终态事件的问题，随后已补充安全 `run_finished` 事件并增加回归测试。
- scope、verification、Reflect、Risk Gate、Reviewer 与 loop eval 能在恢复路径上重新绑定。

本轮仍不能证明：

- 父 Goal 在 child 先失败、后由人工继续成功时会自动重新归档。当前父 Goal 保留最初的
  `checkpoint_blocked`，不会轮询或自动改写历史证据。
- 多 checkpoint 会自动串联，或数小时、数天任务可以无人值守完成。
- 单次成功足以证明比人工拆分普通 loop 更省 Token 或更高成功率。

父 Goal 的重新归档应作为独立后续问题评估。当前实验不通过轮询、后台服务或自动重试来
掩盖该边界。

## 安全修复与验证追加

真实运行后的独立审查又发现并修复了三个问题：

1. Goal 的状态变更原先没有共享 run lock，`complete/stop/run` 并发时可能互相覆盖。
2. `watch` 原先信任被篡改的 `progress.jsonl`，读取端没有再次白名单化和脱敏。
3. `watch --follow` 原先在 run 进入终态后直接退出，没有打印最终 status/step。
4. `progress.jsonl` 原先没有拒绝预置 hardlink，追加写入可能越过 run 边界。

修复后，Goal mutator 共享同一个 `RunMutationLock`；`goal run` 持锁期间不会被其他 Goal
写操作覆盖。进度读取只保留已知 step/event/status 和受限元数据，普通文本与 JSON 输出
都不会返回未知字段、ANSI 控制序列或未识别的 run token。进度追加使用安全文件打开并
拒绝 symlink、junction/reparse point 与 hardlink。loop 的 `success`、`failed` 和
`needs_human` 终态会追加安全 `run_finished` 事件，follow 模式同时打印最终 status/step。

本地验证结果：

- 长任务控制器与 watch：`10 passed`，按 `6 + 4` 两个完整 node 分片运行。
- Reflect 终态进度与 CRLF diff-check：`4 passed`。
- 既有 Goal P0 生命周期：`17 passed`，按 `5 + 4 + 4 + 4` 分片运行。
- Goal 证据新鲜度与完成语义：`17 passed`，按 `4 + 4 + 4 + 5` 分片运行。
- 架构增长与依赖方向：`42 passed`。
- 完整测试节点收集：`1085 collected`。
- `compileall`、Ruff、仓库卫生检查与 `git diff --check`：通过。

本机没有把超过 60 秒的组合命令记为通过；完整执行交由 PR CI 的既有分片完成。

## 显式 Reconcile 与跨进程恢复追加

> 日期：2026-08-09
> 结论更新：`single-checkpoint-reconcile-pass / multi-hour-control-mechanism-pass`

父 Goal 现为每个自动 checkpoint 持久化唯一 `bound_child_run` 和
`runner_timeout_seconds`。`goal reconcile` 只读取这个 child，并按固定锁顺序持有父 Goal
与 child lifecycle lock；它不启动新 Worker、不替换 child、不自动重试或回滚。

新增跨进程测试执行了以下故障链：

1. 独立控制进程创建父 Goal、child loop 和 owned worker 子进程。
2. worker 写入部分修改后保持运行，测试强制终止控制进程。
3. owned worker 仍存活时，父 Goal `recover` 被拒绝，`reconcile` 只保持等待状态。
4. worker 退出后，父 Goal 记录 recovery；child 进入 `child_recovery_required`。
5. 对同一个 child 执行普通 loop recovery，并在第 2 个 iteration 完成 Worker、固定验证和
   独立 Reviewer。
6. 父 Goal 再次 reconcile，刷新原 child 的证据并进入 `checkpoint_done`。

测试同时确认：

- checkpoint 只绑定一个 child，恢复后没有创建替代 run。
- 重复 reconcile 不追加重复 evidence ref。
- 单次 runner timeout 的 deadline 精确记录为 3600 秒。
- timeout 范围外的 59 和 3601 秒被拒绝。
- fake key 不进入 Goal 或 child artifacts。

该结果能够证明 Vega 的控制状态、进度、进程所有权和证据可以跨 CLI 中断恢复，并允许一个
checkpoint 由最多五轮、每次最长一小时的 Worker/Reviewer 调用组成。因此“数小时任务”的
控制机制成立。

该结果不能证明外部模型已经真实连续运行数小时，也不能证明无人值守多 checkpoint 自治。
真实模型长时稳定性仍需要单独的长时间 dogfood，不能由虚拟 deadline 或短时故障测试替代。

## 真实控制进程中断 Dogfood 追加

> 日期：2026-08-09
> Goal：`20260809-173526-goal`
> child：`20260809-173624-626164-feature-loop`
> 正式裁决：`reject`
> 机制附加判断：`fail-closed-mechanism-pass`

本轮在 `.tmp/dogfood/20260809-echo-ai-api-style-r3/target-repo` 的可丢弃副本中执行一个真实
配置契约修复任务。目标是统一 `AI_API_STYLE=chat_completions` 的运行时语义，允许修改范围、
文件数量、Diff 预算和四条确定性验证命令均在运行前固定。真实项目目录未被修改，目标副本
禁止 push。

### 故障注入与恢复过程

1. 父 Goal 创建唯一 child，并启动真实 Codex Worker。
2. Worker 尚未形成 tracked diff 时，父控制进程被中断。
3. child Worker 短暂继续存活，随后退出；父 Goal 首次 reconcile 正确进入
   `child_recovery_required`，没有创建替代 child。
4. 对同一个 child 执行 recover 和 `loop continue`。
5. 恢复后的第 2 轮没有重新运行 Worker，而是把 Worker 标记为 `skipped`，直接对原始基线
   执行 workspace-check、scope gate 和全部固定验证。
6. 验证通过后，Vega 发现没有 tracked diff，按 `needs_human/no_diff` 停止；Reviewer 未启动。
7. 父 Goal 再次 reconcile，最终停在 `needs_human/checkpoint_blocked`。

### 观察结果

- child 恢复全程复用同一个 run，替代 child 数量为 `0`。
- 后端固定测试为 `123 passed`，耗时 `754.39s`。
- 前端测试为 `178 passed`，前端构建和 `git diff --check` 通过。
- child 从创建到终态约 `15m18s`，父 Goal 从创建到最终 reconcile 约 `18m22s`。
- tracked diff 数量为 `0`，有效 Worker 完成次数为 `0`，Reviewer 调用次数为 `0`。
- Goal、child 和 continue 相关进程在实验结束后均已退出。
- run artifacts 未发现环境 API key、`sk-` 凭据或 Bearer token。

预注册任务文字写了“完整后端测试”，但实际机器策略执行的是相关后端测试集合，共
`123` 项。该差异属于实验协议文字缺陷，不能事后把本轮改写成“完整后端测试已通过”。

### 能证明什么

- 控制进程中断后，Vega 能保留唯一 child 身份、进程所有权、恢复记录和完整终态证据。
- Worker 未产生可信结果时，Vega 没有误报成功，也没有启动 Reviewer 审查空 Diff。
- 父 Goal 能把 child 的 `needs_human` 终态重新归档为 `checkpoint_blocked`。
- 敏感信息、目标仓库边界和禁止 push 约束在本轮保持有效。

### 暴露的产品缺口

当 `interrupted_step=worker` 且恢复时没有 tracked diff，当前 `loop continue` 会跳过 Worker，
直接进入昂贵验证。这在安全上是 fail-closed，但在长任务恢复上没有完成原 checkpoint，
并浪费了约 13 分钟验证时间。

下一次实现只应处理这一条恢复决策：

- 明确重新运行 Worker；或者
- 在任何昂贵验证前停止，并要求人工明确选择重新运行 Worker 或接受当前工作区。

在该决策被实现并通过新的真实中断 dogfood 前，不增加自动多 checkpoint、后台 daemon、
自动重试策略或新的编排框架。

### 最终裁决

本轮满足 `fail-closed-mechanism-pass`：控制与证据机制在故障后保持安全、可追溯。

本轮不满足 `candidate-for-opt-in`：checkpoint 没有完成，恢复没有重新获得 Worker 产物，
也没有证明比人工重新启动普通 loop 更可靠或更省成本。因此保留现有实验入口，但不提升为
默认能力、正式长任务模式或多 checkpoint 自动编排。
