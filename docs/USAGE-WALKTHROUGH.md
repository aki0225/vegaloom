# Vega 日常使用 Walkthrough

这份文档用一次真实 dogfood 说明 Vega 的日常用法，以及它如何避免“让 AI 一路自动写到失控”。

示例目标不是展示复杂功能，而是验证一条最小但真实的研发闭环：

```text
需求 -> auto worker -> 自动验证 -> 隔离 reviewer -> request_changes
-> 人工修复 -> loop continue -> 再验证/再审查 -> finish -> decision
```

## 0. 示例目标

临时目标仓库里只有一个 `README.md`，需求是：

> 在 README.md 中增加使用说明小节，说明这是 Vega dogfood 临时仓库，并保留原有内容。

目标仓库还包含：

```text
AGENTS.md
.vega.yaml
README.md
```

其中 `AGENTS.md` 约束文档必须使用简体中文，`.vega.yaml` 指定最小验证命令：

```yaml
version: 1

verification:
  commands:
    - python -c "print('dogfood verification ok')"
  max_commands: 1
  timeout_seconds: 60

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

这里没有固定 model，表示沿用使用者自己的 Codex 模型；只按角色覆盖推理强度。worker 负责实现，
使用 `medium` 控制日常小任务成本；reviewer 负责找问题，保留 `high`。两者都使用临时 session，
但 Vega 的 run 证据仍会保存在当前项目的 `runs/`。

## Assist：主会话实现，Vega 负责证据与隔离审查

日常使用推荐先以干净的 tracked 工作区启动 assist：

```powershell
vega loop bug --repo <target-repo> --text "修复导出按钮无响应" --mode assist
vega status --run <loop_run>
```

正常情况下，Vega 会先生成：

```text
runs/<loop_run>/
  workspace-baseline.json
  worker-prompt.md
  project-context.md
```

只有 `status` 显示 `waiting_for_worker` 且 `worker-prompt.md` 确实存在，才开始实现。可以由当前
主会话直接修改，也可以调用 Codex 或 Claude Code 的原生子代理；Vega 不限制宿主内部怎样执行，
但不会采信子代理的“已完成”自述，也不会把其完整聊天记录传给 Reviewer。

实现结束后运行：

```powershell
vega loop continue --repo <target-repo> --run <loop_run>
```

Vega 会用启动基线检查真实 workspace，再执行 scope、verification、reflect、risk gate 和隔离
review。如果启动时已有 tracked diff、基线捕获不完整或 HEAD 漂移，则不会生成 Worker Prompt，
也不能 continue；应读取 `workspace-check.md`，清理或稳定仓库后新建 loop。

## 1. 启动日常入口

```powershell
vega do feature `
  --repo <target-repo> `
  --text "在 README.md 中增加 Usage 小节，说明这是 Vega dogfood 临时仓库，并保留原有内容。" `
  --mode auto `
  --max-iterations 1
```

Vega 会生成并执行：

```text
brief
project-context
worker-prompt
codex exec worker
verification-summary
reflect
review-pack
codex exec read-only reviewer
```

Vega 调用 `codex exec` 时保留用户配置中的 provider、profile、model 与 Windows sandbox，
但固定关闭个人 memories、plugins、hooks 和 legacy notify。任务上下文应来自 Vega 编译的
brief、项目规则与证据包，而不是当前用户的其他会话记忆或插件回调。
Codex 写工具留下的根目录空 `.agents/` 不阻断 Workspace Gate；只要其中出现文件、子目录、
链接或无法确认的目录状态，Vega 仍会 fail-closed 并交还人工。

关键产物：

```text
runs/<loop_run>/
  agent-brief.md
  project-context.md
  worker-prompt.md
  iterations/01/
    verification-summary.md
    review-findings.md
    review-verdict.json
    fix-prompt.md
