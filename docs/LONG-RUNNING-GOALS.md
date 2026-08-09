# 长任务 Goal Loop 设计

> 状态：P0 人工状态层已实现；P1 单 checkpoint 自动推进与显式 child reconcile 已通过
> PR `#54` 进入主线。主线仍只包含单 checkpoint 控制；显式 `--rerun-worker` 继续位于
> 实验分支。r6 已真实通过同一 child 的显式重跑路径，但后续代码审阅发现 ignored partial
> work 与同路径 tracked 内容变化两项 P1，当前分支已用每轮 Worker baseline 和结构化重跑
> 授权修复。该能力在分支验证完成并获得后续观察前不提升为默认能力。多 checkpoint 自动
> 串联、真实模型连续运行数小时/跨天的稳定性和收益仍未证明。

## 1. 背景

当前 Vega 已经能覆盖一类边界清晰的研发闭环：

```text
brief / do / loop -> worker -> workspace-check -> verification -> reflect -> review -> finish
```

这对小 bug、小功能和一次性 review 足够，但真实研发里还有另一类任务：

- 跨多个模块的重构。
- 一个需求拆成多个阶段逐步落地。
- 需要长时间验证、反复 review、分批修改的大目标。
- 中途可能断开、超时、暂停、换人接手。

这类任务不能简单用“跑得久就是失控”来判断。真正危险的是：没有目标边界、没有停止条件、没有 checkpoint、没有证据和恢复路径。

所以 Vega 需要把“长任务”定义成一组可恢复、可审查、可验证的小闭环，而不是让一个 worker 无限执行。

## 2. 设计参考

可以参考 Codex `/goal` 类能力的思想：

- 先定义一个 durable objective，而不是只给一句临时指令。
- 明确不做什么，避免模型自由扩张 scope。
- 明确怎么验证，避免主观判断完成。
- 明确什么时候停止，避免无限循环。
- 长任务过程中保留 progress / checkpoint，支持恢复和人工接管。

Vega 不需要复制一个重型 `/goal` 系统。更合适的取舍是：

```text
goal contract -> checkpoint plan -> 多个普通 loop -> eval/review/gate -> checkpoint report -> 下一 checkpoint
```

也就是说，长任务不是一个超长 loop，而是多个可追溯 checkpoint 串起来。

## 3. 核心原则

### 3.1 长任务不是失控

长任务可以合法运行很久，只要它满足：

- 有明确 goal contract。
- 有阶段性 checkpoint。
- 每个 checkpoint 有预算和验证。
- 每个 checkpoint 结束后能 review/gate。
- 能暂停、恢复、停止。
- 能解释当前为什么还没结束。

### 3.2 停止不是失败

Vega 里的停止动作应该表示：

```text
停止继续调度新的外部动作 + 记录原因和现场 + 交还给人
```

停止不等于：

- 自动回滚。
- 自动删除文件。
- 自动 kill 进程。
- 自动判定任务失败。

这能保持 runtime 安全：它不替用户做不可逆判断，只负责防止继续扩大影响。

### 3.3 恢复不是继续乱跑

恢复只恢复 Vega 状态机，不直接恢复外部 worker 的上下文。恢复后的默认动作应该是：

1. 展示 recovery report。
2. 要求人检查工作区和产物。
3. 决定是继续、清理、重开，还是停止。

## 4. Goal Contract

长任务开始前，应该固化一个 goal contract。建议字段：

```yaml
objective: 重构 loop 状态机，支持可恢复 checkpoint

non_goals:
  - 不做 Web UI
  - 不引入数据库
  - 不自动 commit
  - 不自动 release

success_conditions:
  - pytest 全部通过
  - ruff 全部通过
  - README 和 ARCHITECTURE 文档更新
  - dogfood eval 覆盖 recover 和 stop 场景

validation:
  commands:
    - python -m ruff check .
    - python -m pytest -q

stop_when:
  - success_conditions 全部满足
  - 连续 2 个 checkpoint 没有实质进展
  - 触发 high risk gate
  - 用户显式 pause 或 stop
  - 当前 checkpoint 超过预算仍无可验证产物

checkpoint_budget:
  max_changed_files: 8
  max_diff_lines: 500
  max_new_files: 4
  max_minutes: 30
  max_iterations: 2
```

