# Supervisor Agent Gate 3B 真实跨机器接力协议

> 状态：`SAG3B-02 formal-gate-nonconforming / SAG3B-03 gate-not-passed-preserved / committed-handoff-fix-merged / SAG3B-04 workspace-check-failed-preserved / SAG3B-05 mcp-isolation-failed-preserved / SAG3B-06 reviewer-isolation-blocked-preserved / SAG3B-07 machine-b-timeout-preserved / SAG3B-08 machine-a-known-side-effect / gate-not-passed`
>
> 日期：2026-08-17
>
> SAG3B-04 基线：`main@012700b6caca0450f820ff374082ae9216bc065f`
>
> SAG3B-04 分支：`codex/sag3b-04-status-visibility`

> 首次协议提交：`977af8f45ae6ba0bc425ca3c9e8556d696ab6664`。该提交在真实 Worker
> 启动前发现控制器自修改和未知副作用降级两个前置缺口，因此不得作为正式执行基线。
>
> SAG3B-03 控制源码提交：`be6fce26c227ac14abd1600b48ade063a01f5686`
>
> 控制源码 tree：`ea711b8ea32e9fa25806954ded0bde195476d4e6`
>
> 控制源码 archive SHA-256：
> `9403dea789288447f5354b51c0f8e8faa57b4433fe65e060484b420464921c75`
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

如果第一次 `agent stop` 是在 active Writer 仍运行时发出的，它只负责向匹配 execution
写入身份绑定的停止请求。原 `agent run` 返回、State 清空 active child/operation 后，必须
使用同一固定控制器再次执行一次 `agent stop`，把静止 Workspace 固化为
`operation_started=false` 的新 Checkpoint：

```powershell
<frozen-vega> agent stop --run <agent-run> --reason "freeze quiescent machine-A workspace before side-effect adjudication"
```

只有 State 无 active binding、最新 Checkpoint 为 `needs_human / blocked`、
`operation_started=false` 且 `external_side_effects=unknown` 时，才进入人工裁决。不得手工
改写旧 Checkpoint，也不得跳过第二次 stop 直接把 unknown 改为 none。

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

## 十一、2026-08-15 机器 A 正式 Attempt 结果

本节只追加 SAG3B-01 的正式机器 A 结果，不改写上方冻结协议。正式启动 HEAD 为
`c08d46ab469f1a98421b3cabc73a2c5cd18ceb50`，固定控制源码提交为
`3e636e40537bfda5213d13a407ae51b6be0fbbd8`。控制源码 tree、archive、launcher、
runner 配置和 Plan 摘要均与启动预检登记一致。

- Agent run：`20260815-184052-agent`；
- child：`20260815-184120-147051-bug-loop`；
- operation：`02125d80693b4fe7ae548fd527814bb5`；
- Worker：`gpt-5.6-sol / xhigh`；
- Codex Worker 进程正常返回结构化 `blocked` Claim，但其 PowerShell、Git、Ripgrep 和
  Node REPL 工具均在启动前遇到
  `windows sandbox: helper_unknown_error: setup refresh had errors`；
- Worker 未能读取或修改工作区，没有 tracked Diff，也没有运行 Worker 自检；
- 观察进程始终没有看到“允许路径 Diff + active Writer”的同时窗口，因此没有发送
  `agent stop`；
- Core Verification 中 Ruff 通过，pytest 对应的受控执行因 Windows `WinError 5`
  无法原子发布 `execution.json` 而失败；该结果只能记为 Verification `failed`，
  不能解释为目标修复通过；
- Risk Gate 和 Reviewer 均未运行，child Finish 为 `needs_human`；
- Supervisor 根据机器 Observation 确定性选择 `replan`，最终 phase 为 `planning`，
  Checkpoint 为 `blocked`；
- HEAD 未改变，Workspace 没有 tracked Diff，外部副作用为 `none`，Writer、pytest
  和 Vega owner 进程均已退出。

本次正式 attempt 判定为
`insufficient-handoff-opportunity / environment-blocked`。它证明真实 Worker 无法形成
可交接 WIP 且 Verification 失败时，Supervisor 不会制造成功、自动重试或继续启动机器 B；
不证明真实跨机器恢复。SAG3B-01 不更换任务、模型、预算或成功条件，也不进行第二次运行。
机器 B 未启动，没有生成 Handoff Task Card 或 Handoff 提交，Gate 3B 未通过，Gate 3C
继续冻结。

## 十二、2026-08-16 SAG3B-02 机器 A 阶段结果

本节追加新的机器 A 本地阶段证据，不重跑或改写 SAG3B-01。控制候选基于
`main@d2c28103d352f251f1bf20d89758e666dba086ed`，当前位于
`codex/supervisor-gate3b-r2`，包含两个前置合同修复：

1. Worker 最终 Claim 为 `blocked` 时不再启动 Core Verification、Risk 或 Reviewer；
2. 已批准 Work Item 的冻结 verification 命令原样下传并覆盖项目默认验证。

正式控制快照 `control-runtime-local-r3` 只复制 `git ls-files src`，并设置
`PYTHONDONTWRITEBYTECODE=1`。导入路径、`agent capabilities`、裸 `codex exec`
workspace-write 和 Vega-owned Codex Runner 预检通过；控制源码 131 个文件，检查后没有
`__pycache__`。当前 Codex 默认配置经脱敏核对为 `gpt-5.6-sol / xhigh`。机器 A 执行时，
该控制快照来自尚未提交的工作树，因此本节只能形成机器 A 本地阶段证据，不能作为跨机器
Gate 通过结论。

正式运行身份：

- Agent run：`20260816-121500-agent`；
- child：`20260816-121529-270617-bug-loop`；
- operation / execution：`e44ed6747d70430d8388b58d82aa5d0d`；
- 目标 HEAD：`d2c28103d352f251f1bf20d89758e666dba086ed`；
- 目标分支：`codex/sag3b-02-wip`，push URL 禁用。

Worker 只修改：

- `src/vega/execution_control.py`；
- `tests/test_execution_control_safety.py`。

实现把固定约 `0.2` 秒的替换重试改为单调时钟控制的 `1.0` 秒上限、`0.05` 秒间隔；
回归测试使用真实 Windows 读取句柄覆盖约 `0.6` 秒短时锁恢复和持续锁 fail-closed。Worker
本地执行结果为 `4 passed / 70 deselected`、Ruff 通过、`git diff --check` 通过；该结果只
属于 Worker 自检，机器 B 仍必须重新执行 Core Verification、Risk、Reviewer 与 Finish。

首次观测到允许路径 Diff 约在 Worker 启动后 `192.221` 秒；停止请求在该观测后约
`53.089` 秒发出。此时 Worker 已完成本地检查，但尚未返回最终 Claim，execution 仍为
`running`。Vega 只向匹配 child/operation 写入 stop request，最终 execution 为
`stopped`，`termination_unconfirmed=false`，owner/child PID 均已退出；没有启动第二 Writer。

原 `agent run` 返回后，Supervisor 清空 active binding 并进入 `needs_human`。按照上文补充的
两阶段 stop 协议，再次执行 `agent stop` 固化静止 Checkpoint；随后用两个 run-local 审计
Artifact 核对：

- Worker 事件只包含本地命令、文件修改、计划和消息，没有外部工具调用；
- sandbox 网络和额外 writable roots 均关闭；
- 目标仓库只有两个允许文件，无未跟踪文件；
- owned execution 已安全停止。

外部副作用据此裁决为 `none`，`checkpoint-004` 为 `stopped / safe`。执行
`agent checkpoint --handoff` 后生成：

- `checkpoint-005`；
- `handoff_status=handoff_ready`；
- Task Card：`.vega/tasks/2026-08/2026-08-16-sag3b-02-handoff.md`；
- Task Card SHA-256：
  `5e3a7d55c25d4927f672894383a3d103add93f3b05634e6c464c325908b8661d`；
- Handoff Workspace Digest：
  `72d07b2bfade0d0cfad7c25462d73163968019b3ed8d6edcb96b3aa245f13ec9`。

本阶段最初判定为 `machine-a-handoff-ready / machine-b-pending`。它证明真实 Worker 可以在
允许范围内留下 WIP，Vega 能以身份绑定 stop 保留现场、完成副作用裁决并生成可移植 Task
Card。它尚不证明物理跨机器恢复、补丁最终正确、Reviewer 通过或 Gate 3B 通过。

提交前架构门禁要求既有大模块不得继续增长。控制源码随后以
`5d252d4b366e7a1bed1eb8370a4c599401055a21` 固定，但与机器 A 的
`control-runtime-local-r3` 对比后，以下三个控制文件不再字节一致：

- `src/vega/agent_codex_adapter.py`；
- `src/vega/agent_codex_evidence.py`；
- `src/vega/loop_runtime.py`。

差异来自 Claim 失败路径去重和等价的行数压缩，不改变本节记录的机器 A 行为；但第五节已经
预注册“机器 A 和机器 B 必须来自同一个 `control_source_commit`”，不能事后用行为等价替代
字节一致。因此 SAG3B-02 的最终判定收紧为
`machine-a-handoff-ready / formal-gate-nonconforming / machine-b-not-run`。不得继续用
SAG3B-02 的机器 B 结果宣称 Gate 3B 通过。

本地提交前证据：

- Adapter 两个完整分片分别为 `11 passed`；
- Verification 相关 Runtime 分片为 `15 passed / 1 skipped`；
- architecture growth 单测为 `42 passed`；
- 完整节点收集为 `1259 collected`；
- compileall、Ruff、repository hygiene、architecture growth 和 `git diff --check` 通过；
- architecture growth 为 `C901 35→35`、Python 模块 `130→130`。

下一步不是重跑 SAG3B-02，也不是把目标 WIP 复制进控制分支，而是：

