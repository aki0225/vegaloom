# Supervisor Agent Gate 3B 真实跨机器接力协议

> 状态：`protocol-frozen / machine-a-pending`
>
> 日期：2026-08-15
>
> 主线基线：`main@2b765cfefe8deac121f752e3c9acfec1e3effd73`
>
> 实验分支：`codex/supervisor-gate3b`

## 一、目标

Gate 3A 已证明 Vega 能在同一台机器的两个隔离副本之间，通过 Handoff Checkpoint、
Resume Capsule 和 Git Task Card 完成机械往返。Gate 3B 只验证一个尚未证明的能力：

```text
机器 A：真实 Codex Worker 产生未完成 WIP
  → 停止并完成 Writer、Workspace 和副作用对账
  → 生成 Handoff Task Card
  → 人工 commit/push

机器 B：只从 Git 拉取任务分支
  → vega agent resume
  → 真实 Codex Worker 继续同一 Work Item
  → 重新执行 Verification、Risk、Reviewer 和 Finish
```

本 Gate 不以同机 clone、worktree、复制 `runs/` 或人工重建旧聊天替代真实跨物理机器接力。

## 二、冻结案例

Case ID：`SAG3B-01`

目标仓库：VegaLoom 本仓库。

用户目标：

> 修复 Handoff 发布失败时可能留下成功 Trace、但 Task Card 和 State 已回滚的叙事不一致，
> 并补充精确回归测试。失败必须保持 fail-closed，不能通过删除 append-only Trace 掩盖失败。

当前静态事实：

1. `create_handoff()` 先追加 `agent_handoff_created`，再写 `status-card.md`；
2. 如果状态卡写入失败，异常处理会删除 Task Card，并恢复旧 State 和 metadata；
3. 已追加的 Trace 不会被回滚，可能继续宣称 Handoff 已成功生成；
4. 现有故障注入测试只检查 State 与 Task Card，没有检查 Trace 和发布叙事的一致性。

该任务来自当前主线真实代码，不暴露预设补丁，不指定具体实现。Worker 可以安全调整发布顺序，
或追加明确的失败/回滚事件，但不得截断、删除或重写既有 Trace。

## 三、唯一 Work Item

```yaml
id: W1
objective: 修复 Handoff 失败发布的 Trace 与状态叙事一致性，并补充最小回归测试
allowed_paths:
  - src/vega/agent_handoff.py
  - tests/test_agent_handoff.py
forbidden_paths:
  - .vega.yaml
  - src/vega/agent_persistence.py
  - src/vega/agent_runtime.py
  - src/vega/agent_task_card.py
  - docs/**
  - eval/**
verification:
  - python -m pytest -q -o cache_dir=.tmp/pytest/cache/gate3b tests/test_agent_handoff.py --basetemp=.tmp/pytest/runs/gate3b-handoff
  - ruff check --no-cache src/vega/agent_handoff.py tests/test_agent_handoff.py
  - git diff --check
```

成功条件：

1. 正常 Handoff 仍生成 Task Card、State、Trace、状态卡和现有 Artifact；
2. 任一发布步骤失败时，State 不得保留有效 Handoff，Task Card 不得作为可恢复入口存在；
3. `status-card.md` 写入失败等晚期故障不得留下无条件的成功发布叙事；
4. append-only Trace 不被截断、删除或改写；
5. 精确测试覆盖晚期故障后的 Trace、State 和 Task Card 一致性；
6. 变更只出现在两个允许文件，三条冻结验证命令全部通过；
7. 机器 B 的旧 Verification、Risk、Reviewer 结果只作为 historical，新执行必须重新生成四个
   Gate 的证据后才能 Finish。

## 四、模型与预算

正式运行固定使用：

```text
Adapter: codex-exec
Worker: gpt-5.6-sol / xhigh
Reviewer: gpt-5.6-sol / xhigh
Worker timeout: 900 秒
Reviewer timeout: 继承当前项目默认值
自动重试: 0
replan: 0
Work Item: 1
```

机器 A 和机器 B 必须使用同一个 Codex Adapter、模型和推理强度。不得因结果更换任务、模型、
允许路径或成功条件。凭据、Provider URL 和本机 Codex 配置不进入协议、Task Card、运行证据
或 Git。

## 五、机器 A 协议

### 5.1 预检

1. 从本协议提交后的 `codex/supervisor-gate3b` HEAD 开始；
2. `git status --short --branch` 除忽略目录外必须干净；
3. `codex --version`、`codex login status` 和 `vega agent capabilities` 通过；
4. 确认有效模型为 `gpt-5.6-sol / xhigh`，但不得读取或打印凭据；
5. Agent Plan 只写入 `.tmp/dogfood/sag3b-01/protocol/agent-plan.json`，不得提交；
6. 启动前记录 HEAD、tree、Plan 摘要和关键文件 SHA-256 到本地运行登记，不修改历史实验记录。

### 5.2 启动与停止

```powershell
vega agent start --repo . --plan .tmp/dogfood/sag3b-01/protocol/agent-plan.json --text "<冻结目标>"
vega agent approve --run <agent-run>
vega agent run --run <agent-run> --timeout 900
```

`agent run` 在一个控制进程中执行；另一个控制进程只观察状态和 tracked Diff。首次同时满足以下
条件时，立即发出身份绑定的停止请求：

