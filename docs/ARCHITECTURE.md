# 架构说明

## 总览

Vega v0.1 是一个本地文件系统优先的 AI Coding Harness。它不追求复杂编排，而是通过两条
顶层执行路径覆盖只读检查和日常编码闭环。

```text
vega run engineering-change
  -> Task Intake
  -> LoopSpec(YAML)
  -> EngineeringChangeRuntime
  -> Context Loader
  -> Tool Broker
  -> Report
  -> 内置线性 Reviewer
  -> Eval
  -> State / Trace / Replay
```

```text
vega do / vega loop
  -> Brief / Project Context
  -> LoopAutomationRuntime
  -> Worker 前工作区基线
  -> Worker
  -> Workspace / Scope Gate
  -> Verification
  -> Reflect / Risk Gate
  -> 独立 ReviewRuntime
  -> Recover / Finish
```

`vega do/loop` 是当前日常 Coding Harness 主线。`vega run engineering-change` 保留为
可安装的只读 Inspection Loop baseline：其解析顺序为 workspace 的
`loops/engineering-change.loop.yaml` 优先，包内只读 baseline 回退；因此源码仓可显式覆盖，
wheel 安装后也能在任意 workspace 使用该检查入口。

两条路径共享本地 state、trace 和 fail-closed 原则，但入口、配置源、artifact 与 reviewer
语义不同。`BriefRuntime`、`ReviewPackRuntime` 和 `ReviewRuntime` 是阶段组件或可单独调用阶段，
不是额外的长期 Agent。

长任务 P0 已提供人工驱动状态层：`goal start/status/step/attach/checkpoint-done/pause/resume/stop/recover`。实验性 P1 另外提供 `goal run --max-checkpoints 1`，只调度一个普通 auto loop，并在 checkpoint 证据边界停止。它不自动串联下一 checkpoint，不自动 commit/push，也不声称能够跨小时或跨天自治完成目标。详见 `docs/LONG-RUNNING-GOALS.md`。

## 核心流程

```text
task.md
  -> 读取 loop YAML
  -> 创建 run_id
  -> 初始化 state.json
  -> 解析目标文件
  -> 生成 plan.md
  -> 按 YAML 配置执行只读工具
  -> 生成 report.md
  -> 生成 review.md
  -> 运行 eval 并写 eval.md
  -> 更新 state.json
  -> 写 trace.jsonl
```

## LoopSpec 边界

YAML 负责配置策略，不负责执行任意代码。

当前允许 YAML 配置：

- 允许和禁用的工具名。
- 目标文件区块名。
- 搜索关键词。
- allowlisted git check。
- 报告必需章节。
- reviewer 检查项和禁用动作表述。
- eval artifact 和 trace event 检查。

YAML 不允许配置：

- 任意 shell 命令。
- 任意 Python hook。
- 自动 patch。
- 自动 commit。
- 自动 release。
- 自动长期 memory 写入。
- 多 Agent 编排。


## 产品能力分层

核心主线是上下文编译、受控 worker、确定性验证、隔离 reviewer 和证据化恢复。`brief`、
`reflect`、`gate`、`review-pack`、`review` 属于可单独观察的流水线阶段；Memory、Goal P0
和 adapter 属于实验能力。完整契约见 `docs/PRODUCT-CONTRACT.md`。

实验能力不得成为核心 run 的必需 artifact 或成功条件，也不作为 v0.1 baseline 冻结或
发布准备的阻断条件。

## Project Knowledge Layer

v0.1.x 已落地项目知识层，用来把项目长期规则和可选历史经验编译成一次运行可用的上下文。

输入来源：

- 目标仓库 `AGENTS.md`。
- 相关子目录 `AGENTS.md`。
- Vega `memory/ledger.jsonl` 中已接受的 memory。
- 当前 bug 或 feature 描述。

输出产物：

```text
knowledge-context.md
agent-brief.md
agents-md-proposals.md
```

`agent-brief.md` 是给真实编码 AI 使用的主要上下文；`agents-md-proposals.md` 只提出项目规则改进建议，不自动修改目标仓库。

## Bug / Feature Brief Runtime

Brief Runtime 是 `LoopAutomationRuntime` 使用的轻量阶段能力，也可以独立调用。它面向开发者
最常见的两类工作：修 bug 和拆需求。

```text
vega brief bug --repo <repo> (--input <file> | --text <text>)
vega brief feature --repo <repo> (--input <file> | --text <text>)
```

Bug 模式额外生成复现计划、根因假设和回归检查。Feature 模式额外生成功能规格、实现计划、验收标准和风险清单。


## Project Profile

