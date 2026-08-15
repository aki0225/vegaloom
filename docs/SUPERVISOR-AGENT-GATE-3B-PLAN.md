# Supervisor Agent Gate 3B 真实跨机器接力协议

> 状态：`prerequisite-ci-pass / controller-refrozen / machine-a-not-started`
>
> 日期：2026-08-15
>
> 主线基线：`main@2b765cfefe8deac121f752e3c9acfec1e3effd73`
>
> 实验分支：`codex/supervisor-gate3b`

> 首次协议提交：`977af8f45ae6ba0bc425ca3c9e8556d696ab6664`。该提交在真实 Worker
> 启动前发现控制器自修改和未知副作用降级两个前置缺口，因此不得作为正式执行基线。
>
> 前置门禁实现提交：`3e636e40537bfda5213d13a407ae51b6be0fbbd8`
>
> 控制源码 tree：`a8c8a5c5d92cd4fb523f895c70803ecfcf0f31fd`
>
> 控制源码 archive SHA-256：
> `f6c58b15a8bffe69df8f7805bfa996ae9eb8c31294c6aaf9b263171d22ad37e9`
>
> Runner 配置 SHA-256：
> `dba63bd3abaf7a8a0950430b6c4d6fbcc40fca45370b0bd853cc42d81fdc6acb`

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
Reviewer timeout: 900 秒
机器 A 最大正式 attempt: 1
机器 B 最大正式 attempt: 1
自动重试: 0
manual repair: 0
replan: 0
Work Item: 1
```

机器 A 和机器 B 必须使用同一个 Codex Adapter、模型和推理强度。不得因结果更换任务、模型、
允许路径或成功条件。凭据、Provider URL 和本机 Codex 配置不进入协议、Task Card、运行证据
或 Git。

## 五、执行前置门禁

### 5.1 固定控制器

正式运行不得从目标 checkout 的 editable 安装加载 Vega。目标任务会修改 Vega 自身源码，如果后续
`agent stop`、`checkpoint --handoff` 或 `agent resume` 直接导入目标 Workspace，实验 WIP
就会改变实验控制器。

机器 A 和机器 B 必须分别从同一个 `control_source_commit` 重建只读控制源码快照，并登记：

```text
control_source_commit
control_source_tree
control_source_sha256
vega_version
python_version
os_arch
codex_version
runner_config_digest
editable_install = false
```

本次冻结值：

```text
control_source_commit = 3e636e40537bfda5213d13a407ae51b6be0fbbd8
control_source_tree = a8c8a5c5d92cd4fb523f895c70803ecfcf0f31fd
control_source_sha256 = f6c58b15a8bffe69df8f7805bfa996ae9eb8c31294c6aaf9b263171d22ad37e9
runner_config_digest = dba63bd3abaf7a8a0950430b6c4d6fbcc40fca45370b0bd853cc42d81fdc6acb
```

控制源码放在项目内被忽略的
`.tmp/dogfood/sag3b-01/control-runtime-3e636e4/`。启动前必须证明实际 `vega.__file__`
位于该快照，而不是目标 checkout 的 `src/vega/`。机器 B 从 Git 中的同一 commit 独立重建
快照，不复制机器 A 的 wheel、源码目录或 Python 环境。

本协议中的 `<frozen-vega>` 表示上述固定控制器启动方式，不表示目标 checkout 中的
`.venv/Scripts/vega.exe`。

### 5.2 未知副作用不得降级

正式运行前必须先通过一个窄前置修复：

1. 没有 active Writer 的 `pause/stop` 必须继承最近 Checkpoint 的
   `external_side_effects`；
2. 最近值为 `unknown` 时，第二次 `stop` 仍为 `needs_human / blocked`；
3. `unknown → stop → handoff` 只能得到 `handoff_blocked`；
4. 不新增状态模型、事务框架或自动副作用判断。

上述继承修复通过后，真实停止演练进一步确认：主动停止真实 Worker 会保守留下
`external_side_effects=unknown`。这不是可以改成 `none` 的 Adapter bug；在缺少人工确认时，
`handoff_blocked` 正是正确终态。Gate 3B 若要继续，必须补一个窄的人工裁决入口，而不是
自动猜测副作用。

人工裁决固定为：

```text
needs_human + no active Writer + latest Checkpoint unknown
  → 人工提供 actor、reason，并在 evidence_refs 中提供至少一个 run-local 条目
  → Vega 重新采集并核对 Workspace
  → 追加 adjudication Artifact 和新 Checkpoint
  → none: stopped / safe
  → known: needs_human / blocked
