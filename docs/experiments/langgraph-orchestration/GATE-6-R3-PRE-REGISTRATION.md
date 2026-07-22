# Gate 6 R3 Goal / Checkpoint / Handoff 续跑预注册合同

> 文档状态：`frozen-before-r3-real-run`
>
> 日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> R1 结论：`blocked / launcher`
>
> R2 结论：`fail / DLP false positive`

---

## 1. R3 目的

R2 已证明真实 provider preflight 可以启动并返回正确身份，但输出 DLP 错误拒绝了
synthetic fixture 的正常 `workdir`。R3 只修复这一项边界：

> 允许当前 synthetic fixture root/repo 的绝对路径出现在 Codex 运行 header 中，同时继续
> fail-closed 拒绝 clean checkout 根路径、source chat 内容和 accepted memory 内容。

R1/R2 的 baseline、consumed tag、raw evidence 和结论全部保持不变。R3 使用新的执行
命名空间和新的 provider session budget。

---

## 2. 允许变化

R3 只允许：

- DLP 输出检查对 synthetic fixture root/repo 做路径白名单；
- 增加 synthetic workdir 允许、clean checkout 根路径拒绝的回归测试；
- 使用新的 R3 baseline/consumed tag 和 session/evidence 目录。

不得改变：

- frozen fixture、Goal、checkpoint、handoff/context 合同；
- provider、base URL、wire API、model、reasoning、auth；
- session hard limit `3`、automatic retries `0`；
- source chat/memory 内容扫描；
- Session A/B 任务、worker epoch 和 sentinel；
- `ready`、`split_required`、`blocked`、`fail` 终态定义。

---

## 3. R3 固定身份与命名

```text
provider = sandboxproxy
base URL = http://127.0.0.1:18080/v1
wire API = responses
model = sandbox-model
reasoning = high
auth = chatgpt
Codex CLI = 0.144.5
ephemeral = true
memory = off
automatic retries = 0
provider session hard limit = 3
```

```text
baseline tag = gate-6-r3-pre-run-v1
consumed tag = gate-6-r3-consumed-v1
fake session = gate6-r3-fake-contract-001
real session = gate6-r3-real-v1
output root = .local-validation/gate-6-r3
fixture root = .tmp/gate-6-r3
clean checkout = .tmp/gate-6-r3/clean-checkout-gate6-r3-real-v1
```

冻结 fixture：

```text
eval/gate-6/handoff-case.json
SHA-256 =
84bbdadb73eb85a088c597f9fafe76e525729a99e7007d861b6a3236921e7270
```

---

## 4. Deterministic 准入

R3 必须取得明确终态：

```text
test_checkpoint_handoff.py = pass
test_goal_cross_session.py = pass
test_gate6_handoff_dogfood.py = pass
compileall = pass
ruff = pass
git diff --check = pass
fake preflight + Session A + Session B = 3 slots
fake handoff/context = ready
fake accepted memory writes = 0
```

R3 fake 结果：

```text
session = gate6-r3-fake-contract-001
decision = fake-passed
handoff SHA-256 =
4ca1c7f362183d5cdc5a11957cd0d2f22191f38c0a5acff4a82da21793f32405
context SHA-256 =
d4d53dbee4fe0deb2679f8c0705d229c264fcb48623862b31a49d820540f45de
failures = []
```

---

## 5. Real 执行合同

真实调用前必须：

1. R2 `gate-6-r2-consumed-v1` 已存在且不可变；
2. R3 baseline commit、annotated tag 和 branch 已推送；
3. clean checkout 的 `HEAD` 与 R3 baseline tag 完全一致；
4. `core.autocrlf=false`、`core.eol=lf`；
5. R3 consumed tag 尚不存在；
6. 工作树干净；
7. Codex resolved executable、version 和 auth preflight 通过。

唯一命令：

```powershell
python scripts/gate6_handoff_dogfood.py `
  --runner real `
  --confirm-real `
  --session gate6-r3-real-v1 `
  --output-root .local-validation/gate-6-r3 `
  --fixture-root .tmp/gate-6-r3
```

任何 provider、DLP、execution artifact、token、timeout 或 termination 失败都必须立即
固化 `blocked` 或 `fail`，不得重试 R3。若需要继续，只能创建 R4 新 baseline。

---

## 6. R3 允许结果

```text
retain-as-langgraph-extension
reuse-independent-of-langgraph
experiment-only
reject-handoff
blocked
fail
```

R3 真实 provider 调用前：

```text
provider sessions = 0
R3 baseline = pending explicit commit
R3 consumed tag = not-created
```
