# 使用说明

## 1. 准备

```powershell
python -m pip install -e .
vega capabilities
vega config check --repo <target-repo>
vega adapters init codex --repo <target-repo>
```

`config check` 只检查配置和仓库准备状态，不运行测试。先处理 `.vega.yaml`、`runs/` ignore、
Python import、行尾和验证命令 warning。

Adapter 只写入 `.agents/skills/vega-agent/SKILL.md`。目标仓库已有同名文件时默认不覆盖。

## 2. 调查

在 Codex 主会话中调用 `$vega-agent`，或从 Codex、Claude Code、普通终端直接使用 CLI。
只要根因、范围或验收仍有一项不明确，就先建立只读 Planning ChangeRun：

```powershell
vega start --repo <target-repo> --text "导出按钮点击后没有反应"
vega run --run <run_id> --timeout 900
```

Vega 在固定 Git revision 的受管 Worktree 中调查，输出：

```text
runs/<run_id>/planning-proposal.json
runs/<run_id>/planning-proposal.md
runs/<run_id>/change-contract.json
runs/<run_id>/execution-plan.json
runs/<run_id>/plan-card.md
```

Proposal 区分事实、假设、未决问题、建议范围和验证建议，并保留来源引用。随后，同一次
`run` 调用确定性 Contract Compiler：

1. 重新校验 Proposal、固定 source revision 和 Planning 上下文；
2. 只接受 `.vega.yaml` 已登记的验证命令；
3. 检查候选路径、风险声明和文件/命令预算；
4. 生成未批准的 Change Contract、Execution Plan 和 Plan Card；
5. 停在 `awaiting_approval`。

路径越界、未知验证、高风险声明缺失或 source revision 漂移时，状态进入 `needs_human`，
不会创建合同或启动 Worker。编译通过后先读取 `plan-card.md`，再批准：

```powershell
vega approve --run <run_id> --actor human
vega run --run <run_id> --timeout 900
```

## 3. 使用显式 Contract 创建 ChangeRun

目标、范围和验证已经明确时，可以跳过只读调查。保持在保存 `runs/` 的 Vega workspace
中执行：

```powershell
vega start --repo <target-repo> `
  --contract <change-contract.json> `
  --execution-plan <execution-plan.json>
```

Vega 会：

- 校验 Contract 与 Plan；
- 绑定目标仓库和基线 revision；
- 创建本地任务分支与隔离 Worktree；
- 写入初始 State、Trace、Task Brief 和 Checkpoint；
- 停在 `awaiting_approval`。

查看状态：

```powershell
vega status --run <run_id>
vega status --run <run_id> --json
```

## 4. 批准并运行

默认由人检查 Plan Card：

```powershell
vega approve --run <run_id> --actor human
vega run --run <run_id> --timeout 900
```

仓库维护者也可以为重复、低风险任务预先配置：

```yaml
approval:
  bounded:
    enabled: true
    policy_id: docs-and-tests-v1
    allowed_paths:
      - docs/**
      - tests/**
    max_changed_files: 4
    max_work_items: 2
    max_repair_rounds: 1
    max_auto_replans: 0
```

配置必须进入 Git。调用方还要显式选择：

```powershell
vega run --run <run_id> --timeout 900 --approval bounded
```

这条命令只在以下条件全部成立时批准并继续 Worker：

- Contract 与每个 Work Item 都列出具体文件；
- Verification 已在 `.vega.yaml` 登记；
- 没有未决问题、高影响副作用或人工风险规则命中；
- 文件、Work Item、Repair 和 Replan 预算没有越界；
- 当前 Workspace 与策略仍和 Planning 基线一致。

任一条件不满足，状态仍是 `awaiting_approval`，状态卡说明原因。可以改用人工批准，也可以重新
调查和修订任务。策略或 Contract 变化会使已有 bounded 批准失效；后续验证、Reviewer 和最终
人工 Git 交付没有捷径。

