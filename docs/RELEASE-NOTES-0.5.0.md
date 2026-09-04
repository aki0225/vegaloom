# Vega v0.5.0 发布说明

> 状态：已发布，日期：2026-09-04。注解 Tag `v0.5.0` 与 GitHub Release 指向
> `1a2ad71929805485ac44d30fba322b15cd150519`；PR 与 main 的 10 个 CI job 均通过，
> wheel 和 sdist 已从该提交构建并上传。

v0.5.0 继续复用既有 ChangeRun、Git Candidate、Verification、Risk、Reviewer 和 Finish，
重点是让日常任务少记一个 Run ID、少切换几条命令，同时保留人工授权和
fail-closed 边界。

## 主要变化

### 日常入口

进入目标 Git 仓库后，常用路径为：

```powershell
vega change "描述要修复或实现的变更"
vega status
vega explain
```

- `vega change` 创建或继续当前仓库唯一未完成的 ChangeRun；
- `vega status` 展示阶段、会话、Diff、Verification、Risk、Reviewer 和下一步；
- `vega explain` 只读解释当前决定、已确认事实、未知项和安全动作；
- 省略 `--run` 时优先按源仓库绑定和 `AgentState.updated_at` 选择唯一未完成任务；没有活动
  任务时显示最近更新的终态 Run；多个候选、损坏记录或归属不一致时拒绝猜测；
- `start`、`approve`、`run` 仍保留给需要显式控制 Planning、批准和执行阶段的高级流程。

`status` 和 `explain` 不调用模型、不重新运行验证，也不修改运行状态。`--full` 和 `--json`
用于排障或脚本消费。

### Provider 与当前终端

Codex 和 Claude Code 共用同一 Provider Session、Candidate 和 Core 门禁合同。Claude Code
继续使用固定 safe-mode 和按角色限制的工具面；项目 Verification 仍由 Vega Core 执行。

Provider 待处理请求可以在 `vega change` 的当前终端显示脱敏摘要，但摘要不是完整授权事实。
如果协调状态缺少足以安全判断的原始目标、权限或策略上下文，Vega 会停止当前 attempt，并
关闭对应 pending，再转 Recovery 或 Provider 原生会话；复杂、敏感或无法分类的请求同样如此。
高级 `vega run` 仍保持活动 Turn 时，`vega respond` 继续可用，但会重新校验完整生命周期绑定。
**同终端可见不等于同终端自动批准。** JSON 和非交互终端不会读取 stdin。

### Reviewer timeout 恢复

对明确 `timed_out` 的 Core Work Item Reviewer，只有在 Candidate、Workspace、Contract、Plan、
Verification、Risk、预算、无外部副作用和执行终态都能重新证明时，才自动恢复一次。恢复：

- 使用新的独立 Reviewer Session；
- 复用原 Candidate 和 child；
- 完整重跑 Verification、Risk 和 Reviewer；
- 不启动新的 Coding Worker。

第二次超时、`error`、`stopped`、终止未确认、最终集成 Reviewer 或任一前提不一致时保持
`needs_human`，交还人工。

## 仍然保留的边界

- Verification 失败不能由 Reviewer `approve` 覆盖；
- Reviewer 不接收 Worker 完整聊天或中间推理；
- Vega 不操作用户当前分支，也不自动 push、merge、release 或部署；
- `ready_to_commit` 只表示候选可交给人检查，不是生产安全证明；
- 数据库、支付、权限、部署和未知外部副作用继续需要显式风险审查和人工判断。

## 发布验证

真实 Codex bounded 与 Claude Code human 路径已完成固定临时仓库验收；Reviewer timeout
使用确定性故障注入验证，没有冒充真实 Provider timeout。脱敏结果见
[`v0.5.0-daily-ux-smoke.md`](../examples/evidence/v0.5.0-daily-ux-smoke.md)。

发布提交的 Python 3.12 CI 分片结果为 Core `312 passed`、Core Heavy `126 passed`、
Supervisor `441 passed`、Security `434 passed, 7 skipped`、Experimental `279 passed`；
Python 3.11 兼容任务为 `1592 passed, 7 skipped`。Windows、POSIX 和 package smoke
同时通过。制品摘要、Tag 和 Release 记录见
[`RELEASE-CHECKLIST.md`](RELEASE-CHECKLIST.md)。
