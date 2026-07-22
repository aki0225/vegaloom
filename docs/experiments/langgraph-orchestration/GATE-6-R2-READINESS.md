# Gate 6 R2 Handoff 真实执行 Readiness

> 状态：`ready-for-r2-baseline-freeze / provider not started`
>
> 日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> R1 provider calls：`0`
>
> R2 provider calls：`0`

---

## 1. Readiness 结论

R1 的 consumed baseline 保持不变。R2 只修复了 Windows Codex launcher 的 executable
resolution 和版本字符串归一化，并已完成本地回归、Gate 6 deterministic 测试和 fake
dogfood，可以进入新的 execution baseline 提交阶段。

```text
R1 baseline = gate-6-pre-run-v1 / consumed
R1 result = blocked before provider call
R2 launcher fix = complete
R2 deterministic tests = pass
R2 fake readiness = pass
R2 real provider calls = 0
```

---

## 2. R2 变更边界

```text
scripts/gate6_handoff_dogfood.py
  - baseline tag = gate-6-r2-pre-run-v1
  - consumed tag = gate-6-r2-consumed-v1
  - real runner uses resolved codex executable
  - codex-cli version output normalizes to 0.144.5
  - preflight OSError fails closed as Gate6Blocked

tests/experimental/langgraph_engine/test_gate6_handoff_dogfood.py
  - Git guard regressions = 2
  - Windows wrapper/version/auth regressions = 3
```

冻结 synthetic fixture 未改变：

```text
fixture = eval/gate-6/handoff-case.json
fixture SHA-256 =
84bbdadb73eb85a088c597f9fafe76e525729a99e7007d861b6a3236921e7270
```

---

## 3. 确定性验证

```text
tests/experimental/langgraph_engine/test_checkpoint_handoff.py = 19 passed
tests/experimental/langgraph_engine/test_goal_cross_session.py = 3 passed
tests/experimental/langgraph_engine/test_gate6_handoff_dogfood.py = 5 passed
Gate 6 R2 related total = 27 passed

python -m compileall -q src scripts/gate6_handoff_dogfood.py = pass
ruff check Gate 6 source/tests/scripts = pass
git diff --check = pass
```

本轮还复用了此前已取得明确终态的兼容回归：

```text
goal smoke = 19 passed
evidence freshness = 20 passed
```

---

## 4. Fake Readiness

```text
command =
python scripts/gate6_handoff_dogfood.py `
  --runner fake `
  --session gate6-r2-fake-contract-001 `
  --output-root .local-validation/gate-6-r2 `
  --fixture-root .tmp/gate-6-r2

evidence =
.local-validation/gate-6-r2/gate6-r2-fake-contract-001

decision = fake-passed
phase = completed
runner invocations = 3
provider sessions = 0
provider hard limit = 3
handoff version = v0001
handoff SHA-256 =
cdf3eca522691873c43b93ddae08d252df8c11ad7b330fc1e6f02afaa50bfc56
context status = ready
context SHA-256 =
a0d2c073b2d6dc48734417f25269eb14345ba0b94d5e00090e158c80bea2fecf
Session A/B distinct = true
source chat included = false
accepted memory writes = 0
automatic retries = 0
failures = []
```

fake summary/report hash：

```text
summary.json =
228a3d6c5e7be4006821138cc5fc413f8fe23472dabd9a9ca49462e6da0a75c0

report.md =
06cf3489a8b2b51d1ca12d3f4ea3ca694670613db401ec11dfcb6bcad280fc3e
```

---

## 5. 本地 Codex 启动探针

R2 real runner 在不发送 provider prompt 的前提下完成了本地启动前探针：

```text
resolved executable = <codex-wrapper>
codex version = 0.144.5
auth mode = chatgpt
```

该探针不计入 Gate 6 provider session budget；正式 provider 调用仍必须从 R2 clean
checkout 的一次 real harness 执行开始。

---

## 6. R2 执行顺序

真实调用前必须按以下顺序执行：

1. 显式暂存 R2 文件，排除用户未跟踪的 `uv.lock`；
2. 提交新的 R2 execution baseline；
3. 创建 annotated tag `gate-6-r2-pre-run-v1`；
4. 推送 R2 branch 和 baseline tag；
5. 从该 tag 建立 `core.autocrlf=false` 的 LF clean checkout；
6. 验证 R1 consumed tag 与 R2 baseline/tag 均不可变；
7. 只执行一次：

```text
python scripts/gate6_handoff_dogfood.py --runner real --confirm-real `
  --session gate6-r2-real-v1 `
  --output-root .local-validation/gate-6-r2 `
  --fixture-root .tmp/gate-6-r2
```

任一 preflight、identity、DLP、execution artifact、token、timeout 或 termination 失败，
立即固化 `blocked` 或 `fail`，不得重试同一 R2 baseline。

---

## 7. 停止线

- R1 `gate-6-pre-run-v1` 和 `gate-6-consumed-v1` 不可复用。
- R2 `gate-6-r2-pre-run-v1` 在提交前不存在，R2 consumed tag 在 real harness 启动前不存在。
- 不放宽 sandbox，不切换 provider/model/auth，不读取 credential store。
- 不把 fake 通过升级为 real 通过。
- R2 real 结果必须生成独立的 `GATE-6-R2-RESULT.md`，再更新顶层 Extended Decision。