1. 让当前控制候选通过 PR CI 并合入主线；
2. 从合并后的同一主线提交预注册新的 `SAG3B-03`，重新冻结
   `control_source_commit / tree / sha256`；
3. 机器 A 和机器 B 都从该提交独立重建控制器；
4. 只有新 Case 完成停止、Handoff、物理换机恢复及新的 Core Gate，才判定 Gate 3B；
5. 在此之前，Gate 3B 和 Gate 3C 均保持未通过。

## 十三、2026-08-16 SAG3B-03 预注册

本节只预注册新的正式 Case，不改写 SAG3B-01 的环境阻断结果，也不把 SAG3B-02 的机器 A
本地阶段证据升级为正式 Gate 证据。SAG3B-03 的唯一目的，是在机器 A 和机器 B 使用同一
已提交控制源码的前提下，重新执行一次真实跨物理机器接力。

### 13.1 冻结控制与输入摘要

控制源码直接由已合入主线的提交通过 `git archive` 导出，不复制工作树，也不包含目标 WIP。
机器 B 必须从同一提交独立重建控制 archive，不能接收机器 A 的控制目录。

```text
case_id = SAG3B-03
prereg_branch = codex/supervisor-gate3b-r3
control_source_commit = be6fce26c227ac14abd1600b48ade063a01f5686
control_repo_root_tree = ea711b8ea32e9fa25806954ded0bde195476d4e6
control_source_pathspec = src/vega
control_source_pathspec_tree = 18da3944c7f42e0cdddcadec70bbdf5373a9bc0b
control_source_file_count = 131
control_source_archive_sha256 = 9403dea789288447f5354b51c0f8e8faa57b4433fe65e060484b420464921c75
control_source_manifest_sha256 = 480096e4f3c2f06aee907ba0566a39c7a7ae742ce4e099668805955ae3d199d4
agent_plan_revision = 1
agent_plan_sha256 = 9f48b01c972e5bec01212f13d5418c412438b115942534a24a24b0603e25e935
runner_config_sha256 = dba63bd3abaf7a8a0950430b6c4d6fbcc40fca45370b0bd853cc42d81fdc6acb
machine_a_protocol_sha256 = e82626e518b2258de464dad15f14ffa9835c5d92b20e40dd6fd567c055c827c1
launcher_sha256 = da4c8ded7d7572168baa852266bc59cccd0f009d3109e8315bd2ff26c807e03c
target_execution_control_sha256 = 288053fa8ff0f22b9f3ed38bc9fae99510ca5f237d1102d98acc5404627d9b01
target_execution_control_test_sha256 = 28e727c06672fad06562e7b76f86f1e3f30ba9fa16e50e6a08a0734a295b0298
target_execution_control_bytes = 37251
target_execution_control_test_bytes = 87052
target_repo_head = be6fce26c227ac14abd1600b48ade063a01f5686
target_repo_root_tree = ea711b8ea32e9fa25806954ded0bde195476d4e6
target_branch = codex/sag3b-03-wip
target_core_autocrlf = false
target_push_url_disabled = true
adapter = codex-exec
worker = gpt-5.6-sol / xhigh
reviewer = gpt-5.6-sol / xhigh
worker_timeout_seconds = 900
reviewer_timeout_seconds = 900
machine_a_attempts = 1
machine_b_attempts = 1
automatic_retries = 0
manual_repairs = 0
replans = 0
```

本地冻结材料位于被忽略的 `.tmp/dogfood/sag3b-03/`。该目录不进入 Git，也不得复制到机器 B。
受 Git 跟踪的本节及其提交历史构成远端预注册记录；机器 A 只能在包含本节的分支已经推送、
远端 HEAD 可核对后启动。

### 13.2 冻结任务

用户目标：

> 修复 Windows 上 `execution.json` 原子发布对短时文件共享锁过于敏感的问题，并补充精确
> 回归测试。合法读取者短时占用目标文件时应在有界等待后成功；持续锁定时仍必须
> fail-closed，不能覆盖旧证据或无限等待。

唯一 Work Item 只允许修改：

```text
src/vega/execution_control.py
tests/test_execution_control_safety.py
```

成功条件固定为：

1. Windows 目标文件被合法读取者短时占用约 `0.6` 秒时，原子发布在有界等待后成功；
2. 持续锁定超过等待上限时抛出明确错误，不把新状态伪装成已发布；
3. 失败路径中的旧 `execution.json` 保持可解析，内容不被截断；
4. 正常成功路径不残留临时发布文件；
5. 不改变 execution lease、heartbeat、stop、recover 或 Windows Job Object 语义；
6. 三条冻结 verification 命令通过；
7. 机器 B 重新执行 Verification、Risk、Reviewer 与 Finish，不接受机器 A 的旧 Gate
   结果作为当前证据。

冻结 verification：

```text
python -m pytest -q -o cache_dir=.tmp/pytest-cache-sag3b03 tests/test_execution_control_safety.py -k execution_model --basetemp=.tmp/pytest-sag3b03
ruff check --no-cache src/vega/execution_control.py tests/test_execution_control_safety.py
git diff --check
```

禁止修改 `.vega.yaml`、Runner、Windows Job、execution process、文档、评测记录或其他路径。
禁止切换 `danger-full-access`、引入自动重试或人工补丁，也禁止 Vega 自动 commit、push、
release、删除文件或写入长期 Memory。

### 13.3 机器 A 停止与 Handoff 条件

机器 A 必须使用 fresh clone，设置 `core.autocrlf=false`，固定目标 HEAD 为
`be6fce26c227ac14abd1600b48ade063a01f5686`，创建 `codex/sag3b-03-wip`，保留 fetch URL
但禁用 push URL。启动前必须验证：

1. 实际 `vega.__file__` 位于冻结 archive 的控制源码目录；
2. `agent capabilities` 通过；
3. Plan、Runner、launcher、目标 HEAD、tree 和两个允许文件的摘要与本节完全一致；
4. 控制工作区与目标 clone 没有旧 SAG3B-01/02 Artifact；
5. 不读取或记录凭据、Provider URL；只允许脱敏核对实际生效的模型与推理强度，
   并确认其为 `gpt-5.6-sol / xhigh`。

首次同时满足以下条件时，机器 A 发出身份绑定的 `agent stop`：

1. 至少一个允许文件出现可解释的 tracked Diff；
2. Agent State 仍绑定 active child 与 operation；
3. execution 尚未进入可信终态；
4. 没有允许路径外变更；
5. 没有已知仓库外副作用。

第一次 stop 只停止匹配的 active Writer。原 `agent run` 返回后，必须再次执行 stop，生成
`operation_started=false` 的静止 Checkpoint，之后才能裁决外部副作用并生成 Handoff。
如果 Worker 在停止请求前已完成全部 Core 流程，本 Case 记录为
`insufficient-handoff-opportunity`，不得更换任务、预算、模型或成功条件。

机器 A 形成 `handoff_ready` 后，只能由操作员暂存并提交两个允许文件与本次 Task Card。
机器 B 只能通过该 Git WIP 分支接收现场；不得接收机器 A 的 `runs/`、Trace、Checkpoint、
SQLite、`.tmp`、虚拟环境、Codex 会话或聊天记录。

### 13.4 正式失败条件

以下任一情况都使 SAG3B-03 保持 `gate-not-passed`：

- 预注册分支未推送，或机器 A 启动时远端 HEAD 与本节所在提交不一致；
- 机器 A/B 未从同一 `control_source_commit` 独立重建控制器；
- 任一冻结摘要、目标 HEAD、tree、允许文件摘要或 Runner 配置不一致；
- 需要第二 Writer、自动重试、人工修改 WIP、放宽 Scope 或降低 fail-closed 语义；
- 没有形成可停止的 partial Diff，或 Handoff 不是 `handoff_ready`；
- 机器 B 不是另一台物理机器，或依赖机器 A 的本地 Artifact；
- 机器 B 没有重新通过 Verification、Risk、Reviewer 和 Finish；
- 出现未知外部副作用、证据缺失、证据过期、工作区漂移或身份绑定冲突。

SAG3B-03 的机器 A 结果可以追加到本文件，但 Gate 3B 的最终结果仍只能在机器 B 完成后追加
到 `eval/real-world-runs.md`。在此之前，Gate 3C 保持冻结。

## 十四、2026-08-16 SAG3B-03 机器 A 结果

SAG3B-03 在远端预注册提交
`0c8b7c53c621d52a685a3139f29479f575d0a1b5` 固定后启动。控制源码、Plan、Runner、
launcher、目标 HEAD/tree、两个允许文件摘要及实际 Codex 模型均与第十三节一致。

正式运行身份：

- Agent run：`20260816-140151-agent`；
- child：`20260816-140320-451167-bug-loop`；
- operation / execution：`bdd042704f4347ebb1d1a8c01b2c3af7`；
- Worker：`gpt-5.6-sol / xhigh`；
- 目标基线：`be6fce26c227ac14abd1600b48ade063a01f5686`；
- 目标分支：`codex/sag3b-03-wip`，本地 push URL 在运行期间保持禁用。

Worker 只修改：

- `src/vega/execution_control.py`；
- `tests/test_execution_control_safety.py`。

WIP 把固定约 `0.2` 秒的原子替换重试改为单调时钟控制的 `1.0` 秒上限，仍保持
`0.02` 秒重试间隔；测试使用真实 Windows 读取句柄覆盖约 `0.6` 秒短时锁恢复和持续锁
fail-closed。机器 A 停止时 Diff 为 `126 insertions / 7 deletions`，没有允许路径外变更或
未跟踪文件。