```

旧 `unknown` Checkpoint 和 Trace 不得改写。证据路径必须位于当前 run 内并绑定 SHA-256；
Workspace 漂移、Git control 不完整、Handoff 已发布或证据缺失时拒绝裁决。CLI 只新增显式
`vega agent adjudicate-side-effects --run <agent-run> --input <request.json>`，不改变默认
命令、自动重试或成功语义。

该窄路径通过定向回归、本地静态门禁和 PR CI 后，重新冻结 `control_source_commit`，才能
开始机器 A。

### 5.3 无效预检记录

`20260815-163843-agent` 只创建了未批准 Plan，没有启动 Worker、没有源码 Diff，也没有外部
副作用。它暴露了：

- 当前仓库需要通过 `VEGA_GIT_SAFE_DIRECTORY` 做进程级显式授权；
- 初始 Plan 错把实现自由度写进 `unresolved_decisions`，因此批准被正确拒绝；
- 当前 `.venv` 是目标 checkout 的 editable 安装，不能作为正式控制器。

该 run 记为 `invalid-preflight / no-model-turn`，不占用机器 A 的一次正式 attempt，也不作为
Gate 3B 成败证据。

### 5.4 前置门禁本地结果

2026-08-15 的本地结果：

- Recovery 分片：`27 passed in 50.23s`；
- Handoff 分片：`14 passed, 1 skipped in 37.31s`；
- 最终新增节点复核：`3 passed in 3.47s`；
- Ruff、compileall、repository hygiene 和 `git diff --check` 通过；
- architecture growth 通过：`C901 35→35`、Python 模块 `128→128`；
- 完整节点收集：`1242 collected`。

完整跨平台回归由 Draft PR 的 9 项 CI 继续验证。CI 未全绿前，当前状态只算
`prerequisite-local-pass`，不得启动机器 A。

未知副作用继承修复与协议 HEAD `87ef7a9` 已通过 PR `#61` workflow `31875925859` 的 9 项 CI：

- 静态检查、仓库卫生和架构增长通过；
- Python 3.12 四个完整文件分片通过；
- Windows 专项与 wheel smoke 通过；
- POSIX 临时目录专项通过；
- Python 3.11 编译与 `1242` 节点收集通过；
- wheel、sdist 构建及干净环境安装通过。

该结果只证明 unknown 不会被第二次 stop 洗白。后续人工裁决候选已完成本地验证：

- Recovery 四个独立分片：`6 + 8 + 8 + 11 = 33 passed`，最慢分片 `25.58s`；
- Handoff：`14 passed, 1 skipped in 39.07s`；
- mutation lock：`10 passed in 5.35s`；
- 裁决安全强化节点：`6 passed in 10.76s`，覆盖 Checkpoint 缺口、State 绑定、
  故障注入、CLI 脱敏和 junction 边界；
- 完整节点收集：`1248 collected`；
- Ruff、compileall、repository hygiene、architecture growth 和 `git diff --check` 通过；
  architecture growth 为 `C901 35→35`、Python 模块 `128→129`。

人工裁决候选 HEAD `0a6985f` 已通过 workflow `31877813234` 的 9 项 CI。控制源码已从该
commit 独立导出到被忽略的 run-local 目录，实际 `vega.__file__` 指向固定快照；控制 tree、
archive 与 runner 配置摘要均已重新登记。当前状态为 `prerequisite-ci-pass /
controller-refrozen`，机器 A 尚未启动。