1. 至少一个允许文件出现可解释的 tracked Diff；
2. Agent State 仍有 active child/operation；
3. 尚未进入可信终态；
4. 没有允许路径外变更或未知外部副作用。

```powershell
vega agent stop --run <agent-run> --reason "Gate 3B physical-machine handoff"
```

如果 Worker 在停止请求前已经完成整个任务，本 Case 记为
`insufficient-handoff-opportunity`，不替换任务、不改超时、不故意制造失败，也不宣称 Gate
通过。

### 5.3 Handoff 发布

停止后必须等待原 `agent run` 进程退出，并确认：

- State 不再绑定 active child/operation；
- Writer、Reviewer、pytest 和 Vega 子进程均已退出；
- Workspace 只有允许路径内的可解释 WIP；
- HEAD 未被 Worker 改变；
- 外部副作用为 `none` 或已明确解释；
- 最新 Checkpoint 可以安全交接。

随后执行：

```powershell
vega agent checkpoint --run <agent-run> --handoff --reason "Gate 3B physical-machine handoff"
```

人工只暂存：

1. 允许路径内的 WIP；
2. 本次生成的 `.vega/tasks/**` Task Card。

暂存后必须执行：

```powershell
git diff --cached --check
git diff --cached --name-status
git status --short --branch
```

确认无 `.env`、`runs/`、`.tmp/`、本地 Agent/Trellis 配置、凭据或其他项目文件后，人工
commit/push。Vega 本身不得执行 Git 写入。

## 六、机器 B 协议

机器 B 必须是另一台物理机器，并且只通过 Git 获取：

- 协议分支历史；
- 允许路径内的 WIP；
- Git Task Card。

禁止复制机器 A 的：

- `runs/`、Trace、Checkpoint、SQLite、状态卡；
- `.tmp/`、虚拟环境、Codex 会话或聊天；
- `.env`、凭据和本地工具配置；
- 未提交文件。

执行顺序固定为：

```powershell
git fetch origin
git switch codex/supervisor-gate3b
git pull --ff-only
vega agent resume --repo .
vega agent status --run <resumed-run>
vega agent run --run <resumed-run> --timeout 900
```

开始真实 Worker 前必须确认：

1. Task Card 位于当前分支、当前 Handoff HEAD，且内容摘要匹配；
2. Workspace Digest、changed files 和 WIP 内容与 Task Card 一致；
3. 新 run 的当前 Work Item 仍为 `W1`；
4. 机器 A 的 Gate 结果标记为 historical；
5. 新 run 的 Verification、Risk 和 Review 均为 `not_run`；
6. 没有第二 Writer，也没有依赖机器 A 本地 Artifact。

机器 B 完成后必须重新执行冻结验证、风险门禁、独立 Reviewer 和 Finish。只有新的机器证据
完整且一致，Supervisor 才能进入 `finalize`。

## 七、通过标准

Gate 3B 通过必须同时满足：

```text
physical_machine_handoff = 1
work_item_count = 1
false_success = 0
duplicate_writer_start = 0
cross_machine_stale_evidence_accepted = 0
unknown_side_effect_auto_retry = 0
automatic_git_write = 0
```

并满足：

1. 机器 A 产生真实未完成 WIP，旧 Writer 安全停止并完成对账；
2. Task Card 能让机器 B 在没有旧聊天和本地 run Artifact 的情况下解释任务；
3. 机器 B 从当前 Handoff HEAD 恢复，继续同一 Work Item；
4. 新 Verification、Risk、Reviewer 和 Finish 全部形成可信 Artifact；
5. 最终 Diff 只包含批准路径，Reviewer verdict 有效；
6. 全过程没有自动 commit、push、release、删除文件或写入长期 Memory。

以下结果安全但不通过 Gate：

- 机器 A 未形成可停止的 partial Diff；
- Handoff 为 `handoff_blocked`；
- 机器 B Task Card、HEAD、Workspace Digest 或 WIP 对账失败；
- 新机器接受旧 Gate 结果作为当前证据；
- 任一验证、风险、Reviewer 或 Finish 证据缺失、过期或冲突；
- `needs_human`、`replan` 或未知副作用。

## 八、立即停止条件

- 需要把机器 A 的本地 run、Trace、SQLite 或聊天复制到机器 B；
- 需要放宽 fail-closed、Scope、Workspace、Evidence freshness 或 Reviewer 门禁；
- 需要修改允许路径外代码才能继续；
- 出现第二 Writer、身份不明的残留子进程或未知外部副作用；
- 需要自动 commit/push，或要求 Vega 保存凭据；
- 需要加入 Claude Code、Memory、多 Work Item、Provider SDK、服务端或新的恢复框架；
- 需要按结果更换任务、模型、预算或成功条件。

## 九、结果记录

本 Gate 的最终结果只在机器 B 完成后追加到：

```text
eval/real-world-runs.md
```

记录至少包括：

- 机器 A/B 的 Git HEAD、run_id、child/operation；
- Handoff Task Card 路径、提交和内容摘要；
- A 侧停止时的 changed files、进程与副作用对账；
- B 侧恢复时 historical/current Gate 状态；
- 新 Verification、Risk、Reviewer、Finish Artifact；
- 最终 Decision、指标和 `gate-exit-pass` 或未通过原因。

历史失败和 fail-closed 结果不得删除、覆盖或润色。机器 A 阶段只提交协议与真实 Handoff WIP，
不提前写入最终 Gate 结论。