Project Profile 是项目画像命令，负责在开发前识别仓库基本结构：

- 技术栈。
- 包管理器或构建工具。
- 推荐测试命令。
- 推荐静态检查命令。
- 入口文件。
- 关键目录。
- 配置文件。
- AGENTS.md 文件。
- 已接受 memory 命中数。

```text
vega profile --repo <repo>
```

它生成 `project-profile.json` 和 `project-profile.md`，供 brief、人工判断和后续复盘参考。brief、reflect、review 和 loop 还会生成 `project-context.md`，把项目画像、推荐验证命令、AGENTS.md 和 accepted memory 合成稳定上下文，但不包含 worker 聊天记录。

## Project Config

`.vega.yaml` 是给 runtime 读取的项目级机器策略，和面向 AI 的 `AGENTS.md` 分工不同：

- `AGENTS.md`：自然语言规则、编码规范、踩坑说明。
- `.vega.yaml`：验证命令、超时、精确路径范围、风险路径、变更预算、默认 runner 等可执行策略。

示例：

```yaml
version: 1
verification:
  commands:
    - python -m ruff check .
    - python -m pytest -q
  max_commands: 2
  timeout_seconds: 180
risk:
  high_paths:
    - src/auth/
  require_human_review:
    - 删除文件
  required_reviews:
    - id: payment
      label: 支付与资金
      paths:
        - src/payments/**
    - id: database
      label: 数据库与迁移
      paths:
        - db/migrations/**
    - id: concurrency
      label: 并发与异步
      paths:
        - src/jobs/**
budget:
  max_changed_files: 5
  max_diff_lines: 300
  max_new_files: 3
  forbid_new_dependencies: true
  forbid_large_generated_files: true
scope:
  allowed_paths:
    - src/auth/**/*.py
  forbidden_paths:
    - tests/**
    - .vega.yaml
budget_profiles:
  refactor:
    max_changed_files: 25
    max_diff_lines: 2000
    max_new_files: 10
prompt_budget:
  worker_max_chars: 40000
  reviewer_max_chars: 60000
  reviewer_diff_max_chars: 30000
runner:
  worker: codex-exec
  reviewer: codex-exec
  codex_exec:
    worker:
      reasoning_effort: medium
      ephemeral: true
    reviewer:
      reasoning_effort: high
      ephemeral: true
```

如果配置不存在，Vega 使用 project profile 自动识别。配置存在时，verification、精确路径范围、change budget、workspace check 和 risk gate 优先使用显式策略。`scope.allowed_paths` 非空时，所有 staged 与 unstaged tracked diff 都必须命中 allowlist；`forbidden_paths` 优先于 allowlist。`vega config check --repo <repo>` 是只读预检：它会拒绝路径逃逸、绝对路径和歧义 Windows 路径，不执行命令，用来在 auto loop 前提前发现不安全配置。

`risk.required_reviews` 使用仓库相对 POSIX glob。它不是禁止修改列表，而是高风险披露义务：
Gate 命中后仍会启动只读 Reviewer，要求每个风险 ID 覆盖全部命中文件并说明判断、证据与剩余
风险；持久化 verdict 固定为 `needs_human`。普通预算超限、删除文件或未命名的
`human-review` 仍保持原有早停行为。

loop 创建前会前后复核 HEAD 与策略文件 bytes 摘要，并把解析后的 scope 摘要写入根状态。
每次 scope gate 使用 `git status --porcelain=v2 --branch -z` 的稳定快照区分 index 与工作区
事实，同时读取 `git ls-files -v`；存在 `assume-unchanged` 或 `skip-worktree` 时直接拒绝，
避免 Git 视图把真实修改隐藏在 allowlist 之外。

Codex runner 采用“角色策略 + 固定安全边界”：

- worker/reviewer 可以分别选择 `profile`、`model`、`reasoning_effort` 和 `ephemeral`。
- 空的 `profile/model/reasoning_effort` 继承用户 Codex 配置，避免 Vega 复制一套全局模型配置。
- runtime 只把白名单字段编译成 `codex exec` 参数，不接受 YAML 传入任意 args。
- worker 的 sandbox 固定为 `workspace-write`，reviewer 固定为 `read-only`，不开放 bypass 参数。
- `execution.json` 记录最终命令；`project-context.md` 记录有效角色策略。

这样可以让高频 worker 使用中等推理成本，让 reviewer 保持更高审查强度，同时不把 Vega
扩张成新的模型配置中心。`ephemeral: true` 只控制 Codex session 是否持久化，不代表 run
证据会消失；Vega 自己的 prompt、输出、diff、verdict、trace 和 execution 记录仍保留在
项目 `runs/`。