首次允许路径 Diff 出现且 child/operation 仍 active 后，机器 A 发出身份绑定 stop。停止请求
距 dispatch 约 `167.077` 秒；execution 最终为 `stopped`、`returncode=1`、
`termination_unconfirmed=false`，owner 与 child 身份探测均为 `gone`。原 `agent run`
返回后再次执行 stop，生成 `operation_started=false` 的 `checkpoint-003`。

第二次 stop 的状态卡使用了“Workspace 控制信息不完整”的泛化提示，但重新采集的
`git_control_complete=true`、unsafe index 为空；实际阻断原因是
`external_side_effects=unknown`。本 Case 不修改该提示文案。机器 A 随后生成两份 run-local
审计：

- Worker 事件只有本地读取、两个文件修改、定向 pytest、Ruff 与 Git 只读命令，没有外部
  工具调用、Git 写入、删除、包安装或网络命令；
- Codex sandbox 禁止网络、没有额外 writable root，并关闭 hooks、memories、plugins 与
  multi-agent；
- owned execution 已停止，Supervisor 无 active binding，Workspace 只有两个允许文件，
  Git control 和 capture 完整。

人工裁决把外部副作用从 `unknown` 解析为 `none`，Vega 追加不可变 adjudication 和
`checkpoint-004 / stopped / safe`。随后 `checkpoint-005` 生成 ready Handoff：

```text
handoff_status = handoff_ready
task_card = .vega/tasks/2026-08/2026-08-16-sag3b-03-handoff.md
task_card_sha256 = 0baa69e82add42b52e08917a60935cd0cc8aec3e832af579c296ea7538e761ab
handoff_workspace_digest = 947a8ce875b6e7a303b60de66c17230d45e9d60f585ea619af7d1f5d11f0bee9
machine_a_result_sha256 = 7ee51b2ce9b9bb67115e4ecd4d53283ed47e6c084d3428ca96b7521a9b71f606
```

操作员只提交 Task Card 与两个允许文件。本地审计提交
`d45058ad5e6e2604e14bf418729ed7ef079a9ec1` 因 GitHub `GH007` 私密邮箱保护未被推送；
没有修改全局 Git 配置或重写该提交。随后从同一父提交和同一 tree 创建 noreply 等价提交并
首次发布远端 WIP 分支：

```text
remote_handoff_commit = 065a42338da5956d410b632dc0c89f9cbdd05a07
remote_handoff_tree = f790e996fbc61ff17e4ebf240ecde6b5f33326ae
remote_branch = codex/sag3b-03-wip
```

控制仓库重新 fetch 后确认远端提交父节点为 `be6fce2`，只包含 Task Card 与两个允许文件，
`git diff --check` 通过。该发布方式只解决 GitHub 邮箱隐私拒绝，WIP tree 没有变化。

机器 A 结果判定为 `machine-a-handoff-ready / machine-b-pending`。Verification、Risk、
Reviewer 和 Finish 均保持 `not_run` 或 historical，没有被解释为通过。只有另一台物理机器
从远端 WIP 分支与同一控制提交独立恢复、重新完成四个 Core Gate 后，SAG3B-03 才可能通过
Gate 3B；在此之前 Gate 3B 与 Gate 3C 均保持未通过。

## 十五、2026-08-16 机器 B 同机隔离模拟

本节记录一次同机、全新目录、只经 Git 接收 WIP 的机器 B 模拟。它用于验证本地恢复链和
发现协议缺口，不等同于第七节要求的另一台物理机器，也不能把 SAG3B-03 升级为 Gate 通过。
本次只消费远端 `codex/sag3b-03-wip`，没有复制机器 A 的 `runs/`、Trace、Checkpoint、
控制目录、虚拟环境、Codex 会话或未提交文件。

### 15.1 隔离与预检

模拟目录位于项目内被忽略的 `.tmp/dogfood/`。目标仓库使用 fresh clone，并在第一次
checkout 前显式设置 `core.autocrlf=false`；fetch URL 保留，push URL 禁用。核对结果如下：

```text
control_source_commit = be6fce26c227ac14abd1600b48ade063a01f5686
control_repo_root_tree = ea711b8ea32e9fa25806954ded0bde195476d4e6
control_source_pathspec_tree = 18da3944c7f42e0cdddcadec70bbdf5373a9bc0b
control_source_file_count = 131
remote_handoff_commit = 065a42338da5956d410b632dc0c89f9cbdd05a07
remote_handoff_tree = f790e996fbc61ff17e4ebf240ecde6b5f33326ae
task_card_sha256 = 0baa69e82add42b52e08917a60935cd0cc8aec3e832af579c296ea7538e761ab
launcher_sha256 = da4c8ded7d7572168baa852266bc59cccd0f009d3109e8315bd2ff26c807e03c
worker = gpt-5.6-sol / xhigh
```

冻结 launcher 在模拟目录内重新生成，摘要与机器 A 预注册值一致；实际 `vega.__file__`
指向模拟控制源码，而不是目标 WIP checkout。`agent capabilities`、Codex 登录状态和目标
分支身份均通过，目标工作树启动前干净。

预检同时发现一个协议可移植性缺口：相同 commit 和 pathspec 在
`core.autocrlf=false` 下直接执行 `git archive`，得到
`7c66d6c6c051fdc020d4100be570247023460bb1133db12dc08b1e10e16d3f8f`，不是预注册 tar
摘要。显式执行 `git -c core.autocrlf=true archive` 后才复现
`9403dea789288447f5354b51c0f8e8faa57b4433fe65e060484b420464921c75`。两次导出引用同一
Git tree，但 tar 内文本行尾不同。后续正式协议必须显式冻结 archive 的 EOL 配置，稳定
Git tree 和 blob manifest 应作为主要源码身份，不能默认把 tar 字节摘要视为跨机器天然稳定。

### 15.2 恢复与 Core 结果

`agent resume --repo <target-repo>` 成功从 Git Task Card 创建新本机 run：

```text
agent_run = 20260816-144519-agent-resume
child_run = 20260816-144638-275022-bug-loop
operation_id = 2003fb3cd10544f194d6ae31e5df1bf0
resume_checkpoint = checkpoint-001 / safe
```

恢复后的 task、Goal revision、Plan revision、批准摘要和当前 Work Item 均与 Task Card
一致；旧门禁只作为 historical 信息，当前 run 没有复用机器 A 的 Verification、Risk 或
Reviewer Artifact。目标分支相对 `handoff_base_revision` 只有 Task Card 和两个允许文件，
没有允许路径外变更。

唯一机器 B Worker 成功退出。它识别到 WIP 已实现冻结目标，执行 Ruff 和基线到 WIP 的
`git diff --check` 后没有重复修改代码。随后 Core 重新运行三条冻结验证：

```text
pytest = 4 passed, 70 deselected
ruff = passed
git_diff_check = passed
```

但是本轮最终为：

```text
child_finish = needs_human
child_current_step = no_diff
scope_pre_verification = skipped
scope_post_verification = skipped
risk = skipped
reviewer = skipped
supervisor_phase = needs_human
gate_result = gate-not-passed
```

根因不是冻结验证失败，而是 committed handoff WIP 的基线语义不完整。机器 B checkout 的
当前 HEAD 已经包含机器 A 提交的实现；Core 仍把当前 HEAD 当作 workspace baseline，只检查
未提交 Diff，因此得到 `changed_files=[]`，没有把
`handoff_base_revision..HEAD` 作为当前应审查的变更集。结果是 Verification 命令虽然通过，
Scope、Risk 和 Reviewer 仍因“无 Diff”被跳过，Finish 正确 fail-closed。

外层 Supervisor 将该 child 结果汇总为 `Verification=blocked`，而 child Artifact 实际记录
`verification_status=passed`。该汇总措辞不影响 fail-closed 结果，但会模糊“测试失败”和
“验证通过但审查证据不足”的区别，后续可在不改变状态机的前提下单独修正。

### 15.3 安全与现场检查

- 目标 worktree 在运行后仍干净，HEAD 保持 `065a423`，没有自动 commit 或 push；
- 所有 Worker 和 Verification owner/child PID 均已退出；
- 运行 Artifact 中未发现凭证形态值；唯一 `Authorization` 命中是源码标识符，不是 Header
  或 token；
- 测试仅在目标仓库被忽略的 `.tmp` 下产生临时目录，没有写到工作区集合根目录或其他项目；
- 没有启动第二 Writer、自动重试、人工修改 WIP、replan 或放宽 Scope。

### 15.4 阶段结论与下一步

本次证明了以下能力：

1. 新目录可以只依赖远端 WIP 与 Git Task Card 恢复 Supervisor run；
2. Goal、Plan、Work Item、限制和 historical Gate 能跨本机 run 重建；
3. Worker 能识别已提交 WIP，避免重复修改；
4. 冻结 Verification 能在恢复后重新执行并通过；
5. 证据不完整时 Supervisor 和 Finish 会 fail-closed，而不是误报成功。

本次没有证明：

1. 另一台物理机器上的恢复；
2. committed WIP 能完整进入 Scope、Risk、Reviewer 和 Finish；
3. Gate 3B 或 Gate 3C 已通过。

下一步只做两个窄修复，不扩大 Agent 功能：

1. 为 `agent resume` 增加 committed handoff baseline 语义：以 Task Card 的
   `handoff_base_revision` 为审查基线，把 `base..HEAD` 与后续 worktree 变更统一编译为
   当前变更集；不得通过 soft reset、重写 WIP commit 或重新制造脏工作树实现；
2. 固定控制 archive 的 EOL 参数，并让 tree/blob manifest 成为跨机器主要身份。

回归测试只需覆盖一个关键场景：clean checkout 位于 handoff WIP commit，Task Card 指向旧
base；恢复后 Scope 必须看到两个允许代码文件，Verification、Risk、Reviewer 和 Finish
必须重新运行。完成修复后新建独立 Case，不重跑或覆盖本次 SAG3B-03 结果。

