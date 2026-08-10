# Goal P1 Worker 重跑交接

> 日期：2026-08-10
> 分支：`codex/goal-p1-real-dogfood`
> 主线基线：`origin/main@28c2ddb`
> 状态：`implemented-awaiting-pr-ci`

## 当前结论

本分支已经实现 2026-08-09 审阅列出的七个合并阻断项，并补齐 Worker 重跑在 baseline
准备、iteration claim、`worker_started` 三个边界上的崩溃恢复。当前适合继续推送实验分支并由
PR CI 验证，但本次交接不直接合并到 `main`。

能力边界没有扩大：`--rerun-worker` 仍需人工显式触发，不改变默认 `vega do`，也没有增加
daemon、数据库、多 checkpoint、自动重试或新的 Runtime。

## 已完成修改

1. Worker baseline V2 只保存路径集合数量与摘要，不把 tracked、untracked、ignored 原始路径写入
   artifact。
2. baseline 与当前快照都绑定 `assume-unchanged`、`skip-worktree` 等 Git index 标记；存在不安全
   标记时拒绝重跑。
3. ignored 目录使用既有预算生成有界后代清单；内容变化、增加、删除或预算不足均 fail-closed。
4. 恢复规划在启动 Worker 前验证来源 baseline artifact、SHA-256 与唯一 trace 绑定。
5. 完整性检查从 iteration 生命周期推导是否必须存在重跑授权，能够发现 state/trace 双删、重复
   和事件顺序错误。
6. 最终 Worker 启动边界重新捕获工作区，偏离授权快照时不调用可写 runner。
7. 新增小型重跑事务：baseline 在 claim 前幂等准备；baseline 与 `running/current_iteration` 在同一
   state 保存中绑定；事务保留到唯一 `worker_started` 证据写入。
8. claim 已持久化但 Worker 尚未启动时，Recovery 恢复为 `needs_human/recovered`；同一 recovery、
   iteration 与事务可以继续，不会启动两个 Worker。
9. 为通过架构增长门禁，将内容清单、tracked workspace、baseline、重跑规划、重跑事务、失败报告
   和 recovery 报告拆成职责单一模块，没有保留兼容性死代码。
10. `worker_started` 已写入但重跑事务文件因 Windows 文件锁等原因无法删除时，Runtime 会在调用
    可写 runner 前抛错并保留事务；锁仍存在时 Recovery 同样拒绝伪装成功，锁释放后再次 Recovery
    才按已启动边界转为 `needs_human`，不会继续执行 Worker 或丢失恢复依据。

## 关键验证证据

通过：

```text
compileall：通过
Ruff：通过
architecture growth：通过（C901 36->35，Python 模块 94->104）
repository hygiene --base-ref origin/main：通过
git diff --check：通过

Worker 重跑三个新增崩溃边界：3 passed
事务删除失败及 started-boundary Recovery 定向回归：2 passed
recovery chaos：45 个节点分成十个独立进程，6 + 6 + 5 + 5 + 5 + 5 + 4 + 4 + 4 + 1 全部通过
workspace snapshot budget：30 passed
workspace manifest containment：11 passed
config-assurance-pilot：272 passed
runtime safety integration：32 passed, 1 skipped
execution control safety：61 passed, 1 skipped
security evidence：42 passed
workspace baseline：18 passed
context boundaries：38 passed
P0 regressions：55 passed
CLI recovery hardening：50 passed, 1 skipped
```

本机未取得可信的单进程全量终态。1129 节点单进程运行到约 13% 时，在与本次改动无关的
Assurance 用例中等待 Git 子进程超过 60 秒；多个 Git 密集型大文件也会在累计运行后出现同类
超时。精确旧 HEAD 的 detached worktree 运行同一 Finish 节点也超过 60 秒，说明至少该超时
不是本分支独有回归。临时 worktree 已删除。

另有一个 Windows 时间敏感节点
`test_verification_timeout_terminates_owned_descendants` 实测约 6.09 秒，未满足既有 `< 4.0s`
断言；本分支没有修改 execution control 或 verification timeout 实现。该结果仍应视为未通过，
不能包装成环境绿灯，最终以 PR 的 Linux/Windows CI 为准。

## 本轮发现并修复的重构测试问题

1. tracked diff helper 移动后，Git 调用预算测试看不到两次 diff 调用。helper 现在支持显式传入
   Git 调用器，保留原有可观测性；对应测试通过。
2. 重跑规划函数移动后，三个故障注入测试仍只 patch 旧模块，导致突变没有发生。测试已同时
   patch 恢复评估与重跑规划模块；三个节点通过。
3. 内容清单 helper 移动后，containment 测试仍引用旧私有位置。测试已指向新模块；11 个节点
   通过。
4. PR #55 首轮 CI 发现 scope-gate 故障注入仍 patch 旧的 `workspace_check` 模块，导致突变没有
   发生。测试现已 patch 实际承载快照逻辑的 `tracked_workspace` 模块；P0 regression 55 个节点
   全部通过。
5. PR #55 首轮 CI 使用的 Typer/Rich 版本会在参数错误输出中插入 ANSI 控制码。新增冲突测试
   现在只在断言前去除 ANSI，不改变 CLI 输出或生产行为；CLI recovery hardening 为 50 passed、
   1 skipped，并在强制颜色环境下复核相关节点。

## 回家后继续

```powershell
git switch codex/goal-p1-real-dogfood
git pull --ff-only
git status --short --branch
```

建议顺序：

1. 先查看本交接文件和最新提交 diff，不再重做七项缺口调查。
2. 查看远端分支 CI 或创建面向 `main` 的 PR；不要直接合并。
3. 若 CI 有失败，只处理可复现失败；Windows 本机 Git 子进程累计超时单独记录，不放宽
   fail-closed 语义或测试断言来换取绿色。
4. CI 全绿后再做一次独立代码审查，确认事务、恢复和最终启动边界没有新的双 Worker 路径。
5. 审查通过后使用 squash merge，并删除远端实验分支。

## 明确不做

- 不新增后台常驻调度器。
- 不自动重跑 Worker。
- 不串联多个 checkpoint。
- 不把 read-only 会话宣传为系统级安全沙箱。
- 不因本机长测试超时而降低证据完整性、成功语义或 60 秒单测上限。