Prompt 预算采用“先度量、再门禁、不静默丢证据”的策略：

- worker/reviewer 在调用外部 runner 前记录精确字符数、UTF-8 字节数、行数和分段规模。
- `worker_max_chars` / `reviewer_max_chars` 约束最终实际 prompt，而不是单个来源文件。
- reviewer 的 full diff 先按 `reviewer_diff_max_chars` 显式截断并留下截断标记。
- 任一 review evidence section 被截断后，即使模型输出 approve，也会被 runtime 改为 `needs_human`。
- 最终 prompt 仍超预算时进入 `needs_human`，不启动外部 runner。
- 不通过静默删除需求、AGENTS.md、验证失败或关键 diff 来“强行塞进上下文”。
- auto loop 把启动时的 reviewer runner/prompt 策略传到 review 阶段，worker 中途修改
  `.vega.yaml` 不能关闭本轮 reviewer 或临时缩小其预算。

review pack 不再分别重复注入 Project Profile 和 Project Knowledge。两者连同 runtime 策略统一由
`project-context.md` 提供；review pack 只保留需求、复盘、diff 摘要、测试摘要、项目上下文和
full diff 六类事实材料。

大目标不应该直接让 auto worker 一口气执行。`vega plan --repo <repo> --input <goal.md> --scope refactor` 会先生成 `change-plan.md`、`scope-profile.md` 和 `phase-plan.md`，供人工确认 scope 后再拆 phase 执行。`vega gate --scope <profile>` 会使用对应 budget profile，表示“这是经过声明的大范围变更”，而不是悄悄放宽默认小任务预算。

## Reflect / Post-run

Reflect 是执行后复盘命令，用于在 AI 或人工完成修改后分析当前工作区 diff，并生成可沉淀的复盘材料。

```text
vega reflect --repo <repo> [--run <run_id>] [--test-log <file>] [--note <text>] [--lesson <text>]
```

它读取：

- staged 与 unstaged 的完整 diff、文件列表和工作区指纹
- staged：`git diff --cached HEAD --check`
- unstaged：`git diff --check`
- 可选测试日志
- 可选上游 run 的 `agent-brief.md`
- 相关 AGENTS.md 与 memory

输出：

```text
diff-summary.md
full-diff.patch
test-summary.md
review-evidence.json
project-context.md
reflection.md
agents-md-proposals.md
eval.md
```

Reflect 不修改目标仓库，也不自动接受 memory。普通复盘只输出事实与规则建议；只有用户显式
提供 `--lesson` 时，才额外生成 `memory-proposals.jsonl`。它会把 staged（index 对 HEAD）和
unstaged（工作区对 index）作为两条事实流分别采集；`full-diff.patch` 用明确标签保留两段，
而不是用 `git diff HEAD` 的净差异覆盖其中一段。这样同一文件处于 `MM` 时，index 中尚未审查的
内容不会被工作区反向修改抵消。Reflect 的确定性 eval 失败时，状态为 `failed`；auto loop 不会
继续启动 reviewer，独立 `review` 也会将其标为 `source_reflect_not_success` 并停止外部 reviewer。

## Isolated Review Pack

Review Pack 是执行后复盘和隔离审查之间的上下文编译层：

```text
vega gate --repo <repo> --run <reflect_run_id>
vega review-pack --repo <repo> --run <reflect_run_id>
vega review --repo <repo> --run <reflect_run_id> --runner codex-exec
```

Risk Gate 会读取 reflect run、当前 diff、测试摘要、变更路径、`.vega.yaml` 风险策略和已接受 memory，
输出 `gate-result.json` 与 `gate-report.md`。独立 `vega gate` 只给出 `self-check`、
`isolated-review` 或 `human-review` 建议；auto loop 会在当前 iteration 写入同等证据。门禁异常
或没有命名披露义务的 `human-review` 会在 Reviewer 前停止；命中
`risk.required_reviews` 时只读 Reviewer 继续生成逐类披露，但终态固定交由人工。
loop state 会记录该 iteration 的 source reflect、风险结论、建议和两份门禁产物哈希。
`risk-gate-report.md` 还会携带机器可校验的结构化绑定：status、iteration、source reflect、
result hash、risk 和 recommendation。loop eval 与 Finish 会同时复核结果、报告和 state；
风险门禁产物缺失、损坏、语义绑定不一致，或未命名的 `human-review` 被 Reviewer 绕过时，
都不能进入 `success/ready_to_commit`。命名高风险只有在逐类披露完整且 verdict 为
`needs_human` 时才能形成有效的人工接管证据。复核时还会从绑定的 Reflect 和当前可信工作区快照重算
风险语义，并核对 loop trace、连续 iteration 编号和 state.current_iteration，避免只同步改写
state、报告和哈希就把高风险结论降级。