```

## 2. 第一轮 reviewer 打回

worker 确实完成了 README 修改，自动验证也通过了：

```text
dogfood verification ok
```

但隔离 reviewer 返回 `request_changes`，原因是 worker 写了英文标题：

```markdown
## Usage
```

这违反了目标仓库 `AGENTS.md` 的中文文档约束。Vega 因此没有进入成功状态，而是生成：

```text
iterations/01/fix-prompt.md
```

这说明 reviewer 没有只看“功能是否完成”，还使用了 `project-context.md` 中的项目规则。

## 3. 人工修复后继续

把标题改成：

```markdown
## 使用说明
```

然后继续同一个 loop：

```powershell
vega loop continue --repo <target-repo> --run <loop_run>
```

这里有一个重要设计点：

- `--max-iterations` 只限制 auto 模式里的自动 worker 重试次数。
- 它不阻止人类接管后继续 `loop continue`。

这样可以避免自动化无限扩张，同时保留人工修复后的复盘和隔离审查能力。

## 4. 第二轮继续打回：工程细节问题

第二轮 reviewer 又返回 `request_changes`，原因是 README 存在 trailing whitespace，`git diff --check` 不通过。

这次问题不是需求理解，而是工程卫生：

```text
git diff --check failed
```

修复方式是去掉行尾空白，重新确认：

```powershell
git diff --check
```

## 5. 第三轮通过

再次运行：

```powershell
vega loop continue --repo <target-repo> --run <loop_run>
```

最终 reviewer 返回：

```json
{
  "verdict": "approve",
  "summary": "变更满足在 README 增加使用说明并保留原有内容的需求，验证记录通过。"
}
```

此时 loop 进入：

```text
status = success
```

## 6. Finish 与人工决策

生成交付摘要：

```powershell
vega finish --run <loop_run>
```

记录人工决策：

```powershell
vega decision approve `
  --run <loop_run> `
  --type finish `
  --reason "dogfood 已人工检查 review、验证摘要和最终 diff" `
  --ref finish-report.md
```

最终关键产物：

```text
finish-report.md
finish-summary.json
decisions.jsonl
trace.jsonl
state.json
```

## 7. 这次 dogfood 证明了什么

这次示例不是“顺利一次过”的 happy path，反而更有价值：

1. worker 能真实改代码或文档。
2. `.vega.yaml` 的验证命令能进入执行链路。
3. reviewer 能基于 `AGENTS.md` 抓到项目规则问题。
4. reviewer 能基于 `git diff --check` 抓到 trailing whitespace。
5. auto loop 被打回后，人类可以接管并继续同一个 run。
6. finish 和 decision 能把最终交付结论落成可追溯证据。

### 7.1 Execution Control 真实 Codex dogfood

2026 年 7 月 10 日又使用本机 `codex-cli 0.144.1`，在项目 `runs/` 下的隔离 Git 仓库执行了两类真实测试。

正常闭环：

```text
run: 20260710-160837-729852-feature-loop
worker: completed，约 59 秒
verification: passed，0.28 秒
reviewer: completed，约 14 秒
verdict: approve
loop status: success
```

隔离证据显示 worker 使用 `workspace-write`，reviewer 使用 `read-only`；`review-context.json` 中 `contains_worker_chat=false`。当次全局 Codex 配置为高推理强度，worker/reviewer 分别消耗约 18.6k/16.1k tokens。它证明链路可用，也暴露出短任务继承高推理配置时成本偏高，后续应该提供显式 runner profile，而不是让 runtime 隐式降低 reviewer 能力。

主动停止：

```text
run: 20260710-160639-831911-feature-loop
stop command: success
worker status: stopped
loop status: needs_human
stop-report.md: exists
unrelated process: remained alive
target repo: clean
```

另一次首次 worker 尝试在已经修改 README 后遇到上游 502 并返回非零退出码。这暴露出“runner 失败不等于工作区没变化”的真实问题，因此 runtime 现在会写 `runner-error-report.md`、保留 workspace check，并把现有 diff 作为部分完成现场交还人工，而不是直接假设任务完整失败。

### 7.2 角色化 Codex runner 真实验证

2026 年 7 月 10 日实现 worker/reviewer 独立配置后，又执行了两次真实 Codex 小任务。

成功闭环：

```text
run: 20260710-165416-208376-feature-loop
worker command: --config model_reasoning_effort="medium" --ephemeral
worker: completed，约 62.29 秒，23,869 tokens
verification: passed，0.19 秒
reviewer command: --config model_reasoning_effort="high" --ephemeral
reviewer: completed，约 12.36 秒，17,675 tokens
verdict: approve
loop status: success
```

`execution.json` 证明最终命令确实按角色编译；`project-context.md` 也记录了 worker/reviewer
的有效策略。目标仓库只修改 `README.md`，项目验证、`git diff --check` 和隔离 reviewer 均通过。

在成功 run 前还有一次 `20260710-165114-223359-feature-loop` 被 reviewer 打回：worker 为了保留
目标仓库原有 CRLF，把新增 Markdown 行也写成 CRLF，而该仓库的 Git 基线没有规范化行尾，
`git diff --check` 将新增行识别为 trailing whitespace。reviewer 返回 `request_changes`，
没有因为功能测试通过就直接 approve。第二次在目标仓库通过 `.gitattributes` 和 `AGENTS.md`
明确 LF 规范后，闭环成功。

这组结果只能证明“角色化参数生效”和“reviewer 能阻止格式问题”，不能证明 token 成本已经下降。
与前一次全局 `xhigh` dogfood 相比，本次 worker token 反而更高，原因包括 prompt/context 大小、
工具失败后的补救步骤和模型执行路径差异。下一步若继续优化成本，应优先压缩重复的
`project-context/review-pack`，并建立同任务多次基线，而不是继续盲目降低 reviewer 推理强度。

### 7.3 Prompt 度量与预算真实验证

加入 prompt 指标和总字符门禁后，使用与 7.2 相同的 LF dogfood 任务再次运行：

```text
run: 20260710-171635-280713-feature-loop
worker prompt: 2,193 chars / 3,595 UTF-8 bytes
worker: medium，约 67.78 秒，23,566 tokens
reviewer prompt: 4,099 chars / 6,209 UTF-8 bytes
reviewer: high，约 14.94 秒，15,659 tokens
verification: passed
verdict: approve
loop status: success
```

review pack 不再分别重复注入 Project Profile 和 Project Knowledge；它们统一由
`project-context.md` 提供。在这个小仓库中，被移除的重复段落约 632 字符。但因为 reviewer
现在也获得了之前缺失的完整 runtime 策略，最终 prompt 相比旧 run 并没有简单缩短，说明
“修正上下文完整性”和“减少重复”需要分别衡量。

同任务中 reviewer token 从 17,675 降到 15,659，worker 从 23,869 变为 23,566，但这仍然只是
单次样本，不能把变化全部归因于 prompt 去重。真正得到确认的是：

- 每次外部调用前都能看到实际字符、字节、行数和分段规模。
- 超预算时不会启动 worker/reviewer，也不会静默删除关键证据。
- token usage 与 prompt 字符数不是简单线性关系，工具调用和模型执行路径仍占较大影响。

## 8. 防止自动化造“屎山”的 Harness 点

Vega 的设计不是让 AI 无限自动写，而是把自动化关进一组 harness 里。

### 8.1 输入 Harness

- `brief` 明确任务目标、非目标、验收标准。
- `project-context.md` 注入项目规则、验证命令和可选的 accepted memory。
- `AGENTS.md` 约束 AI 的长期行为规则。
- `.vega.yaml` 约束 runtime 的机器策略。

### 8.2 执行 Harness

- 默认不自动 commit、push、release。
- `auto` 必须显式 opt-in。
- `--max-iterations` 限制自动 worker 轮数。
- reviewer 使用 read-only sandbox。
- request_changes 后的修复可以由人接管，而不是让自动 worker 无限扩张。

### 8.3 验证 Harness

- `vega config check --repo <repo>` 先做只读配置预检，不执行命令。
- 截断的验证命令、YAML/schema 错误、未知 runner 会在 loop 前暴露。
- 自动执行 `.vega.yaml` 或 project profile 指定的验证命令。
- `git diff --check` 参与 reflect/reviewer 证据。
- 验证失败不能被 reviewer approve 覆盖。
- 验证失败会进入 `needs_human` 或 `needs_fix`。

### 8.4 变更预算 Harness

`.vega.yaml` 可以配置 change budget：

```yaml
budget:
  max_changed_files: 5
  max_diff_lines: 300
  max_new_files: 3
  max_file_bytes: 200000
  forbid_new_dependencies: true
  forbid_large_generated_files: true
