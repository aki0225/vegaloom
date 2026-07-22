# Gate 6 R4 Handoff 真实执行 Readiness

> 状态：`ready-for-r4-baseline-freeze / provider not started`
>
> 日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`

---

## 1. Readiness 结论

R1、R2、R3 现场均已封存。R4 只增加 known verification cache allowlist，未知 ignored
写入仍然 fail-closed。

```text
R1 consumed = present
R2 consumed = present
R3 consumed = present
R4 cache guard fix = complete
R4 deterministic tests = pass
R4 fake readiness = pass
R4 real provider calls = 0
```

---

## 2. 验证结果

```text
tests/experimental/langgraph_engine/test_checkpoint_handoff.py = 19 passed
tests/experimental/langgraph_engine/test_goal_cross_session.py = 3 passed
tests/experimental/langgraph_engine/test_gate6_handoff_dogfood.py = 7 passed
Gate 6 R4 related total = 29 passed

compileall = pass
ruff = pass
git diff --check = pass
```

R4 fake：

```text
command =
python scripts/gate6_handoff_dogfood.py --runner fake `
  --session gate6-r4-fake-contract-001 `
  --output-root .local-validation/gate-6-r4 `
  --fixture-root .tmp/gate-6-r4

evidence =
.local-validation/gate-6-r4/gate6-r4-fake-contract-001

decision = fake-passed
phase = completed
runner invocations = 3
provider sessions = 0
handoff version = v0001
handoff SHA-256 =
3ae9adf411d90c33110bbf5e271c64d2abcf32cc9fbc2e9139213ce153bd3c9c
context status = ready
context SHA-256 =
788f60df92f7b1d32ffdf6649fca81d16ea658b010682d1a5cf33047014d51e5
Session A/B distinct = true
source chat included = false
accepted memory writes = 0
failures = []
```

fake hash：

```text
summary.json =
7e07c31e26ce7bad76d58416399c3a69d0c404bf830775473c9b34bba454d26a

report.md =
d0e58d0ec8214bf5f200baa1e6af3d1f7de0df6134dc53861855438effbee2a2
```

---

## 3. R4 baseline

```text
baseline commit = pending explicit commit
baseline tag = gate-6-r4-pre-run-v1
consumed tag = gate-6-r4-consumed-v1
```

R4 baseline 提交前必须显式暂存 R4 代码、测试和结果/预注册/readiness 文档，排除用户已有
未跟踪的 `uv.lock`。

---

## 4. 唯一执行前检查

从 R4 tag 建立新的 LF clean checkout 后确认：

- R1/R2/R3 consumed tag 均存在；
- R4 consumed tag 尚不存在；
- `HEAD == gate-6-r4-pre-run-v1^{commit}`；
- `core.autocrlf=false`、`core.eol=lf`；
- 工作树 clean；
- resolved executable、Codex version、auth mode 正确；
- provider tunnel 仍在运行；
- 不读取 credential store，不切换配置。

真实流程只执行一次。若 R4 成功完成 Session A/B，才进入最终 Gate 6 结果和决策文档；
若仍 blocked/fail，则保留 R4 全部证据并进入下一次新 baseline。
