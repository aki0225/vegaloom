# Gate 7 R3 真实执行结果

> 状态：`failed`
>
> 日期：`2026-07-19`
>
> 分支：`experiment/langgraph-comparison`
>
> baseline commit：`private-gate-7-r3-baseline-redacted`
>
> 真实 provider session：`1`

## 1. 结论

Gate 7A R3 已真实启动，但 CP01 的唯一真实 worker session 在流式响应阶段断开，
因此 CP01 未完成，Gate 7C 不允许启动。

```text
Gate 7A R3 = failed
Gate 7C R3 = not-triggered
最终结论 = failed
```

R3 已创建并推送 `gate-7a-consumed-r3-v1`，不能删除 consumed tag、重跑 R3、
换 provider、换模型、换 reasoning 或复用 session。

## 2. 冻结身份

```text
baseline commit = private-gate-7-r3-baseline-redacted
case = eval/gate-7/flask-teardown-case-r3.json
case SHA-256 = e244483bb294b2d99cf934f8619729808763c791b5e0b7b6c4ce83bbbd4c5e81
plan SHA-256 = 5c6ae968bfd0378c8eb0643aea16e0e3956c708d9a15c2df805436788abbe2ab
A pre-run = gate-7a-pre-run-r3-v1
A consumed = gate-7a-consumed-r3-v1
C pre-run = gate-7c-langgraph-pre-run-r3-v1
C consumed = not created
A session = gate7a-flask-5928-real-r3-v1
C session = gate7c-flask-5928-real-r3-v1
```

两个 pre-run tag 和 A consumed tag 都是 annotated tag，并 peel 到同一个 baseline commit。
远端实验分支 HEAD 也为该 commit。

## 3. 真实轨迹

```text
arm_started
-> authority_claimed
-> CP01 checkpoint_started
-> CP01 checkpoint_failed
-> terminal failed
```

CP02、CP03、planned migration、Machine F、final tree/diff 和 artifact scan 均未发生。

真实身份：

```text
Codex CLI = 0.144.5
provider = sandboxproxy
model = sandbox-model
reasoning = high
graph schema = gate7-r3-v1
request_max_retries = 0
stream_max_retries = 0
multi_agent = disabled
worker returncode = 1
tokens used = 67,804
```

## 4. R3 修复是否生效

生效。

- worker 首个 `git status` 成功；
- process output 中 `dubious ownership` 命中数为 `0`；
- 项目内 `worker-gitconfig` 只包含当前 fixture repo 的一个 `safe.directory`；
- 通过同一 `GIT_CONFIG_GLOBAL` 重新执行 `git status` 返回 `0`；
- 失败后 fixture repo 仍停在 base commit
  `7b0088693ece1bd3a9238a6fdf56ed8df7a4d43b`；
- 未暂存、已暂存和未跟踪改动均为空。

因此 R2 的 control clone 所有权问题已经被本轮修复，不是 R3 的失败原因。

## 5. 失败根因

CP01 worker 已完成多轮只读代码检查，但在生成完成响应前出现：

```text
stream disconnected before completion:
Transport error: network error: error decoding response body
```

process output 中该终止信息出现两行，`502 Bad Gateway` 命中数为 `0`。本轮只有一个
Codex worker session，event ledger 中没有 retry event，不能把两行终止信息解释成第二个
provider session。

这说明 R3 已越过 R2 的 Git 身份阻断，也越过了最初的上游连接建立阶段，但当前
Responses 流式传输仍不能稳定承载该 CP01 长会话。

## 6. 已证明与未证明

已证明：

- 项目内临时 `safe.directory` 可以安全传入 owned Codex subprocess；
- 不需要修改用户全局 Git 配置、ACL 或系统权限；
- R3 case、plan、baseline、tag 和 session 身份可独立冻结；
- fake linear 与 fake LangGraph 双臂仍能完成三 checkpoint；
- provider 能启动真实 session 并返回大量流式内容；
- consumed 后失败会进入 terminal state，且不会自动启动 CP02 或 Gate 7C。

未证明：

- 真实 provider 下 CP01 能完成；
- 真实三 checkpoint 接力；
- Gate 7A 大任务协议成功；
- LangGraph 真实恢复成本或增量价值；
- 真实物理换机。

## 7. 下一步边界

不能继续 R3。若继续实验，必须创建 R4 新 baseline、case/session/tag 命名空间，并先解决
长流式响应的稳定性或有界输出问题。R4 不能复用本轮 `67,804` tokens 的半成品输出，
也不能把本轮 Git 修复成功与大任务执行成功混为一个结论。
