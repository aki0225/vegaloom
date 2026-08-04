# Vega Plan-first 与修改前确认协议

> 状态：Phase 2 已完成
>
> 适用入口：Codex、Claude Code 等宿主会话，以及边界明确时的 `vega do`

## 1. 目标

当用户只描述 bug 现象或模糊需求时，宿主会话不能直接把猜测交给 Worker。默认顺序为：

```text
只读调查 -> 固定 Plan -> 人工确认 -> assist 实现 -> Vega 判断链 -> Finish
```

这是一项宿主使用协议，不是新的 Planner Agent、Runtime 路由、命令、状态或 artifact schema。
Vega 不在 Runtime 中使用模型判断任务是否模糊。

## 2. 什么时候可以直接执行

只有以下条件同时成立，才可以省略重复调查并直接使用 `vega do` 或启动 assist：

1. 用户已经描述明确的目标行为或缺陷；
2. 允许修改的文件或模块范围明确；
3. 验收标准和验证方式明确；
4. 高风险与非目标没有未决冲突；
5. 用户明确要求直接执行。

缺少任一项时，先执行只读调查。不要用“任务看起来很小”代替这些条件。

## 3. 只读调查边界

修改前允许：

- 读取用户任务、`AGENTS.md`、`.vega.yaml`、源码、测试和相关历史；
- 运行 `git status`、`git diff`、搜索、配置检查和其他不会修改工作区的查询；
- 记录已经确认的事实、仍待验证的假设和需要用户决定的问题。

修改前禁止：

- 修改、创建、删除或格式化目标仓库文件；
- 启动 Worker 或提前运行 `vega loop`；
- 把推测写成事实；
- 扩大用户授权的读取、修改或验证范围；
- 因为生成了 Plan 就视为已经得到批准。

## 4. 固定 Plan 模板

宿主会话必须使用以下标题，并保持事实和假设分离：

```markdown
## User Goal

用户希望最终得到什么结果。

## Non-goals

本轮明确不做什么。

## Observed Facts

- 已由文件、代码或命令确认的事实，并注明来源。

## Hypotheses

- 尚未确认的根因或实现判断，以及如何验证。

## Proposed Scope

- 允许读取的范围。
- 允许修改的范围。
- 明确禁止触碰的范围。

## Verification

- 必须运行的测试、静态检查和人工检查。

## Risk Areas

- 支付、数据库、并发、权限、敏感数据及其他命中风险。

## Unresolved Decisions

- 进入修改前仍需要用户决定的问题；没有则写“无”。
```

权威顺序固定为：

```text
用户任务、AGENTS.md、.vega.yaml
  > 已观察到的代码与运行事实
  > 根因假设和修改建议
```

## 5. 人工确认

Plan 完成后先交给用户。只有以下任一条件成立才进入修改：

- 用户明确批准 Plan；
- 用户一开始已经提供精确范围、验收标准，并明确要求直接执行。

批准只覆盖 Plan 中写明的范围。调查后出现新的高风险、依赖、跨层修改或范围扩大时，先更新
Plan 并再次确认。

## 6. Codex 使用方式

仓库运行：

```powershell
vega adapters init codex --repo .
```

生成的 `$vega-loop` Skill 会执行本协议。批准后：

```powershell
vega loop bug --repo . --text "<用户需求>" --mode assist
vega status --run <loop_run_id>
```

当前 Codex 主会话或宿主原生子代理按 `worker-prompt.md` 完成最小修改，然后：

```powershell
vega loop continue --repo . --run <loop_run_id>
vega finish --run <loop_run_id>
```

边界已经明确且用户要求直接执行的小任务，可以使用：

```powershell
vega do feature --repo . --text "<用户需求>"
```

## 7. Claude Code 使用方式

Claude Code 不需要新的 Vega Runtime 或原生 Runner。普通 Claude Code 主会话按本协议：

1. 先只读调查并输出同一份固定 Plan；
2. 等待用户明确批准；
3. 运行 `vega loop ... --mode assist`；
4. 按 `worker-prompt.md` 修改工作区；
5. 运行 `vega loop continue` 和 `vega finish`。

Claude Code 可以作为外部 Worker，但同一会话不能同时充当独立 Reviewer。Reviewer 必须使用
项目配置的独立只读 runner，或读取 `review-pack` 的全新只读会话；不能接收 Worker 的完整
聊天记录或中间推理。

本阶段不增加 `vega adapters init claude`，也不实现 Claude Code 原生自动 Runner。

## 8. `vega plan` 的边界

`vega plan` 用于大目标的 scope、预算和 phase 规划。它不调查根因，也不自动批准实现，因此
不能替代本协议的 `Observed Facts`、`Hypotheses` 和修改前人工确认。

## 9. 停止线

Phase 2 只固定宿主协议和生成的 Codex Skill：

- 不新增 Planner Agent；
- 不新增 CLI、状态或 schema；
- 不修改 Loop、Gate、Reviewer、Finish 或成功语义；
- 不增加 Claude Code 原生 Runner；
- 不把 Plan 当成自动审批。