当前实现的 `GoalContract` 只保存 objective、repo/input、scope、non-goals 和
success conditions。上例中的 `validation`、`stop_when` 和 `checkpoint_budget` 仍是设计目标，
尚未进入 Goal 状态机或自动调度合同。

这个 contract 的目标是区分四种情况：

| 情况 | 判断方式 | 应对 |
| --- | --- | --- |
| 正常长跑 | 仍在 checkpoint 预算内，有 trace 心跳 | 继续 |
| 超时 | 单个 step 超过 timeout | 停在 needs_human，写 timeout report |
| 断开/陈旧 | state 是 running，但 trace 长时间无更新 | recover 成 needs_human |
| 失控 | 超出 scope/budget 或继续新增无关变更 | stop / needs_human |

## 5. 状态语义

当前实现会主动产生：

```text
created / running / checkpoint_done / paused / stopped / needs_human / success
```

schema 预留、当前 Goal Runtime 不会主动产生：

```text
blocked          缺信息或连续失败，需要人介入
timeout          当前 step 超时，不等于 goal 失败
stale            疑似断开或无心跳，需要 recover
failed           runtime 自身异常或不可恢复错误
```

`stopping` 尚未进入 schema 或 Runtime。当前 P1 的 child 异常、损坏、失败或证据不足统一写为
`needs_human/checkpoint_blocked`；人工 recover 固定写为 `needs_human/recovered`。
`success` 仍必须由人工执行 `goal complete`，并通过 Goal eval 与 checkpoint 证据复核。

## 6. 建议 CLI 形态

P0 只做人工驱动的 goal 状态层，不先做自动长跑：

```powershell
vega goal start --repo . --input goal.md --scope refactor
vega goal status --run <goal_run>
vega goal step --run <goal_run>
vega goal attach --run <goal_run> --checkpoint 01 --ref <child_run> --type loop --note "子 loop 已通过"
vega goal checkpoint-done --run <goal_run> --checkpoint 01 --note "阶段完成"
vega goal complete --run <goal_run> --note "已核对全部 success conditions"
vega goal pause --run <goal_run> --reason "等待需求确认"
vega goal resume --run <goal_run>
vega goal stop --run <goal_run> --reason "方向变化，停止"
vega goal recover --run <goal_run> --reason "CLI 中断"
```

当前 P0 还支持 `--text` 输入短 goal：

```powershell
vega goal start --repo . --text "分阶段整理 runtime 状态机" --scope refactor
```

P1 当前只开放一次有限自动推进：

```powershell
vega goal step --run <goal_run> --text "完成一个边界明确的阶段"
vega goal run --run <goal_run> --max-checkpoints 1 --max-iterations 5 --runner-timeout 3600
```

`goal run` 还支持 `--worker`、`--reviewer`、`--max-iterations 1..5` 和
`--runner-timeout 60..3600`、`--verify/--no-verify`；默认最多两轮 iteration、单次
Worker/Reviewer 最多 900 秒。它没有 `--model` 参数，模型、profile 和推理强度由目标仓库
`.vega.yaml` 与外部 runner 配置决定。

这里的“数小时任务”指一个 checkpoint 可以由多轮 Worker、Reviewer 和确定性验证组成；
每个外部 Worker/Reviewer 调用最多一小时，最多五轮。它不是把单个模型调用无限延长，也不
承诺外部模型在几小时内始终保持目标稳定。

`goal run` 不能无限执行。每个 checkpoint 都必须经过：

```text
checkpoint plan -> loop/worker -> workspace-check -> verification -> reflect -> gate -> review -> checkpoint report
```

如果 gate 是 high risk、验证失败、污染门禁失败、reviewer request_changes 或达到 checkpoint 预算，就停在人工判断。

child loop 创建后，Goal 会把 checkpoint 级 `bound_child_run` 与 Goal 级
`active_child_run` 写入状态。绑定一旦形成就不能替换。另一个终端可以运行：