独立 `vega review` 同样会在启动 reviewer 前固化对应风险门禁。若结果为
`human-review`，仍可保留 reviewer 的辅助发现；命中 `required_reviews` 时还会校验每个风险
ID、全部命中文件、代码证据与剩余风险。review run 必须停在 `needs_human`；
Goal 不会把它当成可完成 checkpoint 的自动证据。

风险门禁完成后，ReviewRuntime 会再次捕获授权快照并复查项目策略、工作区指纹和 index
标记；与最初 review pack 快照不一致时不调用外部 reviewer。该设计关闭的是目标仓库在
单用户工作流中的可观测阶段漂移，不为目标仓库提供操作系统级事务锁。loop run 自身的
生命周期 artifact 另由下述 per-run mutation lock 保护。

这些检查提供的是本地证据链的一致性与重算保证，不是抵抗拥有完整本地文件系统写权限者的签名
系统。若攻击者同时修改源码、Git 状态、所有 run artifacts 与 trace，应使用外部不可变存储或
签名 provenance；这不属于当前单用户轻量 runtime 的范围。

Review Pack 读取 reflect run、上游 `agent-brief.md`、当前 diff、测试日志、AGENTS.md、accepted memory、project profile 和 `project-context.md`，生成：

```text
review-pack.md
review-prompt.md
review-checklist.md
project-context.md
review-context.json
review-prompt-metrics.json
review-prompt-metrics.md
eval.md
```

`vega review` 会在此基础上调用短生命周期 reviewer。当前主要 runner 是 `codex exec`，reviewer 使用 `read-only` sandbox，只输出结构化 `review-verdict.json` 和 `review-findings.md`。这一步的目标不是复刻 Codex `/review`，而是把 `/review` 类能力放进可追踪、可复盘、可迭代的研发闭环。

## Assist / Auto Loop

自动化 loop 是一个轻量 orchestrator，不是多 Agent 平台：

```text
brief -> project-context -> workspace baseline -> worker prompt -> worker -> workspace-check
-> verification -> reflect -> risk gate -> review-pack -> isolated reviewer -> verdict
```

两种模式：

- `assist`：主会话或宿主工具的原生子代理负责实现。Vega 在生成 worker prompt 前先把
  `workspace-baseline.json` 与根状态、trace 进行内容哈希绑定，后续通过 `loop continue`
  对照真实工作区收集 diff、自动验证日志并触发隔离 reviewer。
- `auto`：通过 `loop --mode auto` 或命令级自动入口 `do` 显式选择，用 `codex exec` 作为
  worker。首次启动必须没有 staged 或 unstaged tracked diff；否则不启动 worker，避免把历史
  改动归因给本轮。后续自动迭代保留同一 run 前一轮的 diff 作为基线。worker 后先做工作区污染
  检查，再执行 verification 前的 iteration-local scope gate。scope 越界会 fail-closed；通过后才自动
  执行 `.vega.yaml` 或 project profile 识别出的最多两个验证命令。验证结束后会再次执行 scope gate，
  防止验证脚本写出越界 tracked diff；Reflect 固化 review 输入后还会进行一次 scope recheck。三次
  检查与 reviewer 的工作区快照校验共同防止异步进程把越界 diff 带入隔离审查，默认最多 2 轮。

assist 基线要求捕获完整、没有 staged 或 unstaged tracked diff，且 HEAD 与初始化时一致。
任一条件不满足时，run 会保留 `workspace-check.md/json`、`final-report.md`、state 和 trace，
但不会生成 `worker-prompt.md`，也不会创建 iteration 或把任务交给 Worker。清理或稳定仓库后
必须新建 loop；这类失败 run 不能强行 continue。

`loop continue` 会在创建 iteration 前重新验证 baseline artifact 的 schema、内容哈希和根状态
绑定，再用该基线检查当前 HEAD、启动前未跟踪文件、ignored 清单和 Git 控制状态。缺失或被
改写的基线会 fail-closed。没有 baseline 的旧 assist run 仍可查看，但不能继续自动归因。

当 Vega 的 `runs/` 根目录位于目标仓库中时，runtime 会把这个由 Harness 自己持续写入的 ignored
目录记录为 baseline exclusion，并把 exclusion 一并写入哈希绑定的 artifact。该例外只避免
Vega 把自己的运行产物误判为 Worker 污染，不会放宽对目标仓库其他 ignored 文件的检查。

