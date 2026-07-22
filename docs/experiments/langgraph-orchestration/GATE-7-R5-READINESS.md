# Gate 7 R5 API-key 执行就绪记录

> 状态：`terminal-failed-before-worker`
>
> 日期：`2026-07-20`
>
> 时区：`Asia/Shanghai`

## 当前结论

R5 已完成 API-key 认证合同、独立身份命名空间和 fake 双臂验证，但真实 Gate 7A 在
worker 启动前 terminal 失败。R5 consumed tag 已成功推送，因此 R5 不得重试；没有调用
真实 Provider，Gate 7C 不允许启动。完整结果见
`GATE-7-R5-RESULT.md`。

R4 保持原样：

```text
gate-7a-pre-run-r4-v1 -> private-gate-7-r4-test-closure-redacted
gate-7c-langgraph-pre-run-r4-v1 -> private-gate-7-r4-test-closure-redacted
R4 consumed tags = absent
R4 real sessions = 0
```

## 已验证身份

```text
case SHA-256 = 6b3541059cc6a2a8375424d303cb5a48b79b4b305d3dc6599f3b42b72330eaae
plan SHA-256 = cbda7c69e26370a05e44b4cd7691e386992befdb1f38af72e7d85892b754dba0
auth mode = api-key
Codex CLI = 0.144.5
graph schema = gate7-r4-v1
transcript parser = gate7-r4-v1
```

R5 fake linear v2 与 fake LangGraph v2 均为 `success`，并得到：

```text
CP01 prompt SHA = 4df182033096692bba758ca824a1d54042380ccfa202c9d424138f3e673e9fb3
CP02 prompt SHA = 51ee8cd22813b048b04794ae37d02f7964fd5f9204a79281f774ea9da02d96a8
CP03 prompt SHA = b2ae159146e5298edfe4e02c23715758640b00ab5124a0f7541dd7d32b9dd705
final tree = a5b249e710d1253bee4c099faf91e45f9ebfbddd
canonical diff bytes = 19266
canonical diff SHA-256 =
d8e20d91ebe30ca5056be1b3e4d84d989dbba6fd2a16829baecf0620bdc4d33b
```

双臂共同满足：

```text
automatic retries = 0
planned migrations = 1
Machine F target external attempts = 1
scope violations = 0
duplicate external effects = 0
canary leaks = 0
sensitive material hits = 0
```

## 本轮代码边界

R5 没有改动 Vega 产品 Runtime、LangGraph Engine、Flask 任务或 worker prompt。

代码变更仅包括：

1. `Gate7ExperimentSpec` 显式绑定 `chatgpt` / `api-key` 认证模式；
2. real preflight 按 case 检查认证类型；
3. summary 记录认证模式和 Codex CLI 版本；
4. Gate 7C 对显式认证 case 复验上述身份；
5. Windows 控制子进程输出固定按 UTF-8 失败安全解码；
6. 新增 R5 case、launcher 和自动化测试。

第一次 fake linear v1 已成功写出完整 summary，但控制进程退出时出现 Windows 默认 GBK
对 UTF-8 子进程输出的异步解码异常。该次证据不作为最终 freeze 依据。修复后重新运行
linear v2 与 LangGraph v2，均无解码异常并明确成功。

## 测试策略

R4 在同一天已经完成 `838/838 passed` 的全量闭环。R5 没有修改 `src/` 产品代码，只修改
Gate 7 实验 harness、case、launcher 和对应测试，因此采用：

- 继承 R4 `838/838` 产品与 LangGraph Runtime 基线；
- 完整运行受影响的 Gate 7 测试文件：`45 passed`；
- 单独验证 R5 hash、API-key 识别和 UTF-8 子进程解码；
- 运行 compileall、Ruff 与 `git diff --check`；
- 重新运行 fake linear / LangGraph 双臂。

不得把继承的 R4 全量结果写成“R5 又重新运行了 838 个测试”。

本轮确定性验证结果：

```text
tests/experimental/langgraph_engine/test_gate7_large_task_dogfood.py
= 45 passed

python -m compileall src
= passed

ruff check src tests scripts/gate7_large_task_dogfood.py
  scripts/gate7_large_task_dogfood_r5.py
= passed

git diff --check
= passed
```

## R5 终态证据

```text
Gate 7A status = failed
failure boundary = consumed tag 推送后、worker 启动前
consumed tag = gate-7a-consumed-r5-v1
consumed tag local/remote peel =
private-gate-7-r5-auth-contract-redacted
remote peel verification = post-stop manual verification passed
provider sessions started = 0
worker process started = false
checkpoint_started = 0
checkpoint_completed = 0
event chain = passed (arm_started -> authority_claimed)
Gate 7C = blocked
```

失败原因是 `_claim_real_execution` 在成功推送 consumed tag 后，用 30 秒的
`git ls-remote` 做远端 peel 复核并超时。该失败不是 Provider、模型、worker 或任务语义
失败；R6 将只修复这个控制面超时/异常收口，并使用全新 tag 命名空间。