```powershell
vega watch --run <child_run> --follow
```

`watch` 只展示安全阶段事件、耗时、iteration 和 checkpoint/child run 标识，不展示模型正文、
内部推理、原始命令、命令输出、工具参数或敏感路径。Goal 本身不会恢复外部模型会话；跨会话
恢复依据仍是 Goal/child run artifacts、checkpoint 证据和目标仓库的真实 Git 状态。

控制进程中断后，使用显式核对路径：

```powershell
vega goal reconcile --run <goal_run>
vega recover --run <child_run> --reason "控制进程中断"
vega loop continue --run <child_run> --repo .
vega goal reconcile --run <goal_run>
```

第一次 reconcile 会检查 child 是否仍有存活执行主体。仍存活时只等待；状态仍为 running
但执行主体已消失时，父 Goal 进入 `child_recovery_required`。人工恢复并继续同一个 child
后，第二次 reconcile 会重新验证仓库身份、artifact integrity、verification 和 freshness，
通过后才把原 checkpoint 标记为完成。

## 7. 目录结构草案

```text
runs/<goal_run>/
  state.json
  goal-contract.md
  goal-contract.json
  goal-state.json
  goal-trace.jsonl
  progress.md
  progress.jsonl
  decisions.jsonl
  goal-final-report.md
  goal-eval.md
  checkpoints/
    01/
      checkpoint-plan.md
      checkpoint-evidence.json
      checkpoint-report.md
      checkpoint-blocked.md
      checkpoint-reconcile.md
    02/
      ...
  stop-report.md
  recovery-report.md
```

`checkpoint-blocked.md`、`stop-report.md` 和 `recovery-report.md` 是条件产物。`progress.md`
面向人阅读；`goal-state.json` 面向 runtime；`goal-trace.jsonl` 面向追溯和恢复。
`state.json` 与 `goal-state.json` 保持同内容，用于复用通用 `vega status/latest`。

## 8. 断开、超时、停止的定义

### 8.1 断开

断开是状态和执行现实不一致：

```text
state.json 仍是 running，但 trace.jsonl 长时间没有新事件，且当前 step 没有完成产物。
```

处理方式：

- 不假设失败。
- 不自动清理。
- 先运行 `goal reconcile` 检查绑定 child 是否仍有执行主体。
- 需要记录父控制进程中断时，可运行 `goal recover` 写 `recovery-report.md`；活 child 会阻止
  父 Goal 先行 recover。
- child 执行主体消失后，先恢复并继续 child，再次运行 `goal reconcile`。
- 当前实现标记为 `needs_human/recovered`，不会自动检测或产生 `stale`。
- 不创建替代 child，让用户决定继续、停止或重开。

### 8.2 超时

超时分三层：

| 类型 | 示例 | 应对 |
| --- | --- | --- |
| step timeout | worker/reviewer/verification 单步超过限制 | 停止当前 step，写 timeout report |
| checkpoint timeout | 一个 checkpoint 超过预算 | 停止调度下一步，进入 needs_human |
| goal wall-clock timeout | 整个 goal 超过总预算 | 进入 blocked 或 stopped，等待人工决策 |

超时不应自动判定整个 goal 失败，因为长任务可能只是验证慢、模型慢或网络慢。

当前已先落地普通 loop 的 worker/reviewer attempt 控制：`execution.json` 保存 heartbeat、lease、
deadline 和 owned PID；单次超时会停止该 attempt、写 `timeout-report.md` 并进入
`needs_human`。`goal run --runner-timeout` 可把单次 Worker/Reviewer timeout 配置为 60 到
3600 秒，checkpoint 最多五轮。checkpoint 总 timeout 和 goal wall-clock timeout 尚未实现。

### 8.3 停止

停止分两种：

- 用户显式停止：`vega goal stop --reason "..."`
- runtime 安全停止：预算、污染、验证、gate、review 等门禁触发

停止后：

- Goal 不再调度新的 checkpoint。
- 写 `stop-report.md`。
- 保留当前 checkpoint 证据。
- 不自动 revert。
- 不自动删文件。