验证门禁优先于 LLM 结论：如果自动验证失败，即使隔离 reviewer 返回 `approve`，loop 也会进入 `needs_human` 并生成 `fix-prompt.md`，不会进入 `success/ready_to_commit`。

Loop 内的 Risk Gate 结果由 Runtime 在启动 reviewer 前确定性生成，并直接传给同一同步调用链中的
ReviewRuntime，避免相邻阶段重复执行整套 Git 快照。独立执行 `vega review` 时仍会自行计算
Risk Gate；无论哪条路径，reviewer 启动前都会重新捕获授权快照，终态 Eval/Finish 也会独立重算
风险语义。因此这里复用的是 Runtime 自己刚生成的结果，不是信任 worker 或 LLM 提供的结论。

工作区污染门禁早于验证和 review：auto worker 只要新增未跟踪文件，即使数量没有超过
`budget.max_new_files`，Vega 也会写入 `workspace-check.md/json` 并停止在 `needs_human`，
因为隔离 reviewer 不读取这些文件的内容。Vega 不会自动删除文件；数量预算仍用于展示变更规模，
不能作为放行未跟踪内容的授权。

`max_iterations` 只限制 auto 模式中的自动 worker 重试次数，不限制用户在 `request_changes` 或 `needs_human` 后进行人工修复并继续运行 `loop continue`。这是为了保留“自动化有边界、人工可以接管”的设计。

`loop continue` 只允许继续同一仓库中处于 `needs_human` 的 run。Reflect 会固化
`review-evidence.json`、`full-diff.patch` 和工作区指纹；指纹覆盖 HEAD、staged/unstaged
tracked diff、untracked 文件内容清单和 index 标记摘要，其中 staged/unstaged 各有独立哈希。
Review 会校验 artifact 哈希、当前工作区指纹和 reviewer 授权快照，不一致时不启动外部 reviewer，并进入
`needs_human/evidence_stale`。

每次 loop 写入：

```text
runs/<run_id>-loop/
  state.json
  trace.jsonl
  agent-brief.md
  project-context.md
  project-policy-snapshot.json
  loop-plan.md
  workspace-baseline.json       # assist 模式
  worker-prompt.md
  iterations/
    01/executions/worker/execution.json
    01/executions/reviewer/execution.json
    01/workspace-check.md
    01/verification-summary.md
    01/risk-gate-result.json
    01/risk-gate-report.md
  final-report.md
  eval.md
```

Vega 只负责编译上下文、调用 runner、记录证据和控制迭代；不自动 commit、push、
release，也不要求每次运行生成长期经验候选。

因此 Vega 是控制面，Codex、Claude Code 或人工主会话是执行面。宿主可以选择直接实现或调用
原生子代理，但 Vega 不接管宿主内部的多 Worker 调度，也不依赖 Worker 自述判断完成；是否进入
审查和成功状态只由工作区事实、确定性验证与隔离 Reviewer 证据决定。

### Execution Control

worker、reviewer 和每条 verification 命令共用一个窄的 owned process control：

```text
RunnerExecutionContext
  -> execution.json
  -> Popen + heartbeat polling
  -> stop-request.json / deadline
  -> graceful terminate
  -> stopped | timed_out | completed | failed
```

`execution.json` 记录 run、step、iteration、owner PID、child PID、command、heartbeat、lease、
deadline 和终态。普通外部命令默认把 stdout/stderr 合并写入匿名临时文件；Codex
worker/reviewer 使用 `codex exec --json` 时，stdout 与 stderr 分离：专用 daemon 线程以
64 KiB 分块持续排空 stdout PIPE，同时把不超过
256 KiB 的完整 JSONL 行非阻塞地放入有界实时提示队列；队列满或观察器异常时只丢弃实时提示，
不会阻塞 reader，也不会影响完整输出。独立 daemon dispatcher 异步调用观察器，慢或卡死的
观察器不会拖延 timeout、stop 与 heartbeat；dispatcher 关闭只等待有限窗口，卡死回调作为
daemon 残余风险保留。观察器只映射回合、命令、文件修改、计划与工具调用的事件名称和耗时，
不输出原始命令、路径、命令输出、模型正文、推理内容或工具参数。runner 在完整 JSONL 输出
上独立扫描最后一条合法 `item.completed` 且 `item.type == "agent_message"` 的非空字符串
`text` 作为最终结果；缺少最终消息或终态扫描遇到超限行时 fail closed，不退回裸文本解析。
reader 自然结束时 `process-output.txt` 是 stdout 经过结构化脱敏后的 JSONL artifact；
写盘前先限制物理行长度，再逐行解析 JSON、递归脱敏字符串字段并重新序列化；无效或超限
JSON 行替换为不含原文的安全非终态事件并使 runner fail closed，避免文本替换破坏 JSON
结构。Codex 诊断 stderr 由独立 reader 持续排空，
脱敏后写入 `process-stderr.txt`；stdout 或 stderr 关闭超时、读取异常时都会冻结 sink，
只保留稳定的已读 partial artifact，并把本次 execution 收紧为失败，避免把不完整输出当成
成功。全部已读输出最终都会脱敏写入对应 artifact，卡死 reader 不能再写入对应 sink。

