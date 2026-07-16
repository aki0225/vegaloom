# 长任务 Goal Loop 设计

> 状态：P0 人工状态层已实现；P1 有限自动 checkpoint 推进仍是设计草案。

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

这个 contract 的目标是区分四种情况：

| 情况 | 判断方式 | 应对 |
| --- | --- | --- |
| 正常长跑 | 仍在 checkpoint 预算内，有 trace 心跳 | 继续 |
| 超时 | 单个 step 超过 timeout | 停在 needs_human，写 timeout report |
| 断开/陈旧 | state 是 running，但 trace 长时间无更新 | recover 成 needs_human |
| 失控 | 超出 scope/budget 或继续新增无关变更 | stop / needs_human |

## 5. 状态语义

当前状态较少：

```text
running / success / failed / needs_human
```

长任务需要更明确的状态，但仍应保持轻量：

```text
created          goal 已创建，尚未开始
running          当前 checkpoint 正在执行
checkpoint_done  当前 checkpoint 完成，等待进入下一阶段
paused           用户主动暂停，可恢复
stopping         收到 stop 请求，正在等安全边界
stopped          已停止，不再调度
blocked          缺信息或连续失败，需要人介入
timeout          当前 step 超时，不等于 goal 失败
stale            疑似断开或无心跳，需要 recover
success          goal 达成 success_conditions
failed           runtime 自身异常或不可恢复错误
```

关键区别：

- `timeout` 不等于失败。
- `stale` 不等于失败。
- `paused` 不等于失败。
- `blocked` 表示继续会乱来，需要人判断。
- `success` 必须满足 goal contract 中的停止条件。

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
vega goal start --repo . --text "分阶段收口 runtime 状态机" --scope refactor
```

P1 再增加有限自动推进：

```powershell
vega goal run --run <goal_run> --max-checkpoints 3
```

`goal run` 不能无限执行。每个 checkpoint 都必须经过：

```text
checkpoint plan -> loop/worker -> workspace-check -> verification -> reflect -> gate -> review -> checkpoint report
```

如果 gate 是 high risk、验证失败、污染门禁失败、reviewer request_changes 或达到 checkpoint 预算，就停在人工判断。

## 7. 目录结构草案

```text
runs/<goal_run>/
  state.json
  goal-contract.md
  goal-contract.json
  goal-state.json
  goal-trace.jsonl
  progress.md
  decisions.jsonl
  goal-final-report.md
  goal-eval.md
  checkpoints/
    01/
      checkpoint-plan.md
      checkpoint-evidence.json
      checkpoint-report.md
    02/
      ...
```

`progress.md` 面向人阅读；`goal-state.json` 面向 runtime；`goal-trace.jsonl` 面向追溯和恢复。`state.json` 与 `goal-state.json` 保持同内容，用于复用通用 `vega status/latest`。

## 8. 断开、超时、停止的定义

### 8.1 断开

断开是状态和执行现实不一致：

```text
state.json 仍是 running，但 trace.jsonl 长时间没有新事件，且当前 step 没有完成产物。
```

处理方式：

- 不假设失败。
- 不自动清理。
- 写 `recovery-report.md`。
- 标记为 `stale` 或 `needs_human`。
- 让用户决定继续、停止或重开。

### 8.2 超时

超时分三层：

| 类型 | 示例 | 应对 |
| --- | --- | --- |
| step timeout | worker/reviewer/verification 单步超过限制 | 停止当前 step，写 timeout report |
| checkpoint timeout | 一个 checkpoint 超过预算 | 停止调度下一步，进入 needs_human |
| goal wall-clock timeout | 整个 goal 超过总预算 | 进入 blocked 或 stopped，等待人工决策 |

超时不应自动判定整个 goal 失败，因为长任务可能只是验证慢、模型慢或网络慢。

当前已先落地普通 loop 的 worker/reviewer attempt 控制：`execution.json` 保存 heartbeat、lease、deadline 和 owned PID；单次超时会停止该 attempt、写 `timeout-report.md` 并进入 `needs_human`。checkpoint timeout 和 goal wall-clock timeout 仍属于后续 Goal P1。

### 8.3 停止

停止分两种：

- 用户显式停止：`vega goal stop --reason "..."`
- runtime 安全停止：预算、污染、验证、gate、review 等门禁触发

停止后：

- 不再调度新的 worker/reviewer。
- 写 `stop-report.md`。
- 保留当前 checkpoint 证据。
- 不自动 revert。
- 不自动删文件。

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

### P1：有限自动 checkpoint

目标：允许小范围自动推进，但每个 checkpoint 都有硬边界。

- `goal run --max-checkpoints N`。
- 每个 checkpoint 调一次普通 `loop`。
- checkpoint 后自动跑 gate/review/report。
- 任意门禁失败都停止。
- 连续无进展时停止。

验收：

- 能完成 2-3 个 checkpoint 的小型重构演示。
- 任意 checkpoint 失败不会影响前面 checkpoint 证据。
- 可以从中断处恢复并继续或停止。

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