### 5.5 仓库内 Workspace 预检阻断与重新冻结

2026-08-15 的候选 run `20260815-180117-agent` 使用当时的固定控制器创建并批准 Plan。
第一次简写目标在创建 run 前被 Plan 一致性校验拒绝；改用冻结目标原文后，run 正常进入
`ready`。执行 `agent run` 时，控制器在创建 child 和启动模型前拒绝继续：

```text
创建 child 前 Workspace 已漂移，必须先重新对账
```

核对确认没有 tracked Diff、模型 turn、active child 或外部副作用。漂移来自 Vega 把自己
刚写入仓库内 `runs/` 的 Task Brief、Checkpoint、State 和 Trace 纳入了 Workspace ignored
指纹。该记录判定为 `invalid-preflight / no-model-turn`，不占用机器 A 的正式 attempt，也
不能作为 Gate 3B 成败证据。

最小修复只让绑定 Agent Workspace 复用既有 `workspace_ignored_path_exclusions()`：

- workspace 自有 `runs/` 与受控 verification 临时根不再制造虚假漂移；
- 其他 ignored 路径新增或修改仍触发 `Workspace 已漂移`；
- 不改变 tracked Diff、Git control、Handoff、成功语义或允许路径。

本地新增正反向回归 `2 passed`，并复核 Runtime、Recovery、Handoff 漂移节点 `4 passed`；
完整节点收集为 `1250 collected`。提交 `3e636e4` 已通过 workflow `31879544491` 的 9 项
CI。控制源码随后从该提交重新导出，`vega.__file__` 指向
`.tmp/dogfood/sag3b-01/control-runtime-3e636e4/src/vega/__init__.py`，能力检查通过。

重新冻结值：

```text
control_source_commit = 3e636e40537bfda5213d13a407ae51b6be0fbbd8
control_source_tree = a8c8a5c5d92cd4fb523f895c70803ecfcf0f31fd
control_source_sha256 = f6c58b15a8bffe69df8f7805bfa996ae9eb8c31294c6aaf9b263171d22ad37e9
runner_config_digest = dba63bd3abaf7a8a0950430b6c4d6fbcc40fca45370b0bd853cc42d81fdc6acb
launcher_sha256 = 0af0b84ca94823b92a97c56cfd2a2427a6ac80574af72d6655cad6439661b6a1
agent_plan_revision = 2
agent_plan_sha256 = 78da4cccfaf2c24425bff29f6da8e161a165ff381eb5b0f27bed5df6de143fcf
```

机器 A 仍未正式启动；必须在该文档提交通过 PR CI 后重新生成启动预检。

## 六、机器 A 协议

### 6.1 预检

1. 从前置修复和协议修订均提交后的 `codex/supervisor-gate3b` HEAD 开始；
2. `git status --short --branch` 除忽略目录外必须干净；
3. `codex --version`、`codex login status` 和 `<frozen-vega> agent capabilities` 通过；
4. 确认有效模型为 `gpt-5.6-sol / xhigh`，但不得读取或打印凭据；
5. `control_source_commit`、控制源码摘要和 runner 配置摘要均与协议登记一致；
6. `HEAD == origin/codex/supervisor-gate3b`，且远端 HEAD 等于正式启动 HEAD；
7. Agent Plan 只写入 `.tmp/dogfood/sag3b-01/protocol/agent-plan.json`，不得提交；
8. 启动前记录 HEAD、tree、Plan 摘要和关键文件 SHA-256 到本地运行登记，不修改历史实验记录。

### 6.2 启动与停止

```powershell
<frozen-vega> agent start --repo . --plan .tmp/dogfood/sag3b-01/protocol/agent-plan.json --text "<冻结目标>"
<frozen-vega> agent approve --run <agent-run>
<frozen-vega> agent run --run <agent-run> --timeout 900
```

`agent run` 在一个控制进程中执行；另一个控制进程只观察状态和 tracked Diff。首次同时满足以下
条件时，立即发出身份绑定的停止请求：