`vega stop --run <run> --reason "..."` 只为指定 run 最新的 active execution 写停止请求；
runner 只终止自己记录的 PID，不枚举或 kill 用户的其他 Codex/Node 进程。每条 verification
命令也写入独立 execution 目录，并在墙钟 deadline 到达时终止对应 owned process tree，
避免后代进程继续写入。

worker `stopped/timed_out` 后立即停止 verification/review，并把 loop 交给人工。reviewer `stopped/timed_out` 不会被解析成 approve，同样进入 `needs_human`。verification 的非零退出、stop 或 timeout 会保留 verification artifacts，并阻止后续结果被当作可交付成功。

外部 runner 返回非零退出码时同样采用保守语义：worker 会先记录 workspace check 和 `runner-error-report.md`，然后进入 `needs_human`，因为 provider/网络错误发生前可能已经修改工作区；reviewer runner 错误也进入 `needs_human`，不能被当作有效 verdict。这里不自动重试，避免在未知部分改动上叠加第二个 worker。

如果 CLI 被关闭或状态半完成，可以运行 `vega recover --run <loop_run_id> --reason "..."`。recover 会检查 run 下的全部 execution 记录，而不是只看最新记录；任一 active execution 的 owned/child PID 仍存活时都会拒绝接管，较旧 active execution 不能被较新的 terminal execution 掩盖。只有没有存活执行主体时，缺失、终态或 stale execution 才允许把 `running` 状态交还为 `needs_human` 并写入 `recovery-report.md`。如果中断发生在某轮 iteration 内，recover 会把该轮显式冻结为 `lifecycle=interrupted`，写入 `iterations/<n>/interruption-report.md`，并保留已有 execution、输出和工作区 diff。中断轮不参与可信 verification/reviewer 或 success 判定；人工确认现场后，`loop continue` 必须使用下一连续编号，不能复用或覆盖原 iteration 目录。recover 不清理工作区、不杀进程、不继续执行。

同一个 loop run 的生命周期写入口使用
`runs/<run_id>/.control/run-mutation.lock` 做本地非阻塞 OS 文件锁。`loop start`、
`loop continue`、`recover`、`finish` 和 decision append 共用该锁；第二个写者 busy
时在修改可信业务 artifact 前 fail closed。锁文件长期保留，owner JSON 只用于诊断，
不能代替内核锁判断所有权。

`vega stop` 不获取该锁，否则 owner 执行 worker/reviewer/verification 时用户无法请求停止。
stop 只写 active execution 的 `stop-request.json`，不由第二个 CLI 追加根 trace。

该锁不是数据库事务、目标仓库全局锁、网络文件系统锁或跨机器协调。v0.1.x 仍是本地单用户
CLI，不提供等待队列、自动重试、分布式调度或持续恶意写者隔离。

## Finish / Handoff

Finish 是 loop 完成后的交付整理层：

```text
vega finish --run <loop_run_id>
```

它读取 loop state、迭代 verdict、测试摘要、最终报告和 memory proposal，生成：

```text
finish-report.md
finish-summary.json
```

Finish 的作用是把“能不能交付、提交前还要检查什么、哪些经验需要人工沉淀”整理清楚。它不会自动 commit、push、release，也不会自动接受 memory。

`finish-summary.json.first_screen` 是从同一次可信证据快照派生的兼容性展示视图，原有摘要字段
继续保留。`finish-report.md` 第一屏固定按当前裁决、实际变更、确定性 Gate、验证结果、
Reviewer 意见、证据上限和下一步排序；它不重新运行验证、不调用模型，也不产生新的裁决。
Reviewer 未提供有效行号时，Finish 明确显示缺失，不自行推断位置。

`first_screen.review` 还会从可信 `changed_files` 和 Reviewer verdict 派生 `coverage`、
`priority_files` 与 `other_changed_files`。新 Review Runtime 要求 `reviewed_files` 精确覆盖
完整变更文件清单，漏报或多报时 fail-closed；历史 verdict 仍可读取，但缺少覆盖声明时不会被
展示为完整覆盖。`priority_files` 只根据 finding 和命名高风险位置帮助人工定位，
不能过滤 `actual_changes.changed_files`。文件级覆盖也不等于逐行语义理解，代码复核仍以宿主
Changes / Diff 视图或完整 staged/unstaged Git Diff 为准。

