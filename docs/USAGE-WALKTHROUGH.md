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

在 Codex 主会话中调用 `$vega-agent`，或者按
[修改前调查与计划](PLAN-FIRST-PROTOCOL.md) 手工完成：

1. 读取项目规则和 Git 状态；
2. 复现或定位问题；
3. 区分事实、假设和未决问题；
4. 生成 Change Contract；
5. 生成 Execution Plan；
6. 展示给用户确认。

批准前不启动 Worker。

## 3. 创建 ChangeRun

保持在保存 `runs/` 的 Vega workspace 中执行：

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

```powershell
vega approve --run <run_id> --actor human
vega run --run <run_id> --timeout 900
```

`run` 会连续推进合同允许的 `next` 和 `repair`，直到：

- 全部 Work Item 完成；
- 需要新的批准；
- 需要人工处理；
- 达到预算；
- 被暂停、停止或中断。

单次 Worker 或 Reviewer timeout 为 60～3600 秒。默认使用 Codex App Server；一次性短会话：

```powershell
vega run --run <run_id> --timeout 900 --fresh-session
```

App Server 不可用时默认报错，不会静默切换到 fresh session。

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

Turn 运行中，Steer 在安全事件边界发送；Turn 尚未开始时，它会附加到下一次输入。Steer
超过 8 KiB、目标由人工接管或 Session 不存在时拒绝。

## 7. 响应 App Server 请求

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

## 8. 原生会话接管

```powershell
vega takeover --run <run_id> --role worker --reason "需要人工处理登录"
```

Vega 先请求停止活动 Turn，确认终态后把 Thread owner 改为 `human`，并显示：

```text
codex resume <thread_id>
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

Vega 生成 Task Card 和本机 Resume Capsule，但不执行 Git 操作。人工：

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