如果 child loop 正在运行，`vega goal stop` 不会终止 child。应先运行
`vega stop --run <child_run> --reason "..."` 请求当前 owned process 在安全边界停止，
再根据 Goal 状态和现场决定 `goal recover` 或 `goal stop`。

普通 loop 的运行中停止使用：

```powershell
vega stop --run <loop_run> --reason "方向变化，停止当前 attempt"
```

该命令只写 `stop-request.json`，由仍在运行的 runner 停止 execution.json 中记录的 child PID；不会扫描或终止其他 Codex/Node 进程。若 runner 已断开，则等待 lease 过期或 PID 消失后使用 `vega recover`，recover 本身不 kill 进程。

## 9. 与现有能力的关系

现有能力可以直接复用：

- `.vega.yaml`：提供验证命令、风险路径和预算。
- `loop`：承担单个 checkpoint 的执行闭环。
- `workspace-check`：防止 worker 生成大量噪声文件。
- `verification`：提供机器验证证据。
- `gate`：判断当前 checkpoint 是否可继续。
- `review`：隔离审查。
- `finish`：汇总单个 loop。
- `decision`：记录人工批准或停止原因。
- `recover`：恢复半完成 run。

Goal 层不替代这些模块，只是把它们串成可恢复的长任务协议。

## 10. 分阶段实现建议

### P0：文档和状态层

状态：已实现。

目标：先把长任务说清楚，不急着让 AI 自动长跑。

- 新增 `goal start/status/pause/resume/stop/recover`。
- 新增 `goal attach/checkpoint-done`，把人工执行后的证据引用挂回 checkpoint。
- child run 证据会校验存在性、类型、仓库和完成资格。
- 同一时间只允许一个 active checkpoint，完成后的证据不可修改。
- `goal complete` 与 `goal stop` 分离，分别表达成功和终止。
- 写 `goal-contract.md/json`。
- 写 `goal-state.json` 和 `goal-trace.jsonl`。
- `goal step` 只生成下一个 checkpoint plan，不自动大改。
- `goal step` 只生成 checkpoint plan，不调用普通 loop。
- `goal checkpoint-done` 只写 checkpoint report，不自动进入下一阶段。

验收：

- 能创建 goal。
- 能暂停、恢复、停止。
- 能挂载 child run 或人工证据引用。
- 能标记 checkpoint 完成并生成 report。
- 能生成 goal final report 和 goal eval。
- 能从 running 状态恢复为 needs_human。
- 不调用外部 worker 也能完整追溯目标和状态。

### P1：单 checkpoint 自动推进实验

状态：`experimental / single-checkpoint implemented`

当前实现：

- `goal step --text/--input` 固化本 checkpoint 的唯一任务输入。
- `goal run --max-checkpoints 1` 调用一次普通 auto loop。
- checkpoint 持久化唯一 `bound_child_run` 和 `runner_timeout_seconds`。
- child run 复用 Worker、workspace-check、verification、risk gate、Reviewer 和 loop eval。
- child run 身份、终态和证据引用写回 Goal。
- child 成功且证据资格通过时，自动生成 checkpoint report 并停在 `checkpoint_done`。
- child 异常、损坏、失败或证据不足时，生成 `checkpoint-blocked.md` 并停在 `needs_human`。
- `goal reconcile` 只重新核对绑定 child，并与 child 的 continue/recover lifecycle lock 串行。
- run-local `progress.jsonl` 与 `vega watch` 提供安全阶段可见性。

验收：

- fake Worker/Reviewer 的真实本地 child loop 能完成一个验证通过的 checkpoint。
- 损坏 child state 会保留完整 Goal 状态、trace、progress 和阻塞报告。
- 控制进程中断且 worker 仍存活时，父 Goal 不接管；worker 退出后可恢复同一 child。
- 同一 child 恢复成功后，父 Goal 能刷新原 evidence ref 并完成 checkpoint，不会增加替代 ref。
- 单次 runner timeout 可安全记录为 3600 秒，59 和 3601 秒会被拒绝。
- fake API key 不进入 Goal 或 child run artifacts。
- `max_checkpoints > 1` 被明确拒绝。