## 十六、2026-08-16 committed handoff 修复结果与 SAG3B-04 准备

### 16.1 修复后的证据基线

本分支完成了第 15.4 节要求的窄修复，没有增加新的调度层或第二套 Core：

1. `initial_head_sha` 继续绑定恢复时的当前 WIP HEAD，负责阻止运行期间的 HEAD 漂移；
2. 新增独立 `comparison_base_sha`，固定为 Task Card 的
   `handoff_base_revision`；
3. `comparison_paths` 只包含 Resume Capsule 登记的 WIP 文件，不把 Task Card 或其他
   提交文件交给 Worker、Risk 或 Reviewer 作为代码变更；
4. resume 预检要求 `comparison_base..HEAD` 的提交路径恰好等于 Capsule 文件与当前
   Task Card，缺少文件或出现未登记文件均拒绝恢复；
5. committed、staged、unstaged 与 untracked 四类事实统一进入 Scope、Verification
   workspace fingerprint、Reflect、Risk、Reviewer freshness 和 Finish；
6. Adapter 首次执行仍拒绝 staged、unstaged 或 untracked 污染，只允许已经完成上述对账的
   committed WIP；
7. comparison path 使用 Git literal pathspec，非法、重复或被篡改的路径作为证据问题
   fail-closed；
8. Supervisor 可以准确记录 `Verification=passed`，但只有 `ready_to_commit` 且 Finish
   Artifact 完整、新鲜时才把 Work Item 标为完成，因此不会因措辞修正放松终态。
9. 恢复校验、Agent State 与 run metadata 复用同一份 Handoff HEAD 快照；校验后若
   HEAD 被另一进程推进，恢复立即拒绝，未登记提交不能被 comparison path 过滤掉。

普通 `vega do`、`vega loop` 和没有 comparison binding 的 assist run 继续使用原有
HEAD、index 与 worktree 语义。

### 16.2 本地机械回归

本轮按仓库 60 秒分片规则运行，取得以下明确汇总：

```text
Supervisor Codex Adapter：25 passed
Agent handoff：18 passed, 1 skipped
Task Card resume：1 passed
committed handoff Core E2E：1 passed
evidence freshness：30 passed
review artifact integrity：32 passed
finish artifact integrity：19 passed
success semantics：29 passed
scope/path matching：52 passed
workspace baseline：18 passed
finish policy：11 passed
required risk review loop：6 passed
verification capture compatibility：2 passed
hardened Git security：9 passed
scope evidence P0 regressions：11 passed
合计：264 passed, 1 skipped
```

该证据证明实现可让 clean committed handoff WIP 重新经过 Scope、Verification、Reflect、
Risk、Reviewer 与 Finish，并维持原有 fail-closed 语义。它不替代 PR CI，也不证明另一台
物理机器上的 Gate 3B 已通过。

### 16.3 SAG3B-04 控制源码身份协议

SAG3B-04 必须从修复合入后的同一主线提交重新预注册。机器 A 与机器 B 均独立执行以下
等价命令，不相互传递已展开的控制目录：

```powershell
git -c core.autocrlf=false rev-parse "<runtime-commit>^{tree}"
git -c core.autocrlf=false ls-tree -r --full-tree "<runtime-commit>"
git -c core.autocrlf=false archive --format=tar --output="<archive-path>" "<runtime-commit>"
```

身份判定顺序固定为：

1. 完整 runtime commit；
2. commit tree；
3. `ls-tree` 产生的路径、mode 与 blob OID manifest；
4. archive SHA-256。

前三项是源码身份的主要证据。archive SHA-256 只用于显式
`core.autocrlf=false`、相同导出命令下的传输校验；不得再把不同 EOL 配置产生的 tar
字节差异解释为源码 tree 不同。

### 16.4 停止条件与下一 Case

SAG3B-03 的历史结果保持 `gate-not-passed`，不得用本轮单元测试或同机模拟覆盖。下一次
正式 Case 命名为 `SAG3B-04`，开始前必须同时满足：

1. 本修复已合入主线且 PR CI 全部通过；
2. 从合并提交重新冻结 runtime commit、tree、blob manifest 与 archive 参数；
3. 使用新的目标任务分支、Task Card、run ID 和 evidence 目录；
4. 机器 B 是另一台物理机器，并只从远端 Git 与预注册协议恢复；
5. Scope、Verification、Risk、Reviewer 和 Finish 均在机器 B 重新运行并形成完整 Artifact。

在 SAG3B-04 完成前，Gate 3B 和 Gate 3C 都保持未通过。

截至 2026-08-16，条件 1 已由 PR `#63` 和 `main@435767a` 满足；条件 2～5 仍未执行。
本段记录当时的验收边界；SAG3B-04 当前采用第十七节的独立 fresh clone 修订，仍不得复用或
改写 SAG3B-03 现场。

## 十七、2026-08-17 Gate 3B 验收边界修订与 SAG3B-04 预注册

### 17.1 为什么不再把另一台物理机器作为硬门禁

SAG3B-03 已经证明 fresh clone 可以只依赖 Git Task Card 恢复 Goal、Plan、Work Item 和
historical Gate，但当时的 committed handoff 基线缺口使 Scope、Risk 和 Reviewer 没有继续
运行。该缺口已经合入主线。

Gate 3B 真正需要验证的是：

1. 恢复端不读取生产端的 `runs/`、Trace、Checkpoint、虚拟环境、聊天或未提交文件；
2. WIP 与 Task Card 只通过远端 Git 传递；
3. 恢复端使用独立 clone、独立本机 run 和重新构建的固定控制器；
4. Scope、Verification、Risk、Reviewer 与 Finish 全部重新运行；
5. Workspace、HEAD、comparison base 和 Task Card 任一不一致时继续 fail-closed。

另一台物理机器可以提高环境多样性，但不是上述合同成立的必要条件。并且物理设备并不天然
保证环境独立：共享用户目录、同步盘、凭据或运行目录同样可能污染证据。因此从 SAG3B-04
开始，正式门禁改为：

```text
machine A fresh clone
  → 真实 Codex Worker 形成未完成 WIP
  → 停止、对账、Handoff
  → 人工 commit/push
  → machine B fresh clone 只从远端 Git 获取
  → 独立恢复并重新运行全部 Core Gate
```

这里的 machine A/B 是两个独立执行环境的协议称呼，不再要求对应两台物理设备。真实换机
以后只作为更强的现场观察追加，不阻塞 `v0.2.0` 发布。SAG3B-01～03 的历史判定保持不变，
本节不改写此前预注册条件或实验结果。

### 17.2 隔离要求

SAG3B-04 的两个环境必须同时满足：

- 使用两个独立 `git clone`，不能使用共享 `.git` 的 worktree；
- machine B 只能从远端分支获得 WIP 和 Task Card；
- 不复制 machine A 的 `runs/`、`.tmp/`、Trace、Checkpoint、SQLite、虚拟环境或聊天；
- 两端分别从同一个冻结 commit 导出控制源码，实际 `vega.__file__` 必须指向各自控制快照；
- machine B 创建新的 Agent run，旧 Verification、Risk 和 Reviewer 只作为 historical；
- 两端均禁止 Vega 自动 commit、push、release、删除文件或写入长期 Memory。

同一宿主操作系统、Python 或 Codex 安装可以复用，但这些共享条件必须在结果中披露，不能把
本 Case 宣传为跨操作系统或跨硬件验证。

### 17.3 冻结任务

Case ID：`SAG3B-04`

用户目标：

> 修复 Agent 状态展示在 Worker 已结束并清除 active binding 后，把本次真实 Worker attempt
> 显示为“未启动”或丢失 latest child 的问题。状态卡与通用 status/watch 应继续显示最近一次
> 已对账的 child，但不能把它重新标记为 active。

唯一 Work Item：

```yaml
id: W1
objective: 在清除 active Writer 后保留最近一次已对账 Worker attempt 的只读状态展示
allowed_paths:
  - src/vega/agent_runtime_support.py
  - src/vega/agent_run_status.py
  - tests/test_agent_runtime.py
  - tests/test_agent_codex_adapter.py
forbidden_paths:
  - src/vega/agent_contract.py
  - src/vega/agent_persistence.py
  - src/vega/agent_codex_adapter.py
  - src/vega/agent_graph.py
  - docs/**
  - eval/**
verification:
  - python -m pytest -q tests/test_agent_runtime.py::test_generic_status_retains_latest_child_after_binding_is_cleared tests/test_agent_codex_adapter.py::test_agent_success_path_preserves_completed_worker_in_status_card
  - ruff check --no-cache src/vega/agent_runtime_support.py src/vega/agent_run_status.py tests/test_agent_runtime.py tests/test_agent_codex_adapter.py
  - git diff --check
```

成功条件：

1. active Writer 存在时仍显示当前 child，不改变单 Writer 或 operation 绑定；
2. Worker 完成并清除 active binding 后，`status-card.md` 继续显示本次已对账 child；
3. 通用 `status/latest/watch` 的 `last_child_run` 与 `brief_run` 继续指向最近一次可信 child；
4. 没有 Worker 历史时仍显示“未启动”，不能凭文件名或不可信外部 Claim 生成 child；
5. 不新增 Agent State 字段、Schema、事件账本或第二套状态数据库；
6. 不把 Worker 聊天、自述或内部推理传给 Reviewer；
7. machine B 重新执行三条冻结 Verification，并形成新的 Risk、Reviewer 与 Finish Artifact。

已确认事实：

- `write_status_card()` 当前只使用 `state.active_child_run` 生成 `worker_label`；
- `load_agent_status_state()` 当前把 `last_child_run` 与 `brief_run` 都设置为
  `state.active_child_run`；