```

Risk gate 会据此识别：

- 变更文件数是否超预算。
- diff 行数是否超预算。
- 新增文件数是否超预算。
- 是否新增或修改依赖声明。
- 是否新增大体量生成物或大文件。

这些命中后会升级到 `human-review`，避免小需求被 AI 写成大改动。

auto worker 结束后还有一层更早的工作区污染门禁：如果本轮新增未跟踪文件超过 `budget.max_new_files`，Vega 会写入 `workspace-check.md/json` 并停止在 `needs_human`，不会继续跑验证或 reviewer。它只保留证据，不自动删文件。

如果用户明确给的是大目标或重构文档，不应该用小任务预算硬拦，而应该先声明 scope：

```powershell
vega plan --repo . --input goal.md --scope refactor
```

这会生成：

```text
change-plan.md
scope-profile.md
phase-plan.md
risk.md
```

人工确认后再记录 decision，并把大目标拆成多个 phase 执行。后续 gate 可以使用同一个 profile：

```powershell
vega gate --repo . --run <reflect_run> --scope refactor
```

这样“大改”不是被禁止，而是必须显式声明、分阶段、可审查、有授权。

### 8.5 审查 Harness

- reviewer 使用独立、短生命周期会话，不继承 worker 的完整聊天记录。
- reviewer 在同一目标仓库的只读视图中读取明确编译的 review pack、diff、验证日志、
  项目规则、风险门禁和可选 accepted memory。
- 这里是角色、会话和输入边界隔离，不是容器或操作系统级安全隔离。
- Reflect 固化 `full-diff.patch` 和 `review-evidence.json`；Review 校验 HEAD、tracked diff、
  untracked 内容清单和 artifact 哈希，证据过期时不启动 reviewer。
- `fix-prompt.md` 只要求修复 reviewer 指出的 findings，避免扩大范围。

### 8.6 状态与追溯 Harness

- `state.json` 记录状态机。
- `trace.jsonl` 记录关键事件。
- 每轮都有独立的 `iterations/<n>/`。
- `execution.json` 记录 worker/reviewer 的 owned PID、heartbeat、deadline 和终态。
- `vega stop --run <run> --reason "..."` 通过 `stop-request.json` 请求当前 owned process 安全停止。
- `timeout-report.md` / `stop-report.md` 记录中断原因，并阻止本轮继续 verification/review。
- `recovery-report.md` 只接管 lease 过期、PID 消失、execution 终态或记录缺失的 `running` loop。
- recover 会把半完成轮冻结为 `lifecycle=interrupted`，并写
  `iterations/<n>/interruption-report.md`；该轮只保留现场，不作为成功证据。
- recover 后的 `loop continue` 使用下一连续 iteration；如目标目录已存在或 state 编号有缺口，
  Runtime 会拒绝继续，不覆盖旧证据。
- `loop continue` 只允许同一仓库中处于 `needs_human` 的 run。
- `.control/run-mutation.lock` 保护同一 loop run 的 start、continue、recover、finish 和
  decision append；busy 时命令立即失败，不等待或自动抢锁。
- `.control/run-mutation-owner.json` 只提供受限诊断信息，是否持锁以内核文件锁为准。
- `vega stop` 不获取 mutation lock，只写 active execution 的 `stop-request.json`，不由
  第二个 CLI 追加根 `trace.jsonl`。
- `finish-report.md` 汇总结论和提交前 checklist。
- `decisions.jsonl` 记录人工批准或拒绝原因。

### 8.7 Memory Harness

- 普通 brief/loop 不要求生成 memory proposal。
- 只有 reflect 显式收到 `--lesson` 时才生成经验候选。
- 长期 memory 必须人工 accept。
- 不允许一次任务自动污染长期项目记忆。

## 9. 阶段整理后的观察项

现在已有的 harness 能管住基本风险：输入、配置预检、执行、验证、工作区污染、变更预算、
prompt 预算、隔离审查和状态追溯。Memory、Goal P0 与 adapter 保持实验性质，不作为主流程
成功条件。可以用项目内脚本做确定性 dogfood eval：

```powershell
python scripts/dogfood_eval.py --runner none --workspace .
```

脚本会在 `runs/dogfood-eval-<timestamp>/` 里输出 `summary.json` 和 `EVAL-REPORT.md`，当前覆盖：

- 普通 bug loop 在零 Memory Proposal 时仍能完成 review 和 finish。
- 只有显式 `--lesson` 才生成经验候选，且不会自动写长期 ledger。
- 配置预检。
- owned process 停止隔离。
- worker 污染门禁。
- prompt 预算门禁。
- 大范围 scope gate。
- Goal P0 生命周期。

Goal P0 case 会验证：

- `goal start -> step -> attach -> checkpoint-done -> complete/pause/resume/recover/stop` 状态链路。
- child run 证据会校验类型、仓库和完成资格，完成后的 checkpoint 不可再修改。
- `goal-contract.md/json`、`goal-state.json`、`goal-trace.jsonl`、`progress.md`、`checkpoint-plan.md`、`checkpoint-evidence.json`、`checkpoint-report.md`、`goal-final-report.md` 和 `goal-eval.md` 存在。
- 未生成 `worker-prompt.md`、`loop-run.txt` 或长期 memory ledger。
- 目标仓库 `git status` 不被 goal step 改动。
- `vega status` / `latest --kind goal` 能识别 goal run。

以下方向只记录为观察项，不在 v0.1.x 继续实现：

- architecture gate：新增框架、跨层调用、目录结构变化必须人工确认。
- delete reason gate：删除文件时必须说明业务理由和回滚方式。
- generated code provenance：大段生成代码必须标记来源和验证方式。
- test impact gate：变更命中核心模块时要求更强测试证据。

只有真实 dogfood 多次暴露同一类问题时，才考虑把它们沉淀进 `.vega.yaml`。当前停止线是：
冻结新功能，只修影响核心证据链和安全边界的缺陷。
