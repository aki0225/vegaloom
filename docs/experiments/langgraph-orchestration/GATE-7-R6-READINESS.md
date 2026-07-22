# Gate 7 R6 API-key 执行就绪记录

> 状态：`terminal-failed-at-cp01-transcript-budget`
>
> 日期：`2026-07-20`
>
> 时区：`Asia/Shanghai`

## R5 终态不会被覆盖

```text
R5 status = failed-before-worker-no-provider-session
R5 consumed = gate-7a-consumed-r5-v1
R5 Gate 7C = blocked
```

R6 使用新的 case、session 和 tag 命名空间；R4/R5 证据保持只读。

## 当前结论

R6 已于 `2026-07-20（星期一）`完成唯一一次真实 Gate 7A。远端 consumed tag 控制、
API-key、Provider 和 CP01 worker 均成功，但 transcript 与 token 超过冻结上限，结果为：

```text
Gate 7A = failed-at-cp01-transcript-budget
Gate 7C = not started
R6 retry = forbidden
```

完整证据见 [`GATE-7-R6-RESULT.md`](GATE-7-R6-RESULT.md)。

## R6 固定身份

```text
case SHA-256 = b8475f796f9ec8bac1c51eee9f1d30975e00c5b4e70859933f158663867b3f8d
plan SHA-256 = c0e372e5c56d6a322882ff147cee0e7e890bc4f9d20654fd18bb074f34ee8ddf
auth mode = api-key
Codex CLI = 0.144.5
remote Git verification timeout = 120 seconds
```

## 已验证

- R6 case/plan hash 已重新计算并绑定 launcher；
- API-key 认证识别测试通过；
- remote tag 读取、首次 consumed tag 预检与 push 超时 fail-closed 测试通过；
- Machine E/F 子控制进程同时保留脱敏 `stdout`/`stderr` 尾部，失败 traceback 不再静默
  丢失；
- Gate 7 受影响测试文件共 `50 passed`；
- `gate7-r6-fake-linear-v2` 与 `gate7-r6-fake-langgraph-v1` 均为 `success`；
- fake 双臂的 case、plan、三个 prompt、三个 checkpoint tree、最终 tree 和 canonical
  diff 全部一致；
- fake 双臂自动重试、重复外部副作用、scope violation、canary leak、敏感材料命中和
  Provider session 均为 `0`；
- R6 两个 baseline annotated tag 已创建并推送；
- Gate 7A consumed tag 已创建并推送，local/remote peel 均为
  `private-gate-7-r6-baseline-redacted`；
- Gate 7A 使用了 `1` 个真实 Provider session，worker 启动 `1` 次；
- Gate 7C consumed tag 不存在，Provider session 为 `0`；
- R6 按预注册合同 terminal，不得重跑。

## Fake 双臂证据

```text
linear session = gate7-r6-fake-linear-v2
LangGraph session = gate7-r6-fake-langgraph-v1

case SHA-256 =
b8475f796f9ec8bac1c51eee9f1d30975e00c5b4e70859933f158663867b3f8d
plan SHA-256 =
c0e372e5c56d6a322882ff147cee0e7e890bc4f9d20654fd18bb074f34ee8ddf
final tree = a5b249e710d1253bee4c099faf91e45f9ebfbddd
canonical diff bytes = 19266
canonical diff SHA-256 =
d8e20d91ebe30ca5056be1b3e4d84d989dbba6fd2a16829baecf0620bdc4d33b

linear summary SHA-256 =
7b59765b9665a6de9cd284d209815aa01d945c49bb427668d6c37fe33eec2f34
LangGraph summary SHA-256 =
416c7094a7eb32bcaccb5ec8688406c1cc489059c972ef143f4f8ac4f1eb9a5a
```

LangGraph handoff 恢复从 `cp02_completed` 恢复到 `cp03_completed`，checkpoint count
从 `4` 增加到 `5`，`resume_external_attempts=0`、
`replayed_external_attempts=0`、`target_external_attempts=1`。

第一次 fake linear session `gate7-r6-fake-linear-v1` 不作为通过证据。它在 CP03 已提交
并完成 `1 passed` 定向验证和 `495 passed` 全量验证后，Machine F 返回 1；旧父进程只
读取 `stdout`，无法取得真实 traceback。该失败证据保持不可变。补充 `stderr` 后的新
session v2 成功，因此 readiness 只冻结 v2。

## R6 准入完成记录

1. compileall、Ruff、`git diff --check`：`passed`；
2. R5 结果与 R6 实现/合同提交：`private-gate-7-r6-baseline-redacted`；
3. 短路径 checkout 与远端分支：`clean and aligned`；
4. `codex 0.144.5`、API-key auth、loopback Provider：`passed`；
5. 两个 R6 baseline annotated tag：`pushed and peeled to private-gate-7-r6-baseline-redacted`；
6. 唯一一次真实 Gate 7A：`terminal failed`；
7. Gate 7C：`blocked by Gate 7A result`。

R6 真实结果已经记录：

```text
consumed tag push result
remote peel verification result
provider session count
worker start count
checkpoint order
transcript/audit hash chain
handoff/final identity
scope/DLP/canary/token evidence
```
