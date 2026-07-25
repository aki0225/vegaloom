# MVP 范围

## 产品名

- `Vega`：产品与品牌名。
- `vegaloom`：公开仓库、Python distribution 和发布制品名。
- `vega`：Python 导入包与 CLI 命令。

## 定位

Vega 是一个本地优先的轻量 AI Coding Harness。`vega do/loop` 提供日常编码闭环，
`vega run engineering-change` 保留为 YAML 驱动的只读检查 baseline。

它要证明的是这些基础能力：

- 显式 task intake。
- YAML 驱动的 loop policy。
- 显式 state。
- 窄 Tool Broker。
- reviewer pass。
- 本地 eval。
- trace/replay artifacts。

## v0.1 目标

冻结一个可安装、可复盘的只读研发任务检查 baseline：

```text
engineering-change
```

它读取任务文件和目标仓库，按 YAML 配置收集上下文，生成计划、报告、复核和评估。

## 范围分层

为避免 v0.1 继续膨胀，当前范围分成三层：

- `v0.1 baseline`：必须保持稳定的最小闭环。
- `v0.1.x extensions`：已经在主线中落地的日常研发 harness 能力，但不反向扩大 v0.1 baseline。
- `experimental extensions`：Goal、Memory 和 adapters 等实验扩展，可验证设计取舍，但不作为
  v0.1 baseline 冻结或发布准备的阻断条件。

## v0.1 baseline 必须有

- CLI：`vega run engineering-change --task <task.md> --repo <repo>`
- CLI：`vega list-loops`
- 包内 `engineering-change` YAML 作为可安装 baseline；workspace 的
  `loops/engineering-change.loop.yaml` 可显式覆盖并保持源码仓镜像
- run 目录：`runs/<run_id>/`
- `state.json`
- `trace.jsonl`
- `plan.md`
- `report.md`
- `review.md`
- `eval.md`
- 只读 file search/read
- Tool Broker Git allowlist 仅包含 `git.status`、`git.diff`、`git.diff_check`
- reviewer pass
- spec-driven eval

## v0.1.x extensions 已落地能力

这些能力已经构成当前日常 Coding Harness 主线，用来把 baseline 扩展成受控 worker、
确定性验证、独立 reviewer 和恢复交付闭环。它们需要继续维护，但不应反向改写
v0.1 baseline 的最小验收定义。

- CLI：`vega brief bug --repo <repo> (--input <file> | --text <text>)`
- CLI：`vega brief feature --repo <repo> (--input <file> | --text <text>)`
- CLI：`vega profile --repo <repo>`
- CLI：`vega plan --repo <repo> (--input <goal.md> | --text <text>) [--scope <profile>]`
- CLI：`vega config check --repo <repo> [--json]`
- CLI：`vega reflect --repo <repo> [--run <run_id>] [--test-log <file>] [--note <text>]`
- CLI：`vega gate --repo <repo> --run <reflect_run_id> [--json]`
- CLI：`vega review-pack --repo <repo> --run <reflect_run_id>`
- CLI：`vega review --repo <repo> --run <reflect_run_id> [--runner codex-exec|none]`
- CLI：`vega loop bug|feature --repo <repo> (--input <file> | --text <text>) [--mode assist|auto]`
- CLI：`vega loop continue --repo <repo> --run <loop_run_id> [--test-log <file>] [--no-verify]`
- CLI：`vega do bug|feature --repo <repo> (--input <file> | --text <text>) [--mode auto|assist]`
- CLI：`vega latest [--kind <kind>] [--json]`
- CLI：`vega status --run <run_id> [--json]`
- CLI：`vega finish --run <loop_run_id> [--json]`
- CLI：`vega stop --run <loop_run_id> --reason <reason>`
- CLI：`vega recover --run <loop_run_id> --reason <reason>`
- CLI：`vega decision approve|reject --run <run_id> --type <type> --reason <reason>`
- CLI：`vega decision list --run <run_id> [--json]`
- CLI：`vega adapters init codex [--repo <repo>] [--force]`
- CLI：`vega goal start --repo <repo> (--input <goal.md> | --text <text>) [--scope <profile>]`
- CLI：`vega goal status --run <goal_run> [--json]`
- CLI：`vega goal step --run <goal_run>`
- CLI：`vega goal attach --run <goal_run> --checkpoint <n> --ref <child_run> --type <type> [--note <text>]`
- CLI：`vega goal checkpoint-done --run <goal_run> --checkpoint <n> [--note <text>]`
- CLI：`vega goal complete --run <goal_run> --note <text>`
- CLI：`vega goal pause --run <goal_run> --reason <reason>`
- CLI：`vega goal resume --run <goal_run>`
- CLI：`vega goal stop --run <goal_run> --reason <reason>`
- CLI：`vega goal recover --run <goal_run> --reason <reason>`
- 目标仓库可选 `.vega.yaml` 项目级机器策略
- `agent-brief.md`、`knowledge-context.md`、`project-context.md`、`agents-md-proposals.md`
- Change plan 的 `change-plan.md`、`scope-profile.md`、`phase-plan.md`、`risk.md`
- Bug brief 的 `repro-plan.md`、`root-cause-hypotheses.md`、`regression-check.md`
- Feature brief 的 `feature-spec.md`、`implementation-plan.md`、`acceptance-criteria.md`、`risk.md`
- Project profile 的 `project-profile.json`、`project-profile.md`
- Reflect 的 `diff-summary.md`、`full-diff.patch`、`test-summary.md`、
  `review-evidence.json`、`project-context.md`、`reflection.md`
