# Gate 6 R3 Handoff 真实执行 Readiness

> 状态：`ready-for-r3-baseline-freeze / provider not started`
>
> 日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`

---

## 1. Readiness

R1 和 R2 现场均已封存。R3 只包含 synthetic fixture workdir 的 DLP 白名单修复和回归
测试，未改变 Goal / Checkpoint / Handoff 业务合同。

```text
R1 consumed = present
R2 consumed = present
R3 launcher/DLP fix = complete
R3 deterministic tests = pass
R3 fake readiness = pass
R3 real provider calls = 0
```

---

## 2. 验证结果

```text
tests/experimental/langgraph_engine/test_checkpoint_handoff.py = 19 passed
tests/experimental/langgraph_engine/test_goal_cross_session.py = 3 passed
tests/experimental/langgraph_engine/test_gate6_handoff_dogfood.py = 6 passed
Gate 6 R3 related total = 28 passed

compileall = pass
ruff = pass
git diff --check = pass
```

R3 fake：

```text
command =
python scripts/gate6_handoff_dogfood.py --runner fake `
  --session gate6-r3-fake-contract-001 `
  --output-root .local-validation/gate-6-r3 `
  --fixture-root .tmp/gate-6-r3

evidence =
.local-validation/gate-6-r3/gate6-r3-fake-contract-001

decision = fake-passed
phase = completed
runner invocations = 3
provider sessions = 0
handoff version = v0001
handoff SHA-256 =
4ca1c7f362183d5cdc5a11957cd0d2f22191f38c0a5acff4a82da21793f32405
context status = ready
context SHA-256 =
d4d53dbee4fe0deb2679f8c0705d229c264fcb48623862b31a49d820540f45de
Session A/B distinct = true
source chat included = false
accepted memory writes = 0
failures = []
```

fake hash：

```text
summary.json =
0acacd23230e5fe44078d0262adf9e4dec90df9beb27c994da541ca32421d880

report.md =
cdb08f22a51e54ba718496a92e4476f07df006894224d1f5c3d787b280fee6b1
```

`report.md` 的 hash 在 raw evidence 中为连续十六进制字符串。

---

## 3. R3 baseline

```text
baseline commit = pending explicit commit
baseline tag = gate-6-r3-pre-run-v1
consumed tag = gate-6-r3-consumed-v1
```

R3 baseline 提交前必须显式暂存以下文件，排除用户已有的 `uv.lock`：

```text
scripts/gate6_handoff_dogfood.py
tests/experimental/langgraph_engine/test_gate6_handoff_dogfood.py
docs/experiments/langgraph-orchestration/GATE-6-R2-RESULT.md
docs/experiments/langgraph-orchestration/GATE-6-R3-PRE-REGISTRATION.md
docs/experiments/langgraph-orchestration/GATE-6-R3-READINESS.md
```

---

## 4. 唯一执行前检查

从 R3 tag 建立新的 LF clean checkout 后，必须确认：

- R1/R2 consumed tag 仍存在；
- R3 consumed tag 尚不存在；
- `HEAD == gate-6-r3-pre-run-v1^{commit}`；
- `core.autocrlf=false`、`core.eol=lf`；
- 工作树 clean；
- resolved executable、Codex version、auth mode 正确；
- provider tunnel 仍由外部进程维护；
- 不读取 credential store，不切换配置。

真实流程只允许执行一次。R3 若在 provider 启动后 blocked/fail，保留全部 token 和
execution evidence，不得重试同一 baseline。