`run` 会连续推进合同允许的 `next` 和 `repair`，直到：

- 全部 Work Item 完成；
- 需要新的批准；
- 需要人工处理；
- 达到预算；
- 被暂停、停止或中断。

单次 Worker 或 Reviewer timeout 为 60～3600 秒。默认使用 Codex；首次执行可选 Claude Code：

```powershell
vega run --run <run_id> --provider claude --timeout 900
```

同一 ChangeRun 会沿用已经建立的 Provider Session。显式传入另一个 Provider 会被拒绝，避免
Planning、Worker 和恢复阶段意外落到不同会话。一次性短会话：

```powershell
vega run --run <run_id> --timeout 900 --fresh-session
```

当前 Provider 不可用时默认报错，不会静默切换到另一个 Provider 或 fresh session。

## 5. 看进度

```powershell
vega watch --run <run_id> --follow
vega latest
```

状态卡包含：

- 当前阶段和 Work Item；
- Worker / Reviewer Thread、owner、Turn 和压缩次数；
- 当前 child 与 Git 变更；
- Verification、Risk 和 Reviewer；
- 待发送 Steer、待响应请求；
- Checkpoint、证据健康和允许动作。

`watch` 只显示事件名和耗时。完整 stdout、stderr、模型正文和命令参数不进入事件流。

## 6. Steer

方向需要补充但合同没变：

```powershell
vega steer --run <run_id> `
  --role worker `
  --text "补充检查重复提交和失败回滚"
```

`--role reviewer` 会选择最近的 Reviewer Session，也可以传完整角色名。

Codex Turn 运行中，Steer 在安全事件边界发送；Turn 尚未开始时，它会附加到下一次输入。
Claude Code V1 只支持后一种方式：排队后在下一 Turn 发送，状态卡不会把它标成中途送达。
Steer 超过 8 KiB、目标由人工接管或 Session 不存在时拒绝。

## 7. 响应 Codex App Server 请求

命令或文件审批：

```powershell
vega respond --run <run_id> `
  --interaction <request_id> `
  --decision accept
```

可用决定：

```text
accept
accept-session
decline
cancel
```

`accept-session` 由 Provider 的会话级审批缓存解释，只对后续匹配请求生效。命令类型、权限或
策略范围不同，Codex 仍会再次询问；Vega 不在本地扩大这份授权。
App Server 不能安全分类命令时，状态卡显示“未分类命令执行”，并要求先接管原生会话确认；
Vega 不会把原始命令或参数复制进协调状态来换取更详细的摘要。

权限、工具用户输入和 MCP elicitation 使用 JSON：

```powershell
vega respond --run <run_id> `
  --interaction <request_id> `
  --input <response.json>
```

请求类型分别要求：

- 权限：`{"permissions": {...}}`
- 工具输入：`{"answers": {...}}`
- MCP：`{"action": "accept|decline|cancel", ...}`

响应中检测到敏感信息时 Vega 拒绝落盘。改用原生会话接管。

Claude Code Provider 使用固定工具白名单和非交互权限模式，不生成这类待响应请求。

## 8. 原生会话接管

```powershell
vega takeover --run <run_id> --role worker --reason "需要人工处理登录"
```

Vega 先请求停止活动 Turn，确认终态后把 Thread owner 改为 `human`，并显示：

```text
codex resume <thread_id>
```

Claude Code Session 对应显示：

```text
claude --resume <session_id>
```

如果接管时 Session 原本空闲、没有 active Writer binding，且人工没有改 Workspace，可以交还：

```powershell
vega reclaim --run <run_id> --role worker
```

活动 attempt 被接管时会保留中断的 Writer binding，不能直接 `reclaim`。人工处理完成后先走
`recover` 做现场对账，或生成 Handoff / 新 ChangeRun。人工已经改过代码时同样先核对 Git 和
外部副作用，不能把人工改动静默归因给旧 Worker。