- 可信 Observation 已记录 `child_run`，append-only Agent Trace 也记录 child 身份；
- Supervisor 完成对账后会清除 active child/operation，这代表 Writer 已退出，不代表历史
  attempt 从未发生。

假设：

- 状态卡可以优先使用 active child，其次使用当前可信 Observation 的 child；
- 通用状态可以在没有 active child 时，从已验证 Trace 中恢复最近 child；
- 无需新增持久化状态字段或放松现有状态权威。

### 17.4 固定基线与预算

预注册起点：

```text
main_base = 012700b6caca0450f820ff374082ae9216bc065f
target_branch = codex/sag3b-04-status-visibility
adapter = codex-exec
work_item_count = 1
machine_a_attempts = 1
machine_b_attempts = 1
automatic_retries = 0
manual_repairs = 0
replans = 0
worker_timeout_seconds = 900
reviewer_timeout_seconds = 900
```

包含本节的预注册提交必须先推送并核对远端 HEAD。随后该提交同时作为：

- machine A/B 的固定控制源码 commit；
- machine A 目标分支的 Handoff base；
- machine B 恢复时重新构建控制器的唯一来源。

machine A 只在第一次出现允许路径 Diff 且 Writer 仍 active 时发送一次身份绑定 stop。若
Worker 在停止前已经形成可信终态，本 Case 记录为 `insufficient-handoff-opportunity`，不通过
人工制造脏工作树补造 Handoff。

### 17.5 通过标准

SAG3B-04 通过必须同时满足：

```text
git_only_isolated_handoff = 1
fresh_clone_count = 2
work_item_count = 1
control_source_commit_match = 1
task_card_only_resume = 1
duplicate_writer_start = 0
stale_gate_evidence_accepted = 0
automatic_git_write = 0
false_success = 0
```

并且 machine B 的 Scope、Verification、Risk、Reviewer 与 Finish 全部形成新的、彼此一致的
Artifact。真实物理换机不再是 Gate 3B 和 `v0.2.0` 的硬前置条件。

## 十八、SAG3B-04 实际结果与 SAG3B-05 预注册

### 18.1 SAG3B-04 实际结果

SAG3B-04 按第十七节使用两个独立 fresh clone 执行，没有共享 `.git`、`runs/`、`.tmp/`、
Checkpoint、Trace、虚拟环境或聊天。两端均从同一控制提交独立导出固定控制源码：

```text
control_source_commit = e4ca7c31c18f5c362b97dccf711a607f08470e11
control_source_tree = fa8c3df541cb3df7ea6b80678c2d5bea387601c1
control_source_archive_sha256 = 6e6e304d4e587ad08e4fb62c28b857a2a15a92391ecd824cc8d87d8f08b35e15
machine_a_agent_run = 20260817-113413-agent
machine_a_child_run = 20260817-113539-074896-bug-loop
machine_a_operation = f859f3a632d645dcb5d09b76d3352810
handoff_commit = 8848541261e466220f9e68076207b06961039af0
task_card_sha256 = 52729d5616b9bd463ae660fbbdce5c7800368d1f1ce867e303740290e356d900
machine_b_agent_run = 20260817-114943-agent-resume
machine_b_child_run = 20260817-115021-127727-bug-loop
machine_b_operation = 4776b8fe19674275bc20fd450ee4a8de
```

机器 A 在允许路径出现首个 tracked Diff 后发送身份绑定的 stop，等待 owned Worker 退出，
完成 Workspace 与外部副作用人工裁决，并生成 Git Task Card。人工只提交四个允许文件与
Task Card；机器 B 只通过远端分支取得这些内容，并成功执行 `agent resume`，建立新的本机
Agent run、Checkpoint、Task Brief 和真实 Codex child。旧 Verification、Risk 与 Reviewer
均保持 historical，没有被当作当前通过证据。

机器 B 的真实 Worker 返回 `completed` Claim，且没有再修改 tracked 文件。但现有 Core 在
Verification 前执行 Workspace Gate 时得到：

```text
status = needs_human
current_step = workspace_check_failed
baseline_tracked_changes_present = false
baseline_untracked_changed = false
baseline_ignored_changed = true
git_control_changed = false
verification = skipped
risk = skipped
reviewer = skipped
```

新增 ignored 路径来自 Worker 自检：

```text
.tmp/pytest/runs/pytest-28816/
```

本仓库 `tests/conftest.py` 会在未显式提供 `--basetemp` 时，把 pytest 临时目录放到
`.tmp/pytest/runs/`。SAG3B-04 冻结命令没有使用 `{{vega_verification_temp}}`，真实 Worker
按任务中的 pytest 命令自检后改变了 ignored 清单。普通 `git status` 没有显示该变化，但
Vega 的 ignored Workspace 证据正确发现并阻断了后续 Gate。

因此本 Case 的判定固定为：

```text
git_only_isolated_handoff = pass
task_card_only_resume = pass
fresh_child_dispatch = pass
workspace_gate = failed
verification_risk_review_finish = not_run
gate_3b = not_passed
false_success = 0
```

该结果不重跑、不清理后继续，也不改写为“基本通过”。它证明了 Git-only 交接和恢复路径，
同时暴露了预注册验证命令与 Worker ignored 现场约束不兼容。SAG3B-04 的失败 Artifact 保留
在本机；公开文档只记录必要摘要，不提交原始运行日志或本机路径。

### 18.2 为什么不放宽 ignored Workspace Gate

备选方案是把整个 `.tmp/pytest/` 加入 Workspace Gate 豁免。该方案会让任意 Worker 写入
普通 ignored 测试目录而不被发现，扩大了当前明确的 harness-owned 豁免边界，因此拒绝。

SAG3B-05 使用现有受控机制解决：

1. pytest 冻结命令显式使用 `{{vega_verification_temp}}`；
2. Worker Prompt 已明确禁止 Worker 自行运行包含该占位符的 harness-owned 命令；
3. 只有 Vega Core 在 Verification 阶段展开占位符并创建独占临时目录；
4. `.tmp/pytest/` 等其他 ignored 路径继续参与 Workspace Gate。

这只修正实验协议，不修改 Workspace、Verification 或 fail-closed 的生产语义。

### 18.3 SAG3B-05 冻结任务

Case ID：`SAG3B-05`

目标分支继续使用 `codex/sag3b-04-status-visibility`，不再创建额外功能分支。冻结起点为
SAG3B-04 Handoff 提交 `8848541261e466220f9e68076207b06961039af0`；包含本节的预注册
提交在真实 Worker 启动前推送并登记为两端唯一控制源码提交。

用户目标：

> 加固 Agent 状态展示的执行身份核对：状态卡和通用 status/watch 在保留最近 child 时，
> 必须同时验证 operation 绑定。State 或可信 Observation 只要 operation 与最近一次
> `worker_dispatch_committed` Trace 不一致，就拒绝展示，不能只比较 child ID。

唯一 Work Item：

```yaml
id: W1
objective: 让历史 child 展示同时核对 child 与 operation 身份
allowed_paths:
  - src/vega/agent_run_status.py
  - tests/test_agent_runtime.py
  - tests/test_agent_codex_adapter.py
forbidden_paths:
  - src/vega/agent_contract.py
  - src/vega/agent_persistence.py
  - src/vega/agent_runtime_support.py
  - src/vega/agent_worker.py
  - docs/**
  - eval/**
verification:
  - python -m pytest -q -p no:cacheprovider -o cache_dir={{vega_verification_temp}}/cache --basetemp={{vega_verification_temp}}/runs tests/test_agent_runtime.py::test_generic_status_retains_latest_child_after_binding_is_cleared tests/test_agent_runtime.py::test_agent_status_rejects_active_operation_trace_mismatch tests/test_agent_runtime.py::test_agent_status_rejects_observation_operation_trace_mismatch tests/test_agent_codex_adapter.py::test_agent_success_path_preserves_completed_worker_in_status_card
  - ruff check --no-cache src/vega/agent_run_status.py tests/test_agent_runtime.py tests/test_agent_codex_adapter.py
  - git diff --check
```

成功条件：

1. active child 与最近 dispatch Trace 的 child 或 operation 任一不一致时，状态读取
   fail-closed；
2. 可信 Observation 与最近 dispatch Trace 的 child 或 operation 任一不一致时，状态卡写入
   fail-closed；
3. `external_claim` 仍不能成为可信完成或身份来源；
4. 同一 child 在 repair 中使用新的 operation 时，以最近一次 dispatch 绑定为准，不误报历史
   operation；
5. 正常 active child、已清除 active binding 的最近 child，以及从未启动 Worker 的状态展示
   保持原行为；
6. 不新增 State 字段、Schema、事件账本、自动重试或新的成功语义；
7. machine B 重新形成 Workspace、Scope、Verification、Risk、Reviewer 与 Finish Artifact。

### 18.4 SAG3B-05 固定预算与通过标准

```text
adapter = codex-exec
work_item_count = 1
machine_a_attempts = 1
machine_b_attempts = 1
automatic_retries = 0
manual_repairs = 0
replans = 0
worker_timeout_seconds = 900
reviewer_timeout_seconds = 900
```

执行方式继续遵循第十七节：

```text
machine A fresh clone
  → 真实 Codex Worker 形成首个允许范围 tracked Diff
  → 身份绑定 stop、进程与 Workspace 对账、人工副作用裁决
  → Handoff Task Card
  → 人工 commit/push
machine B fresh clone
  → 只从远端 Git 拉取
  → 显式选择 SAG3B-05 Task Card 执行 resume
  → 新真实 Codex child
  → 重新执行全部 Core Gate
```

SAG3B-05 只有同时满足以下条件才通过：

```text
git_only_isolated_handoff = 1
fresh_clone_count = 2
control_source_commit_match = 1
task_card_only_resume = 1
duplicate_writer_start = 0
worker_ignored_workspace_change = 0
workspace_scope_verification_risk_review_finish = pass
automatic_git_write = 0
false_success = 0
```