尚未证明：

- 自动完成 2-3 个连续 checkpoint。
- 真实模型连续运行数小时或数天时的目标稳定性、供应商连接稳定性和成功率。
- 主进程崩溃后自动重连原外部 Worker 会话。
- 相比人工拆分多个普通 loop，P1 能显著提高任务成功率或降低成本。

因此当前阶段只验证控制机制和单 checkpoint 恢复路径，不把它描述为长期自治能力。

2026-08-09 的一次真实 Codex 实验在可丢弃目标仓库中完成了受限代码修改、固定验证、
Reflect、Risk Gate 和独立 Reviewer。child 首次因 Windows CRLF 的确定性 diff check
误判停在 `needs_human`；修复后使用 `loop continue` 继续同一 child，最终得到
`success/approve`。完整追加记录见
[`../eval/long-task-controller-experiment.md`](../eval/long-task-controller-experiment.md)。

该真实运行暴露的父 Goal 重新归档缺口，已经通过显式 `goal reconcile` 修复。该命令不轮询、
不启动模型、不自动重试，只重新读取 checkpoint 已绑定的 child。跨进程测试已证明：
控制进程退出、owned child 仍存活时不会并发接管；child 退出后可恢复同一 run，继续成功后
父 Goal 能重新验证证据并进入 `checkpoint_done`。

这仍然只证明控制器和证据恢复机制，不证明真实模型可以无人值守稳定工作数小时。

2026-08-09 的真实控制进程中断 dogfood 又验证了一个更严格的场景：父控制进程在真实 Codex
Worker 尚未形成 tracked diff 时退出。Goal 正确保留唯一 child，Worker 退出后允许 recover，
并能由 reconcile 归档最终 `needs_human` 证据；没有创建替代 run、误报成功或泄露凭据。

但恢复后的 `loop continue` 跳过 Worker，直接对原始基线运行全部固定验证，最终因 `no_diff`
停止。该行为安全但无效，也产生了约 13 分钟无必要验证成本。因此本轮正式裁决为
`reject`，机制附加判断为 `fail-closed-mechanism-pass`。

P1 当前保持实验入口，不提升为默认或正式长任务模式。唯一允许的后续实现是处理
`interrupted_step=worker` 且无 tracked diff 的恢复决策：明确重新运行 Worker，或者在昂贵
验证前要求人工选择。完成该窄修复和新 dogfood 前，不进入多 checkpoint、后台 daemon、
自动重试策略或 P2 扩建。完整记录见
[`../eval/long-task-controller-experiment.md`](../eval/long-task-controller-experiment.md)。

该窄修复随后增加了显式 `loop continue --rerun-worker`：没有新成果时普通 continue 会先
拒绝，只有人工明确选择后才在同一 child 的下一 iteration 重跑；已有 tracked 或非 ignored
untracked partial work 时禁止重跑覆盖。相关恢复与 CLI 分片测试已通过。

同日 r4 真实 dogfood 没有命中“无成果时显式重跑”的理想路径。Goal
`20260809-191214-goal` 与唯一 child `20260809-191248-657688-feature-loop` 均被保留；
控制层中断后，Windows launcher/job tree 使 child owner/Codex 一并结束，目标副本留下
12 个文件的 partial work。Vega 没有覆盖、清理或创建替代 child；reconcile、recover 和
最终 `checkpoint_blocked` 均按 fail-closed 语义工作。

普通 `loop continue` 在该 partial work 现场返回非零，进入人工风险门禁，不启动新的
Worker 或 Reviewer。正式裁决为 `reject`，机制附加判断为
`fail-closed-partial-work-pass`。因此当前长任务能力只应表述为支持 Goal/child 状态、
进度、证据和人工恢复，并能在单个 checkpoint 的进程故障后安全停下；不应表述为数小时
或数天无人值守自治。

若未来继续实验，必须新建独立协议验证真实显式重跑路径，先解决 Windows 故障注入的进程
所有权歧义；在此之前不增加 daemon、多 checkpoint、后台自动重试或新的编排框架。

