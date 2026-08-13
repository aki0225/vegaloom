# Supervisor Agent V1 当前交接

> 日期：2026-08-13
>
> 分支：`experiment/supervisor-agent-v1`
>
> 主线基线：`origin/main@706286d`
>
> 状态：`Gate 2A 已实现 / 等待 PR CI`

## 当前结论

Gate 0、Gate 1 与 Gate 2A 已在同一个实验分支实现。当前版本适合推送远端并通过 Draft PR
运行 CI，但**不应直接合并到 `main`，也不应开始 Gate 2B 的真实 Codex 接入**。

现有默认 `vega do / loop / goal`、Reviewer、Verification、Risk Gate、Finish 与成功语义均未
改变。`vega agent` 仍是实验入口；Graph 只能路由到 `finalizing`，不能自行写入
`ready_to_commit`。

## 本轮已完成

1. 对同一 Agent run 的批准、计划修改、dispatch、observe、recover、pause、resume、steer 和
   stop 使用 mutation lock 串行化。
2. dispatch 在发布 `acting` State 前，以 run-local write-once marker 登记 operation identity；
   同一 run 不允许复用旧 `operation_id`。随后原子持久化唯一 child/operation binding，并保守
   标记 operation 可能已经开始；该标记表示已越过不可自动重试边界，不等于证明真实进程已经启动。
3. CLI 输入的 Observation 永远按外部 Claim 保存，不能伪造完成状态或 Gate 结果；受信
   Observation 必须绑定当前 Work Item、child 和 operation；Observation ID 不能包含路径，
   外部 Claim 与 Recovery 机器对账均使用 write-once Artifact，不能复用后覆盖旧证据。
4. 旧 binding 尚未被可靠核销时一律禁止第二 Writer；Worker 仍存活、execution 缺失或损坏、
   execution identity 与当前 operation 不一致、Trace 损坏等情况都会保留原
   child/operation binding。
5. 当前 dispatch 后不存在可依赖的 `worker_reserved` 中间态或第二次 `confirm_started`。
   只有未来由受信 Adapter 在同一原子边界内证明“进程未启动”时，才可评估自动释放旧 binding；
   Gate 2A 对缺少此类证据的现场保持 fail-closed。
6. 旧版两阶段 dispatch 留下的 `operation_started=false` 只表示未取得启动确认，不能作为
   operation 未启动证明；升级后恢复一律保留原 binding 并交给人工。
7. Recovery 只有在 Checkpoint 成功落盘后才提交解除 Writer 的新 State；Checkpoint 写入失败时
   保留原 binding，避免崩溃窗口错误释放 Writer。
8. partial diff、数据库/支付/部署/外部 API 副作用、损坏状态、未知 schema 和不完整现场均
   fail-closed 进入人工处理。
9. SQLite Graph checkpoint 丢失时仍从 Agent State、Checkpoint 与真实 Workspace 对账。
10. 增加 `pause`、`resume-local`、`stop` 和结构化 `recover` 命令；不执行自动回滚、提交或推送。
11. Plan revision 写入前先撤销旧批准和 dispatch 权限；批准、恢复和 `next / repair` 只有在当前
    Checkpoint 与 Task Brief 成功落盘后，才发布可 dispatch 的 State。
12. dispatch 前重新校验 State、批准 Plan、safe Checkpoint 与 Task Brief manifest 属于同一
    revision、Work Item 和 Workspace，拒绝 stale Plan 或 stale Task Brief。
13. 跨机器 Task Card 恢复只有在 Checkpoint、Task Brief、Trace 和状态卡全部写入成功后，才
    最后发布可 dispatch 的 State；任何向调用方报告的写入失败都不会留下隐藏 ready run。
14. 同步实施计划、状态权威 ADR、Roadmap、文档导航和 CI core-cli 分片。

## 已取得的本机证据

通过：

```text
Agent 状态、恢复、Task Card 与锁定向回归：58 passed
完整测试节点收集：1188 collected
Ruff：通过
compileall：通过
architecture growth：通过（C901 35->35，Python 模块 104->122）
repository hygiene --base-ref origin/main：通过
CI YAML 解析与 core-cli 分片：通过
git diff --check：通过
UTF-8 BOM 检查：通过
```

本机 Python 3.14 仍有 LangChain Core 的 Pydantic V1 兼容警告；项目 CI 使用 Python 3.11/3.12。
Agent 回归已取得明确终态：Contract 18、Recovery 20、Runtime 16、Task Card 4，共
`58 passed`；Recovery 最终拆成两个 10 节点分片，分别用时 23.46 秒与 23.21 秒。全仓
1188 个节点仍只有收集证据，没有可信的本机全量执行终态；此前合并分片被外层约 60 秒超时
中断，不能计为通过或失败。最终全量结论必须等待 PR CI，不能把定向测试或超时运行写成
全量通过。

## 回家后继续

```powershell
git fetch origin --prune
git switch experiment/supervisor-agent-v1
git pull --ff-only
git status --short --branch
Get-Content docs/SUPERVISOR-AGENT-V1-HANDOFF.md
```

建议顺序：

1. 确认本地 HEAD 与远端实验分支一致，Workspace 干净。
2. 查看或创建面向 `main` 的 Draft PR，让 PR CI 验证 Gate 0～2A。
3. CI 失败时只修复能够复现的失败，不降低 fail-closed、单 Writer 或 60 秒测试上限。
4. CI 通过后再做一次 diff 审查，重点检查 dispatch commit 安全闩、Writer binding 核销条件、
   损坏 Artifact 和 partial diff 的恢复路径。
5. 审查没有阻断项后，再决定 Gate 2B 的真实 Codex Adapter；不要在本次交接提交中继续实现。

## 下一 Gate 的边界

Gate 2B 只应连接一个真实 Codex Adapter，并复用当前：

- Plan 批准；
- Task Brief；
- 单 Writer 与 owned process；
- Observation / Decision；
- Workspace 对账；
- Verification、Risk、Reviewer 与 Finish。

Gate 2B 不新增多 Worker、Provider 平台、服务端、自动重试、自动 commit/push/release 或第二套
成功裁决。

## 未完成事项

- 尚未创建或验证 Draft PR CI。
- 尚未连接真实 Codex Worker。
- 尚未执行两个冻结真实案例。
- 尚未验证跨机器 Task Card 接力和 Claude Code 薄 Adapter；它们属于 Gate 3。
- 受信 Observation 已经 write-once；若其后的 Checkpoint 写入失败，State 会保守保留 active
  Writer，但重试需要新的 Observation ID。该路径不会开放第二 Writer，后续是否需要事务化
  Observation/Decision/Graph 由 Gate 2B 真实 Adapter 故障注入决定。