1. 至少一个允许文件出现可解释的 tracked Diff；
2. Agent State 仍有 active child/operation；
3. 尚未进入可信终态；
4. 没有允许路径外变更或未知外部副作用。

```powershell
<frozen-vega> agent stop --run <agent-run> --reason "Gate 3B physical-machine handoff"
```

如果 Worker 在停止请求前已经完成整个任务，本 Case 记为
`insufficient-handoff-opportunity`，不替换任务、不改超时、不故意制造失败，也不宣称 Gate
通过。

### 6.3 Handoff 发布

停止后必须等待原 `agent run` 进程退出，并确认：

- State 不再绑定 active child/operation；
- Writer、Reviewer、pytest 和 Vega 子进程均已退出；
- Workspace 只有允许路径内的可解释 WIP；
- HEAD 未被 Worker 改变；
- 外部副作用为 `none` 或已明确解释；
- 最新 Checkpoint 可以安全交接。

如果最新 Checkpoint 的 `external_side_effects=unknown`，先在当前 run 内写入人工核对记录，
例如 `runs/<agent-run>/manual-evidence/side-effects-reviewed.md`，并在
`.tmp/dogfood/sag3b-01/protocol/side-effect-adjudication.json` 写入：

```json
{
  "reason": "已核对执行记录、进程和任务范围；本次没有仓库外写入",
  "workspace_explained": true,
  "external_side_effects": "none",
  "actor": "machine-a-operator",
  "evidence_refs": [
    "manual-evidence/side-effects-reviewed.md"
  ]
}
```

随后使用同一固定控制器执行：

```powershell
<frozen-vega> agent adjudicate-side-effects --run <agent-run> --input .tmp/dogfood/sag3b-01/protocol/side-effect-adjudication.json
```

只有裁决结果为 `none`、最新 Checkpoint 为 `stopped / safe` 时才继续。若人工确认结果为
`known`，必须在请求中如实写入 `known`，任务保持 `needs_human / blocked`，本次 Gate 不得
发布 ready Handoff。

满足上述条件后执行：

```powershell
<frozen-vega> agent checkpoint --run <agent-run> --handoff --reason "Gate 3B physical-machine handoff"
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

## 七、机器 B 协议

机器 B 必须是另一台物理机器，并且只通过 Git 获取：

- 协议分支历史；
- 允许路径内的 WIP；
- Git Task Card。

禁止复制机器 A 的：

- `runs/`、Trace、Checkpoint、SQLite、状态卡；
- `.tmp/`、虚拟环境、Codex 会话或聊天；
- `.env`、凭据和本地工具配置；
- 未提交文件。

机器 B 必须从 `control_source_commit` 独立重建 `<frozen-vega>`，并确认控制源码摘要、版本和
runner 配置摘要与机器 A 一致。

执行顺序固定为：

```powershell
git fetch origin
git switch codex/supervisor-gate3b
git pull --ff-only
<frozen-vega> agent resume --repo .
<frozen-vega> agent status --run <resumed-run>
<frozen-vega> agent run --run <resumed-run> --timeout 900
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

## 八、通过标准

Gate 3B 通过必须同时满足：

```text
physical_machine_handoff = 1
work_item_count = 1
control_runtime_digest_match = 1
remote_head_match = 1
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

## 九、立即停止条件

- 需要把机器 A 的本地 run、Trace、SQLite 或聊天复制到机器 B；
- 正式控制器从目标 checkout 的 editable 安装导入 Vega；
- 机器 A/B 的控制源码 commit、摘要或 runner 配置不一致；
- 需要放宽 fail-closed、Scope、Workspace、Evidence freshness 或 Reviewer 门禁；
- 需要修改允许路径外代码才能继续；
- 出现第二 Writer、身份不明的残留子进程或未知外部副作用；
- 需要自动 commit/push，或要求 Vega 保存凭据；
- 需要加入 Claude Code、Memory、多 Work Item、Provider SDK、服务端或新的恢复框架；
- 需要按结果更换任务、模型、预算或成功条件。

## 十、结果记录

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