SAG3B-05 未通过前，Gate 3B 继续保持未通过，不能发布 `v0.2.0`。

## 十九、SAG3B-05 实际结果与 SAG3B-06 预注册

### 19.1 SAG3B-05 实际结果

SAG3B-05 的 machine A 使用独立 fresh clone，并从预注册提交独立导出固定控制源码：

```text
control_source_commit = 11ec47d8b918ac764e76d81085214e95a4cd217b
control_source_tree = e549487608a74fb835599b03e46db82b6b467aeb
control_source_archive_sha256 = 09ac0c7979b38e6c574f0606191aa5d5e3a92345709a54911301b6343fa6cb31
plan_sha256 = 7325cfa9e6b5e44e859873c3d9f5af533e55ead4ecd71f62482bfd11efd049b3
machine_a_agent_run = 20260817-122655-agent
machine_a_child_run = 20260817-123446-775354-bug-loop
machine_a_operation = d5c8624601484bbab2984e2d9d061878
```

真实 Codex Worker 启动后，进程树显示它仍继承并启动了用户配置中的外部 MCP Server。现有
Runner 虽然已禁用 hooks、memories、plugins、多 Agent、sandbox shell 网络和额外可写根
目录，但这些开关不会自动关闭 `mcp_servers`。外部 MCP 的启动参数还可能携带敏感连接配置，
因此不能把该现场裁决为 `external_side_effects=none`。

发现后立即向身份绑定的 Worker 发送 stop，并在没有 tracked Diff 的情况下完成进程与
Workspace 对账：

```text
worker = stopped
tracked_diff = none
phase = needs_human
checkpoint = checkpoint-002
external_side_effects = unknown
verification = not_run
risk = not_run
reviewer = not_run
finish = not_run
```

因此本 Case 的判定固定为：

```text
fresh_clone_machine_a = pass
real_worker_dispatch = pass
mcp_isolation = failed
git_handoff = not_run
machine_b_resume = not_run
workspace_scope_verification_risk_review_finish = not_run
gate_3b = not_passed
false_success = 0
```

该结果不重跑、不改写成预检失败，也不把 Worker 被及时停止解释为无外部副作用。原始运行
Artifact 只留在本机，外部 MCP 名称、启动参数和敏感配置不进入公开文档。

### 19.2 MCP 隔离修复边界

SAG3B-05 暴露的是 Supervisor Writer 启动边界缺口，不是状态身份任务本身的失败。修复只
作用于 `single_writer` 的 Supervisor Worker：

1. 在 Worker 子进程启动前调用 Codex 自身的 MCP 配置解析命令；
2. 只从输出中读取 Server 名称和启用状态，不记录 transport、命令、参数、环境变量或
   stderr；
3. 对每个有效 Server 生成 `mcp_servers.<name>.enabled=false` 覆盖；
4. 使用相同 profile 和覆盖再次解析，确认 Server 集合没有变化且全部关闭；
5. 解析失败、超时、标识不受支持、集合变化或仍有启用项时，写入 preflight failure，
   不启动 Worker。

不采用 `mcp_servers={}`：Codex 配置层使用合并语义，空表不能证明已经继承的 Server 被
移除。当前修复也不扩展为通用工具策略引擎，不修改普通 Loop 的 Worker/Reviewer 行为，
不读取或复制 MCP 的敏感配置。

### 19.3 SAG3B-06 冻结任务

Case ID：`SAG3B-06`

目标分支继续使用 `codex/sag3b-04-status-visibility`。包含本节和 MCP 隔离修复的提交必须
先推送并核对远端 HEAD；该提交随后作为 machine A/B 唯一固定控制源码。

用户目标保持不变：

> 加固 Agent 状态展示的执行身份核对：状态卡和通用 status/watch 在保留最近 child 时，
> 必须同时验证 operation 绑定。State 或可信 Observation 只要 operation 与最近一次
> `worker_dispatch_committed` Trace 不一致，就拒绝展示，不能只比较 child ID。

唯一 Work Item：

```yaml
id: W1
objective: 让历史 child 展示同时核对 child 与 operation 身份
allowed_paths:
  - src/vega/agent_run_status.py
  - tests/test_agent_runtime.py
  - tests/test_agent_codex_adapter.py
forbidden_paths:
  - src/vega/agent_contract.py
  - src/vega/agent_persistence.py
  - src/vega/agent_runtime_support.py
  - src/vega/agent_worker.py
  - src/vega/runner.py
  - src/vega/codex_mcp_isolation.py
  - docs/**
  - eval/**
verification:
  - python -m pytest -q -p no:cacheprovider -o cache_dir={{vega_verification_temp}}/cache --basetemp={{vega_verification_temp}}/runs tests/test_agent_runtime.py::test_generic_status_retains_latest_child_after_binding_is_cleared tests/test_agent_runtime.py::test_agent_status_rejects_active_operation_trace_mismatch tests/test_agent_runtime.py::test_agent_status_rejects_observation_operation_trace_mismatch tests/test_agent_codex_adapter.py::test_agent_success_path_preserves_completed_worker_in_status_card
  - ruff check --no-cache src/vega/agent_run_status.py tests/test_agent_runtime.py tests/test_agent_codex_adapter.py
  - git diff --check
```

machine A 在派发前必须先看到 MCP 隔离 preflight 通过。真实 Worker 运行期间只观察进程身份、
存活状态和 Artifact，不再读取完整命令行；如出现未知外部工具进程、外部副作用无法裁决或
隔离配置漂移，本 Case 立即保持失败。

### 19.4 SAG3B-06 固定预算与通过标准

```text
adapter = codex-exec
work_item_count = 1
machine_a_attempts = 1
machine_b_attempts = 1
automatic_retries = 0
manual_repairs = 0
replans = 0
worker_timeout_seconds = 900
reviewer_timeout_seconds = 900
```

执行顺序继续使用第十七节的 Git-only 双 fresh clone 协议。除原通过条件外，新增以下硬条件：

```text
mcp_isolation_preflight = pass
inherited_mcp_process_started = 0
external_side_effects_machine_a = none
external_side_effects_machine_b = none
tracked_secret_or_mcp_config = 0
```

SAG3B-06 只有同时满足以下条件才通过：

```text
git_only_isolated_handoff = 1
fresh_clone_count = 2
control_source_commit_match = 1
task_card_only_resume = 1
duplicate_writer_start = 0
worker_ignored_workspace_change = 0
workspace_scope_verification_risk_review_finish = pass
automatic_git_write = 0
false_success = 0
```

SAG3B-06 未通过前，Gate 3B 继续保持未通过，不能发布 `v0.2.0`。

## 二十、SAG3B-06 实际结果与 Reviewer 隔离补充

### 20.1 machine A 结果

SAG3B-06 machine A 使用独立 fresh clone 和固定控制源码：

```text
control_source_commit = 7f1a3989da58e51761e043850fcf4e8d8a6380a8
control_source_tree = a290413ba9176a967f73a8b78e9e171af09c5b39
control_source_archive_sha256 = eaef0fe169b392c6bec89fce12f73c7b92fe9923ca4a2e0025632eddc28def28
plan_sha256 = c8b163084a9a672086f86ca5296ff31df4721539f782625a63009fc09b50afc2
machine_a_agent_run = 20260817-132935-agent
machine_a_child_run = 20260817-133147-913617-bug-loop
machine_a_operation = 241db9a22b374b51adc9e32ebe1ca29b
handoff_commit = 49faf18766e232d56fc2693efb0b06d422000acd
task_card_sha256 = de9b12fd011bd839d4954c8022561e21d9bc22195be3c5e615731769000de469
```

Writer 启动前，MCP 探针发现 5 个有效配置项；逐项覆盖后复查为 0 个启用项。owned execution
命令包含 5 个禁用覆盖。两次进程树快照只看到 Codex 自身启动链和 Worker 的短生命周期
PowerShell，没有额外持久 MCP Server。Codex JSONL 也没有 MCP、Web、浏览器或其他外部
工具事件。

第一次出现允许路径 tracked Diff 且 Writer 仍 active 时，操作员发送身份绑定 stop。Worker
可靠停止，`termination_unconfirmed=false`，Workspace 只有以下两个允许文件：

```text
src/vega/agent_run_status.py
tests/test_agent_runtime.py
```

人工核对本机进程、命令类型、Workspace 和 Worker 自检临时目录后，将本次 operation 的
外部副作用裁决为 `none`，随后生成 Handoff Task Card。操作员只提交上述两个 WIP 文件与
Task Card；远端 Handoff 提交的父节点为固定控制提交。

### 20.2 为什么没有启动 machine B

machine B 会在 Worker 完成后进入现有 Core Reviewer。复核固定控制源码后确认：

1. Supervisor Writer 使用 `CodexExecRunner(single_writer=True)`，会执行新增的 MCP 隔离；
2. `LoopAutomationRuntime` 的默认 Reviewer 没有注入独立 runner；
3. Reviewer 因此仍由普通 `CodexExecRunner` 创建，不会执行 `single_writer` 分支中的 MCP
   探针；
4. read-only sandbox 只约束 Workspace 写入，不能证明外部 MCP Server 不会启动或产生外部
   副作用。

SAG3B-06 的硬条件要求 machine A/B 都不得启动继承 MCP。继续启动 machine B 必然使用已知
不满足该条件的固定控制器，因此本 Case 在 machine B 派发前 fail-closed：

```text
machine_a_mcp_isolation = pass
machine_a_handoff = pass
machine_b_fresh_clone = not_run
machine_b_worker = not_run
machine_b_reviewer = not_run
workspace_scope_verification_risk_review_finish = not_run
gate_3b = not_passed
false_success = 0
```