## 9. Revision

根因、实现步骤或候选文件变化，但仍在原合同内：

```powershell
vega revise --run <run_id> `
  --contract <current-contract.json> `
  --execution-plan <execution-plan-v2.json>
```

Contract 内容未变时，Plan revision 可以自动采用。Contract 内容变化时，状态回到
`awaiting_approval`：

```powershell
vega approve --run <run_id> --actor human
vega run --run <run_id>
```

实际 Diff 越界、命中未授权风险或预算耗尽时，即使提议文件写得合法也不会自动采用。

## 10. 只重跑验证

代码和 Reviewer finding 都不需要改，只是验证命令或本地依赖环境需要修正：

```powershell
vega retry --run <run_id>
```

该命令复用当前 Diff 和原 Worker 证据，只重跑 Verification、Risk 和 Reviewer。源码、未跟踪
文件、Git 控制状态或 Candidate 变化时拒绝。

## 11. Worker 异常

Worker 超时、断网、429、Provider 5xx、终端关闭或进程消失后，先确认旧进程状态，再准备：

```json
{
  "reason": "Worker 在 Turn 中断后没有可信终态",
  "workspace_explained": true,
  "external_side_effects": "none",
  "actor": "human",
  "evidence_refs": []
}
```

执行：

```powershell
vega recover --run <run_id> --input <recovery.json>
```

规则：

- 旧进程仍活着：拒绝新 Writer；
- Workspace 未变且操作未开始：允许恢复；
- partial Diff 可解释、无外部副作用：保留现场并继续；
- Workspace 不清楚或外部副作用 unknown：`needs_human`；
- 数据库、支付、部署和外部 API 未知副作用不自动重放。

人工核对未知副作用后：

```powershell
vega adjudicate --run <run_id> --input <adjudication.json>
```

## 12. 暂停、停止和本机恢复

```powershell
vega pause --run <run_id> --reason "等待接口确认"
vega resume --run <run_id>

vega stop --run <run_id> --reason "任务取消"
```

`pause` 只在没有活动 Writer 时生效。`stop` 不回滚代码或删除 Artifact。

## 13. 换机器

在现场可解释、没有活动 Writer 时：

```powershell
vega handoff --run <run_id> --reason "换机器继续"
```

`handoff` 生成 Task Card 和本机 Resume Capsule，不替用户提交或推送任务分支。人工：

1. 检查任务分支上的 WIP 和 Task Card；
2. commit；
3. push；
4. 在新机器拉取同一分支；
5. 进入仓库后恢复：

```powershell
vega resume --repo .
```

也可以显式指定 Task Card：

```powershell
vega resume --repo . --task .vega/tasks/<task-card>.md
```

若目标项目把 `.vega/` 整体写入 `.gitignore`，只对生成的 Task Card 显式使用
`git add -f -- .vega/tasks/<task-card>.md`；WIP 仍按正常规则逐项暂存。

新生成的 Task Card 使用 Git 条目身份（mode + Blob）绑定交接内容。换机后的 CRLF/LF
checkout 差异不会被误判为代码变化；路径、mode、Blob 内容或 Task Card 语义发生变化时仍会
拒绝恢复。

恢复会创建新的本机 run。旧聊天、Thread ID、Trace 和凭据不参与恢复。

## 14. 完成

正常 `run` 会自动推进 `finalizing`。如果在发布父终态前中断，重新执行：

```powershell
vega run --run <run_id>
```

完成后读取：

```text
runs/<run_id>/agent-final-report.md
runs/<run_id>/agent-final-report.json
```

人工重点检查：

- 完整 changed files；
- Reviewer finding 和建议优先文件；
- 实际运行的验证命令与 exit code；
- 高风险命中；
- 最终集成 Reviewer；
- 未证明事项。

Vega 到这里结束。Git push、PR、merge 和 release 由人执行。