同日后续 r5 因监控脚本在 execution 仍为 `starting` 时过早裁决而
`protocol-invalid`，没有执行故障注入，也没有得到产品结论。该 child 使用 `vega stop`
安全结束，目标副本保持 clean，父 Goal 正确停在 `checkpoint_blocked`。

独立 r6 只修正监控条件，并在同一任务、预算和 prepared HEAD 下命中真实显式重跑路径：
iteration 01 在 Worker running、工作区 clean 且尚无 `file_changed` 时精确终止本次 owner
与命名 Job；reconcile 与 recover 后，普通 continue 无副作用拒绝，`--rerun-worker` 在同一
child 创建 iteration 02。真实 Codex 只修改 `README.md`，固定验证通过，Risk Gate 为 low，
独立 Reviewer 返回 `approve`，child 为 `success/done`，父 Goal reconcile 后为
`checkpoint_done`。

r6 正式裁决为 `candidate-for-opt-in`，机制判断为
`explicit-worker-rerun-path-pass`。这关闭了“Worker 无成果中断后直接跳过 Worker 并浪费
验证”的窄缺口，证明单 checkpoint 可以通过人工显式选择恢复并完成。

能力边界仍不变：这不是无人值守长任务系统，不证明模型连续数小时或数天稳定运行，也不自动
恢复、自动重试或创建多个 checkpoint。P1 保持显式实验入口，默认 Runtime 不变；后续只在
真实日常任务中观察，不因本轮结果增加 daemon、数据库或新的编排框架。完整追加证据见
[`../eval/long-task-controller-experiment.md`](../eval/long-task-controller-experiment.md)。

### 2026-08-09：r6 后安全审阅与显式重跑加固

r6 的真实成功只证明当时命中的 clean-workspace 路径。随后代码审阅发现两项不能由该样本
覆盖的 P1：

1. 首轮恢复只检查 tracked 与非 ignored untracked 路径，Worker 留下 ignored partial work
   时可能错误允许重跑。
2. 后续 iteration 只比较变更路径集合；同一 tracked 路径内容在确认重跑后被外部修改时，
   旧快照可能无法发现。

当前实验分支没有增加新的长任务组件，而是复用现有 workspace baseline artifact：

- 每轮 auto Worker 启动前写入 `iterations/<NN>/worker-baseline.json`。
- baseline 增加 staged/unstaged tracked diff 内容哈希，并保持旧 artifact 可读取。
- 根状态记录最新 Worker baseline，并以结构化授权绑定来源中断轮次、recovery ID 和 baseline
  SHA256。
- eval 与 artifact integrity 要求 `auto_worker_rerun_requested` trace 和授权一一对应；
  baseline 缺失、篡改或 trace 被删除时 fail-closed。
- 达到 `max_iterations` 后，状态输出不再建议显式重跑，只保留人工完成现场后的普通 continue。

本轮只修复 r6 后审阅发现的恢复边界，没有运行新的真实模型 dogfood，也没有改变 r6 的历史
裁决。显式 Worker 重跑仍是实验分支能力；主线、默认 `vega do`、普通 loop 和 Reviewer
语义均不改变。

### P2：更强 eval 和经验沉淀

目标：让长任务是否成功更可量化。

- goal-level eval。
- checkpoint success rate。
- stop reason 统计。
- 超时/断开 dogfood case。
- memory proposal 只在 goal 完成或人工确认后生成。

验收：

- 能比较小任务、大任务、长任务的成功率。
- 能回答“为什么停”“停在哪里”“下一步是什么”。

## 11. 暂不做

为了保持轻量，暂不做：

- Web UI。
- 后台 daemon。
- 数据库任务队列。
- 自动并发 worker 池。
- 自动 commit/push/release。
- 自动回滚和清理。
- 复杂多 Agent 平台。

## 12. 一句话设计结论

长任务不应该靠一个无限上下文 worker 硬跑到底，而应该靠 goal contract 和 checkpoint 把大目标切成多个可验证、可恢复、可审查的小闭环。

Vega 的价值不是替 AI 放开手脚，而是给 AI 的长时间工作加上边界、证据、恢复和停止语义。