该结果不通过临时修改用户全局 Codex 配置、复制认证目录或放宽外部副作用标准绕过。machine A
Handoff 保留在远端作为本次实验记录，但不得被后续 Case 当作已完成 Gate。

### 20.3 Reviewer 隔离修复边界

下一次正式 Case 前必须把 MCP 隔离与 Writer 专属限制分开：

1. `CodexExecRunner` 增加独立的 MCP 隔离开关；
2. `single_writer` 仍隐含启用 MCP 隔离，并继续关闭网络、额外可写根目录和多 Agent；
3. Supervisor 默认 `LoopAutomationRuntime` 为 Reviewer 注入
   `CodexExecRunner(isolate_mcp=True)`；
4. Reviewer 仍使用 read-only sandbox，不继承 Writer 对话，也不取得 Writer 专属写权限；
5. 用户显式注入的测试或替代 Runtime 不被静默覆盖；
6. MCP 探针失败时 Reviewer 也必须在启动前 fail-closed。

该修复不改变普通 `vega do` 或现有 Loop 的 runner 选择，不增加通用工具策略引擎。

## 二十一、SAG3B-07 预注册

### 21.1 冻结任务

Case ID：`SAG3B-07`

目标分支继续使用 `codex/sag3b-04-status-visibility`。包含本节和 Supervisor Reviewer MCP
隔离修复的提交必须先推送并核对远端 HEAD；该提交随后作为 machine A/B 唯一固定控制源码。

用户目标：

> 在 Supervisor 接受真实机器 Observation 并发布 Decision、Checkpoint、State 与 Trace 前，
> 先验证当前 active child/operation 与最近一次 `worker_dispatch_committed` Trace 完全一致。
> 如 Trace 的 operation 被篡改、截断或与 State 冲突，必须在任何新的权威状态发布前
> fail-closed，不能等到最后写状态卡时才发现。

唯一 Work Item：

```yaml
id: W1
objective: 把 dispatch Trace 身份验证前移到 Supervisor reconcile 的发布边界
allowed_paths:
  - src/vega/agent_run_status.py
  - src/vega/agent_runtime.py
  - tests/test_agent_runtime.py
  - tests/test_agent_codex_adapter.py
forbidden_paths:
  - src/vega/agent_contract.py
  - src/vega/agent_persistence.py
  - src/vega/agent_runtime_logic.py
  - src/vega/agent_worker.py
  - src/vega/runner.py
  - src/vega/codex_mcp_isolation.py
  - .vega/**
  - docs/**
  - eval/**
verification:
  - python -m pytest -q -p no:cacheprovider -o cache_dir={{vega_verification_temp}}/cache --basetemp={{vega_verification_temp}}/runs tests/test_agent_runtime.py::test_agent_status_rejects_active_operation_trace_mismatch tests/test_agent_runtime.py::test_agent_status_rejects_observation_operation_trace_mismatch tests/test_agent_runtime.py::test_reconcile_rejects_trace_operation_mismatch_before_artifact_publication tests/test_agent_codex_adapter.py::test_agent_success_path_preserves_completed_worker_in_status_card
  - ruff check --no-cache src/vega/agent_run_status.py src/vega/agent_runtime.py tests/test_agent_runtime.py tests/test_agent_codex_adapter.py
  - git diff --check
```

成功条件：

1. `observe_machine()` 在写入新的 Observation、Decision、LangGraph route、Checkpoint、Plan、
   State、Trace 或状态卡之前，验证 active child/operation 与最近 dispatch Trace；
2. Trace operation 不一致时，原 `agent-plan.json`、`agent-state.json`、`trace.jsonl`、
   `status-card.md` 和已有 Checkpoint 字节保持不变；
3. 不产生新的 Observation、Decision 或 Checkpoint；
4. 正常绑定、同一 child 的合法 repair 新 operation、历史 child 展示和现有成功路径保持；
5. 不新增 State 字段、第二套身份数据库、自动修复 Trace 或新的成功语义；
6. machine B 重新形成 Workspace、Scope、Verification、Risk、Reviewer 与 Finish Artifact。

### 21.2 固定执行协议

```text
adapter = codex-exec
work_item_count = 1
machine_a_attempts = 1
machine_b_attempts = 1
automatic_retries = 0
manual_repairs = 0
replans = 0
worker_timeout_seconds = 900
reviewer_timeout_seconds = 900
```

machine A/B 继续使用两个无共享 `.git`、`runs/`、`.tmp/` 或聊天的 fresh clone。双方固定控制
源码必须相同，并在 Worker/Reviewer 启动前分别确认：

```text
writer_mcp_isolation_preflight = pass
reviewer_mcp_isolation_configured = 1
```

machine A 在首次出现允许路径 tracked Diff 且 Writer 仍 active 时停止并生成新 Handoff。
由于分支保留 SAG3B-06 Task Card，machine B 必须显式选择本 Case 的 Task Card：

```text
vega agent resume --repo . --task .vega/tasks/2026-08/<sag3b-07-task-card>
```

不得让自动发现歧义替代显式任务选择，也不得删除或改写 SAG3B-06 历史 Task Card。

### 21.3 通过标准

```text
git_only_isolated_handoff = 1
fresh_clone_count = 2
control_source_commit_match = 1
task_card_only_resume = 1
duplicate_writer_start = 0
writer_inherited_mcp_process_started = 0
reviewer_inherited_mcp_process_started = 0
external_side_effects_machine_a = none
external_side_effects_machine_b = none
workspace_scope_verification_risk_review_finish = pass
automatic_git_write = 0
false_success = 0
```

SAG3B-07 未通过前，Gate 3B 继续保持未通过，不能发布 `v0.2.0`。

## 二十二、SAG3B-07 实际结果

### 22.1 machine A Handoff

SAG3B-07 machine A 使用独立 fresh clone，并从同一提交导出固定控制源码：

```text
control_source_commit = e35cffcb3c0bc3669a5be401cfb8c84beaaa2487
control_source_tree = e19fe0e23ef61e3792585936c6fdb42d4aa1614f
control_source_archive_sha256 = c288dd576d86dfbc7418145aa7aa6af2c49aa8d98fcf2c9c65facfff86a8a860
plan_sha256 = 53035d1a70ee91e0497d911d51a72faaaca533334bc6797d8b4fcc0eed38883f
machine_a_agent_run = 20260817-140316-agent
machine_a_child_run = 20260817-140425-408345-bug-loop
machine_a_operation = 4803e1062038442e8e1ffb132ab9ff04
handoff_commit = 976fc359de306153837c8d809b05ed6bdd8513e5
task_card_sha256 = 5c0cdfd4f3096346dfbbc5aa6ebc9e6aae35797bce714200ae8168d019f49389
```

Writer 启动前 MCP 隔离复查通过，owned command 包含 5 个逐项禁用覆盖。进程树只出现
Codex 自身启动链与短生命周期 PowerShell；JSONL 没有 MCP、Web、浏览器或外部基础设施
工具事件。首次出现允许路径 tracked Diff 后，操作员按绑定身份停止 Writer；最终只有：

```text
src/vega/agent_runtime.py
tests/test_agent_runtime.py
```

人工核对 owned 进程、命令类型和 Workspace 后，将 machine A 外部副作用裁决为 `none`。
随后只提交两个 WIP 文件和新的 Task Card。远端 Handoff 提交的父节点为固定控制提交，
Task Card 中旧 Verification、Risk 与 Reviewer 仍明确标记为 historical `not_run`。

### 22.2 machine B 恢复与超时

machine B 从远端 Handoff 提交建立第二个独立 fresh clone，没有共享 `.git`、`runs/`、
`.tmp/` 或聊天。它重新导出固定控制源码并得到与 machine A 相同的 archive SHA-256，
随后显式选择 SAG3B-07 Task Card 执行恢复：

```text
machine_b_head = 976fc359de306153837c8d809b05ed6bdd8513e5
machine_b_agent_run = 20260817-141631-agent-resume
machine_b_child_run = 20260817-141708-799856-bug-loop
machine_b_operation = bb646171185747e685e4f25fda8ea761
writer_timeout_seconds = 900
```

恢复成功重建 Goal、批准 Plan、当前 Work Item、Handoff 基线和 Workspace 约束；新 run 没有
复用 machine A 的 State、Trace、SQLite 或运行目录。Writer 启动命令包含 5 个 MCP 禁用
覆盖，进程树只出现 Codex、命令执行器、PowerShell 和 Python。109 条 Codex JSONL 事件中
只有 reasoning、todo 与 command，没有 MCP、Web、浏览器、网络或 Git 写入事件，也没有
最终 `agent_message` 或 `turn.completed`。

Worker 没有修改 Handoff 提交以外的新文件。它在当前 Python 3.14.3 / pytest 9.0.2 环境中
反复调查测试进程不退出的问题，多次 pytest 自检被有界命令超时终止，最终超过冻结的
900 秒 Worker 预算。Vega 将 execution 记为：

```text
worker_status = timed_out
returncode = 1
termination_unconfirmed = false
machine_b_workspace_new_drift = 0
verification = not_run
risk = not_run
reviewer = not_run
finish = not_run
supervisor_action = human
external_side_effects = unknown
```

Supervisor 发布 machine Observation 后确定性进入 `needs_human`，没有启动第二 Writer、
自动重试、repair、commit、push、release 或长期 Memory 写入。Worker、命令子进程与控制
进程均已退出。因为没有获得可信 Worker 终态，也没有形成新的 Verification、Risk、Reviewer
和 Finish Artifact，SAG3B-07 判定为：

```text
machine_a_handoff = pass
machine_b_git_only_resume = pass
machine_b_worker = timeout
workspace_scope_verification_risk_review_finish = not_run
gate_3b = not_passed
false_success = 0
```