Finish 会重新读取当前 `.vega.yaml/.vega.yml`，核对启动时文件摘要、scope 摘要和
`project-policy-snapshot.json` 的根状态绑定。升级前缺少三阶段 scope 证据的旧 run 只允许
复盘，不会被提升为 `ready_to_commit`。

## Decision Ledger

没有 Web UI 时，审批落在本地 run 目录的 append-only ledger：

```text
vega decision approve --run <run_id> --type gate --reason "..."
vega decision reject --run <run_id> --type review --reason "..."
vega decision list --run <run_id>
```

每条记录写入：

```text
runs/<run_id>/decisions.jsonl
```

Decision ledger 用来记录“为什么允许继续”“为什么退回”“为什么允许进入提交前检查”等人工判断。它不替代 Git，不替代 memory accept/reject，也不引入用户系统或 Web UI；它只是把 Codex 主会话里的人工确认变成可追溯证据。

## Goal P0

Goal P0 是长任务的人工状态层。实验性 P1 在此之上增加一次受限调度：用户先明确写出 checkpoint
任务，Runtime 再复用一个普通 auto loop，结束后依据 child state、artifact integrity、
verification 和 Reviewer 证据决定 `checkpoint_done` 或 `needs_human`。

```text
goal start -> goal-contract -> progress
goal step --text/--input -> checkpoints/<n>/checkpoint-plan.md
goal run --max-checkpoints 1 -> child loop -> evidence qualification
goal attach -> 校验 child run / manual file -> checkpoint-evidence.json
goal checkpoint-done -> 证据资格检查 -> checkpoint-report.md
goal complete -> goal-final-report.md + goal-eval.md
pause/resume/stop/recover -> goal-state + goal-trace
```

每个 goal run 的基础产物：

```text
runs/<goal_run>/
  state.json
  goal-state.json
  goal-trace.jsonl
  goal-contract.md
  goal-contract.json
  progress.md
  progress.jsonl
  checkpoints/01/checkpoint-plan.md
```

随状态生成的条件产物：

```text
  # checkpoint 完成或阻塞
  checkpoints/01/checkpoint-evidence.json
  checkpoints/01/checkpoint-report.md
  checkpoints/01/checkpoint-blocked.md

  # goal 完成、停止或恢复
  goal-final-report.md
  goal-eval.md
  stop-report.md
  recovery-report.md
```

`state.json` 和 `goal-state.json` 内容保持一致，前者用于复用通用 `status/latest` 读取链路，后者保留 goal 专属语义。同一时间只允许一个 active checkpoint。自动证据必须来自当前 workspace 的真实 child run，且 kind、repo、artifact integrity、verification 和状态会被校验；manual 证据必须是 workspace 或目标仓库内的真实文件。完成后的 checkpoint 不允许继续挂载或改写证据。`goal complete` 表示满足 success conditions，`goal stop` 表示目标被终止，两者不会混用。

P0 不调用 worker/reviewer。P1 只在用户明确执行 `goal run` 时启动一个 child loop，并写入
`active_child_run`、`last_child_run`、`last_child_status` 和 `progress.jsonl`。失败或证据不足
时写 `checkpoint-blocked.md`；不会自动重试、回滚、commit、push、写长期 Memory 或进入下一
checkpoint。

## Tool Adapter

Adapter 是 Vega 和具体 AI 编码工具之间的轻量接入层。当前只实现 Codex skill adapter：

```text
vega adapters init codex --repo <repo>
```

它在目标仓库生成：

```text
.agents/skills/vega-loop/SKILL.md
.agents/skills/vega-review/SKILL.md
```

这些 skill 只描述什么时候调用 `vega loop`、`vega gate`、`vega review`、`vega status`，不安装 hook，不修改全局配置，也不自动执行危险动作。这样可以让主会话理解 Vega 流程，同时保持核心 runtime 与具体工具解耦。
旧版生成的 `.codex/skills` 不会被自动删除或改写；新命令只管理 `.agents/skills`
下的两个 Vega Skill。

初始化会先解析整批目标文件的真实路径；任一目标越过目标仓库或无法确认边界时，在写入前
停止。创建父目录后还会在写文件前再次解析，`--force` 只能覆盖仓库内文件，不能绕过边界。
真实目标仍位于仓库内的 symlink、Windows junction 或其他可解析目录链接可以继续使用。
该检查不承诺抵御拥有并发本地写权限的攻击者在最终检查后替换目录；句柄级 no-follow 写入
和此类 TOCTOU 故障注入需要单独的威胁合同。

