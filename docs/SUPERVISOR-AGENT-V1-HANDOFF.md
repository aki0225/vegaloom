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
2. 将 Writer 状态拆成 `worker_reserved` 与 `worker_started`，区分“已登记 Writer”和
   “operation 已经开始”。
3. Worker 仍存活或 execution/Trace 证据损坏时保留原 child/operation binding，禁止第二 Writer。
4. 只有 operation 未开始、Workspace 未变、控制信息完整且没有外部副作用时，才恢复到
   `ready`；后续仍需人工显式 dispatch 新 child。
5. partial diff、数据库/支付/部署/外部 API 副作用、损坏状态、未知 schema 和不完整现场均
   fail-closed 进入人工处理。
6. SQLite Graph checkpoint 丢失时仍从 Agent State、Checkpoint 与真实 Workspace 对账。
7. 增加 `pause`、`resume-local`、`stop` 和结构化 `recover` 命令；不执行自动回滚、提交或推送。
8. 同步实施计划、状态权威 ADR、Roadmap、文档导航和 CI core-cli 分片。

## 已取得的本机证据

通过：

```text
Agent 状态、恢复、Task Card 与锁定向回归：49 passed
完整测试节点收集：1169 collected
Ruff：通过
compileall：通过
architecture growth：通过（C901 35->35，Python 模块 104->122）
repository hygiene --base-ref origin/main：通过
CI YAML 解析与 core-cli 分片：通过
git diff --check：通过
UTF-8 BOM 检查：通过
```

本机 Python 3.14 仍有 LangChain Core 的 Pydantic V1 兼容警告；项目 CI 使用 Python 3.11/3.12。
本分支尚未取得可信的本机全量测试终态，因此必须等待 PR CI，不能把定向测试写成全量通过。

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
4. CI 通过后再做一次 diff 审查，重点检查 Writer binding、operation started、损坏 Artifact 和
   partial diff 的恢复路径。
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