- Risk gate 的 `gate-report.md`、`gate-result.json`，并读取 `.vega.yaml` 中的风险路径策略
- Change budget gate：变更文件数、diff 行数、新增文件数、依赖变更和大文件预算
- Scope profile：为 refactor / migration 等大目标显式放宽预算，但保留人工确认和审查证据
- Config check：只读预检 `.vega.yaml`，拦截 schema、验证命令截断和未知 runner
- Review pack 的 `review-pack.md`、`review-prompt.md`、`review-checklist.md`、`project-context.md`、`review-context.json`、`review-prompt-metrics.json/md`
- 隔离 review 的 `review-findings.md`、`review-verdict.json`、`review-runner-output.txt`
- 自动化 loop 的 `loop-plan.md`、`worker-prompt.md`、`worker-prompt-metrics.json/md`、`project-context.md`、`iterations/*/workspace-check.md`、`iterations/*/workspace-check.json`、`iterations/*/verification-summary.md`、`iterations/*/verification-result.json`、`final-report.md`
- Worker/reviewer/verification execution lease：`execution.json`、`process-output.txt`、可选 `stop-request.json`
- Stop/timeout report：中断 attempt 后进入 `needs_human`，不继续后续自动步骤
- Runner error handoff：provider/网络/CLI 错误后写 `runner-error-report.md`，保留可能存在的部分改动并交还人工
- Owned process 边界：worker、reviewer 和 verification 只终止 Vega 自己启动并记录 PID
  的 process tree，不扫描用户其他 Codex/Node 进程
- Worker 污染门禁：auto worker 新增未跟踪文件超过 `budget.max_new_files` 时停止在 `needs_human`
- Prompt 预算门禁：记录实际 prompt 规模，worker/reviewer 超预算时不启动外部 runner
- Review 快照门禁：Reflect 固化工作区指纹和完整 diff；工作区变化或证据 artifact 被改写时，
  reviewer 不启动并进入 `needs_human`
- Recovery report：检查全部 execution；任一 active execution 仍有 owned/child PID 存活时
  拒绝接管，避免较旧 active execution 被较新的 terminal execution 掩盖
- Recovery iteration：安全接管后冻结半完成轮，保留 execution 和部分 diff；后续 continue
  使用下一连续编号，中断轮不参与 success、verification 或 reviewer 判定
- 验证失败不能被 reviewer approve 覆盖，必须进入修复或人工判断
- `loop continue` 只允许同一仓库中处于 `needs_human` 的 run
- `--max-iterations` 只限制 auto worker 自动重试，不阻止人工修复后的 `loop continue`
- Finish 的 `finish-report.md`、`finish-summary.json`
- Run status 的状态摘要、关键产物和下一步指引
- Decision ledger 的 `decisions.jsonl`
- Codex adapter 的 `.codex/skills/vega-loop/SKILL.md` 与 `.codex/skills/vega-review/SKILL.md`
- Goal P0 的 `goal-contract.md/json`
- `goal-state.json`
- `goal-trace.jsonl`
- `progress.md`
- `checkpoint-evidence.json` 和 `checkpoint-report.md`
- `goal-final-report.md` 和 `goal-eval.md`
- `goal step` 只生成 checkpoint plan，同一时间只允许一个 active checkpoint
- `goal attach` 校验 child run 的存在性、kind、repo 和完成资格，不自动执行 child run
- `goal checkpoint-done` 需要可完成证据；manual evidence 必须显式 override
- `goal checkpoint-done` 和 `goal complete` 会重新校验证据当前是否仍有效
- 完成后的 checkpoint 证据不可再修改
- `goal complete` 和 `goal stop` 分别表达成功完成与终止

## 实验能力

以下能力保留用于验证设计取舍，但不属于 bug/feature 主流程成功条件：

- CLI：`vega memory list/search/accept/reject`
- reflect 显式 `--lesson` 时生成 `memory-proposals.jsonl`
- memory proposal ID 与人工 accept/reject ledger
- Goal P0 人工状态层
- Codex skill adapter

实验能力允许完全不使用，也不得要求每个 run 强制生成对应 artifact；其未冻结或后续变化
不阻断 v0.1 baseline 发布准备。

## Future goal loop 草案

P1 的 `goal run --max-checkpoints N`、自动 checkpoint 推进、自动调用普通 loop 等能力不属于当前 v0.1/v0.1.x 发布准备范围。

## 显式非目标

v0.1 不包括：

- Web UI
- 数据库服务
- SQLite memory store
- 向量检索
- LangGraph
- Letta
- 多 Agent 编排
- 后台 daemon
- GitHub webhook
- 浏览器自动化
- AgentToolGate adapter
- 自动安装 hook
- 修改 Codex 全局配置
- 隐式自动代码修改；只有 `loop --mode auto` 或命令级自动入口 `do` 才会调用 worker
- 无上限自动代码修改
- 自动 commit
- 自动 release
- 自动长期 memory 写入

## 示例任务

当前保留两个真实任务样例：

- `examples/tasks/check-atg-mcp-docs.md`
- `examples/tasks/check-vega-runtime-docs.md`

第一个用于外部项目文档一致性审查；第二个用于 Vega 自检，证明它不是单一 ATG demo。

## 版本口径

```text
v0.1 baseline: YAML 驱动的只读 engineering-change Inspection Loop
v0.1.x daily mainline: brief/profile/reflect/gate/review/loop/finish/decision/recover Coding Harness
experimental extensions: Goal、Memory、adapters
future P1: 有限自动 checkpoint 推进
future optional: SQLite + FTS5 memory ledger、replay UI
```

当前产品范围以 `docs/PRODUCT-CONTRACT.md` 为准：核心是上下文编译、受控执行、确定性验证、
隔离审查和证据化恢复；Memory、Goal P0 与 adapter 保持实验状态。

当前稳定版本为 `v0.1.3`，在成功语义维护修复基础上补充公开实验和发布准备材料，不扩大上述范围。