该 Case 不重跑，也不通过延长预算或事后补造成功 Artifact 改写。单独诊断中，同一 4 个
定向测试节点在另一个已存在的隔离开发环境中得到 `4 passed`；这只支持当前 WIP 的代码
正确性审查，不属于冻结 machine B 的 Gate 证据，也没有证明 pytest 进程不退出的唯一根因。
下一正式 Case 必须在预注册前先冻结可终止、可复现的项目测试环境；不能把环境修正偷换成
SAG3B-07 通过。

## 二十三、2026-08-17 SAG3B-08 稳定执行环境预注册

### 23.1 Case 目的与停止线

SAG3B-08 只验证一个剩余问题：

> 在两个不共享 `.git`、`runs/`、`.tmp/`、虚拟环境或聊天的 fresh clone 中，使用已经证明
> 可终止的 Python 3.12 环境，能否完成真实 Worker partial Diff、Git Task Card Handoff、
> machine B 恢复，以及新的 Workspace、Scope、Verification、Risk、Reviewer 与 Finish。

本 Case 不新增 Runtime 机制。PR `#68` 已以 `main@70282d1` 合入 Windows batch launcher
兼容和人工 replan attempt epoch；这些能力只能作为当前产品前置事实，SAG3B-08 的正式证据
仍固定为零自动重试、零 repair、零 replan。

SAG3B-08 是当前 Gate 3B 的唯一下一 Case。若它仍因环境或新的 Harness 边界未通过，保留
真实结果并停止自动追加 SAG3B-09；后续是否继续 Gate 3B 必须另行决策。

### 23.2 冻结任务

Case ID：`SAG3B-08`

控制基线：`main@70282d1`

协议与目标分支：`codex/sag3b-08-stable-env`

用户目标：

> 修正首次真实 Worker 拒绝脏 Workspace 时的过期错误提示。拒绝逻辑保持不变，但提示必须
> 说明 staged、unstaged 或 untracked 变更需要先处理；已经完成 comparison baseline 对账的
> committed Task Card handoff 走独立恢复路径，不能继续声称跨机器接力属于“后续 Gate”。

唯一 Work Item：

```yaml
id: W1
objective: 让首次 Worker 的脏 Workspace 错误提示符合当前 committed handoff 合同
allowed_paths:
  - src/vega/agent_codex_preparation.py
  - tests/test_agent_codex_adapter.py
forbidden_paths:
  - src/vega/agent_codex_adapter.py
  - src/vega/agent_contract.py
  - src/vega/agent_persistence.py
  - src/vega/agent_recovery.py
  - src/vega/agent_runtime.py
  - src/vega/loop_runtime.py
  - docs/**
  - eval/**
verification:
  - python -m pytest -q -p no:cacheprovider -o cache_dir={{vega_verification_temp}}/cache --basetemp={{vega_verification_temp}}/runs tests/test_agent_codex_adapter.py::test_adapter_rejects_dirty_initial_workspace_before_creating_child
  - ruff check --no-cache src/vega/agent_codex_preparation.py tests/test_agent_codex_adapter.py
  - git diff --check
```

成功条件：

1. 首次 plan-approved Worker 遇到 staged、unstaged 或 untracked 变更时仍 fail-closed；
2. 错误文本不再包含“跨机器接力和累计归因属于后续 Gate”；
3. 新文本明确区分未对账的工作区变更与已验证的 committed Task Card handoff；
4. Task Card resume、comparison baseline、attempt 计数和成功语义均不改变；
5. 只修改两个允许文件，不新增 Schema、状态字段或恢复分支。

### 23.3 冻结执行环境

两端均使用本机现有 `Python 3.12.10` 和 `codex-cli 0.147.0`，但不得共享虚拟环境：

```text
python = 3.12.10
pytest = 8.4.2
pytest-timeout = 2.4.0
ruff = 0.15.20
worker_timeout_seconds = 900
reviewer_timeout_seconds = 900
automatic_retries = 0
manual_repairs = 0
replans = 0
```

所有现场只能位于仓库内被忽略的 `.tmp/dogfood/sag3b-08/`：

```text
.tmp/dogfood/sag3b-08/
  machine-a-target/
  machine-b-target/
  control-a/
  control-b/
  envs/
  protocol/
  evidence/
```

每端分别从包含本节的已推送提交导出固定控制源码，并建立独立 control venv 与 target venv。
控制命令使用 control venv 的 Python；其子进程 `PATH` 只优先指向对应 target venv，确保
Worker 自检和 Core Verification 使用目标 clone 的依赖环境，而不是用户全局 Python。

正式模型派发前，两端必须各自连续三次执行冻结 pytest 节点，每次在 60 秒内明确退出，并确认：

- exit code 为 0；
- 没有残留 pytest、目标 venv Python 或 Vega owned process；
- target Workspace 字节、HEAD、index、tracked、untracked 和 ignored 清单没有变化；
- `python -VV`、pytest、pytest-timeout 和 Ruff 版本与本节一致。

任一预检失败，Case 记为 `invalid-harness`，不得启动真实 Worker。

### 23.4 machine A/B 固定协议

machine A：

1. 从远端协议分支建立 fresh clone；
2. 用固定控制器创建并批准单 Work Item Plan；
3. 后台启动一次真实 `agent run`；
4. 首次出现允许路径 tracked Diff 且 active child/operation 仍有效时，立即发送身份绑定 stop；
5. 等待原控制进程退出，再执行静止 Workspace stop、外部副作用人工裁决和 Handoff；
6. 人工只提交两个允许文件与本次新 Task Card，并推送同一目标分支。

machine B：

1. 从远端 Handoff HEAD 建立第二个 fresh clone，不复制 machine A 本地 Artifact；
2. 显式选择 SAG3B-08 Task Card 执行 `agent resume`；
3. 使用独立固定控制器和 target venv 启动一次真实 Worker；
4. 重新运行 Workspace、Scope、Verification、Risk、Reviewer 与 Finish；
5. 不复用 machine A 的 Gate 结果，不自动 commit、push、repair、replan 或重试。

### 23.5 通过标准

```text
stable_environment_preflight = pass
fresh_clone_count = 2
git_only_isolated_handoff = 1
control_source_commit_match = 1
task_card_only_resume = 1
duplicate_writer_start = 0
worker_ignored_workspace_change = 0
workspace_scope_verification_risk_review_finish = pass
automatic_git_write = 0
false_success = 0
```

全部条件满足时，Gate 3B 判定为 `gate-exit-pass`，下一步才是 Gate 3C 的少量日常价值观察。
否则如实记录唯一阻断点，Gate 3B 保持未通过，默认不继续扩建基础设施。

### 23.6 machine A 实际结果与停止判定

SAG3B-08 使用预注册提交
`a816be2385766003c4351fd4a7674f24fbb5c523`，两份独立控制 archive 的 SHA-256 均为
`b0712e70df565c9b727fe76035fa0f8a64e68a71ece093e7bf59cfc2faed6f81`。machine A 在
Python `3.12.10`、pytest `8.4.2`、pytest-timeout `2.4.0` 和 Ruff `0.15.20` 环境中连续
三次通过冻结预检，耗时分别为 `17.726s`、`2.895s` 和 `2.848s`；每次都没有 Workspace
漂移或残留目标进程。

正式运行身份为：

```text
agent_run = 20260817-235358-agent
child_run = 20260817-235421-789385-bug-loop
operation = c7e74fd678f9410f8378ae881bc90cf6
```

真实 Worker 只修改：

```text
src/vega/agent_codex_preparation.py
tests/test_agent_codex_adapter.py
```

修改保持原有脏 Workspace 拒绝判断，只更新用户可见错误文本，并补充精确断言。Ruff 和
`git diff --check` 自检通过。Worker 的定向 pytest 首次运行继承了控制端
`VEGA_GIT_SAFE_DIRECTORY`，与测试创建的 fixture 仓库不一致，因此在进入目标断言前失败。
Worker 随后启动移除该环境变量后的重跑，但身份绑定 stop 在该命令完成前生效。

停止后 execution 为 `stopped`，`termination_unconfirmed=false`；Agent State 无 active
child 或 operation，目标 HEAD 未改变，Workspace 只有两个允许文件。第二次静止 stop 生成
`checkpoint-003 / needs_human / blocked / operation_started=false`。

人工核对同时确认：Worker 把 pytest `--basetemp` 指向系统 `%TEMP%`，并遗留一个
`vega-worker-sag3b08-*` 目录，其中包含 pytest fixture、临时 Git 仓库和测试 Workspace。
这违反了 Worker Prompt 的“自检不得额外留下文件”约束，也不符合本 Case “所有现场位于
`.tmp/dogfood/sag3b-08/`”的冻结边界。该事实不能裁决为 `none`；人工裁决追加：

```text
checkpoint = checkpoint-004
phase = needs_human
status = blocked
external_side_effects = known
handoff_status = none
```

因此没有执行 `agent checkpoint --handoff`，没有生成 SAG3B-08 Task Card、Handoff 提交或
machine B clone，也没有自动重试、repair、replan、commit、push 或长期 Memory 写入。

最终判定：

```text
stable_environment_preflight = pass
machine_a_partial_diff = pass
worker_ignored_workspace_change = fail
external_side_effects_machine_a = known
git_only_isolated_handoff = 0
machine_b_started = 0
workspace_scope_verification_risk_review_finish = not_run
gate_3b = gate-not-passed
```

该结果证明稳定 Python 环境消除了 SAG3B-07 的预检超时，但没有证明跨机器完整恢复。它同时
暴露一个更直接的产品边界：Prompt 对 Worker 自检临时文件位置的约束不是确定性执行隔离。
按照第 23.1 节停止线，不自动追加 SAG3B-09，也不为本次结果扩大 Runtime；后续是否继续
Gate 3B 必须重新决策。