## Tool Broker

Tool Broker 只治理 `engineering-change` 的窄只读上下文工具和 Git 检查入口：

| 工具 | 风险 | 行为 |
|---|---|---|
| `file.search` | 低 | 使用 `rg` 搜索目标仓库 |
| `file.read` | 低 | 读取目标仓库内的文本文件 |
| `repo.run_check` | 中 | 执行 allowlisted git check |

Tool Broker 的 Git allowlist 只声明：

- `git.status`
- `git.diff`
- `git.diff_check`

`report` 和 `review` 是 runtime 固定阶段，分别写入固定的 run artifacts，不是调用方可请求的
任意工具。外部 runner 的内部工具调用由其 sandbox 管理；Vega 不声称所有写入或所有
工具调用都经过 Tool Broker。

## OpenAI-compatible Provider 边界

配置 `ciii-direct` 或任何非本地 OpenAI-compatible endpoint 时，请求会把 API key 放在认证
头中，并发送脱敏后的 task 文本与 tool evidence。使用者必须先确认第三方服务可信，并确认
数据出站、留存和合规政策允许发送这些内容。本地代理可能继续转发到上游服务，因此同样需要
核对完整请求路径。文档、测试、trace 和仓库中不得出现真实 key。

## Runtime Artifacts

每次运行写入：

```text
runs/<run_id>/
  state.json
  trace.jsonl
  plan.md
  report.md
  review.md
  eval.md
```

这些文件共同构成 replay 基础：

- `state.json`：当前 run 的结构化状态。
- `trace.jsonl`：每个关键事件和工具调用。
- `plan.md`：执行前计划。
- `report.md`：工程审查报告。
- `review.md`：报告和边界复核结果。
- `eval.md`：artifact、section、trace、tool policy 和可选 memory policy 检查。

## Reviewer 与 Eval

Reviewer 有两层：`engineering-change` 内置 reviewer 是线性检查步骤；`vega review` 是可选的
独立 reviewer runner。两者都不是长期多 Agent 编排。

“独立 reviewer”表示 Vega 启动独立、短生命周期的 reviewer 会话，使用独立编译的 prompt；
启用 `codex-exec` 时 sandbox 固定为 `read-only`，且 reviewer 不继承 worker 的完整聊天记录。
reviewer 仍会在同一目标仓库的只读视图中读取明确编译的 review pack，包括任务或 brief、
当前 diff、验证结果、项目规则、风险门禁和可选 accepted memory。因此这里是角色、会话和输入
边界隔离，不是完全信息隔离，也不是操作系统级安全沙箱。

当前 reviewer 检查：

- 报告是否覆盖任务问题。
- 报告是否声称已经写入目标仓库。
- 报告是否出现自动提交、发布、补丁等禁用动作。
- 工具调用是否符合 allowlist。
- 如果存在 memory proposal，是否仍保持 proposal-only。

Eval 负责检查运行完整性：

- 必需 artifact 是否存在。
- `state.json` schema 是否可解析。
- `report.md` 是否包含必需章节。
- `trace.jsonl` 是否包含必需事件。
- 工具调用是否符合 allowlist。
- reviewer 是否存在失败项。
- 如果存在 memory proposal，是否是合法 JSONL，且 run 目录没有长期 memory 写入。

## 可选 Memory Ledger

Memory 是实验性的经验账本。普通 brief、engineering-change 和成功 loop 不强制生成 proposal。
只有 reflect 显式收到 `--lesson` 时才写：

```text
runs/<run_id>/memory-proposals.jsonl
```

长期 memory 必须由用户显式接受或拒绝：

```text
vega memory accept <proposal_id> --run <run_id>
vega memory reject <proposal_id> --run <run_id>
vega memory search <query>
```

接受或拒绝记录写入：

```text
memory/ledger.jsonl
```

proposal 数量允许为 0。存在 proposal 时仍必须显式接受或拒绝，这保证了 memory 沉淀是人工
确认结果，而不是 runtime 自动扩权。仓库级 memory 使用不暴露绝对路径的本地仓库 scope
精确匹配；同名目录不会互相回填，未绑定仓库的通用经验必须显式保持 `repo=null`。

## 为什么仍然不做数据库

v0.1 重点是证明 loop、state、tool、eval、trace 的基本闭环。文件优先有几个好处：

- 容易检查。
- 容易调试。
- 容易演示。
- 容易手动清理。
- 不引入过早复杂度。

当 memory ledger 增长到需要复杂查询、审计和并发写入时，再考虑 SQLite + FTS5。
