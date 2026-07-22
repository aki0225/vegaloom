# Gate 6 R4 Goal / Checkpoint / Handoff 续跑预注册合同

> 文档状态：`frozen-before-r4-real-run`
>
> 日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> R1：`blocked / launcher`
>
> R2：`fail / DLP false positive`
>
> R3：`fail / verification cache misclassified`

---

## 1. R4 目的

R3 已证明真实 preflight 和 Session A 能运行；失败仅来自 verification 产生的
`__pycache__` ignored 文件。R4 只修复 ignored cache 过滤，不改变业务合同：

> 允许已知 Python/pytest 工具缓存，不允许任何未知 ignored artifact、Git 元数据或
> 权威业务文件变化。

---

## 2. 允许变化

R4 只允许：

- `__pycache__/` 和 `.pytest_cache/` ignored 路径从 `status_ignored` 摘要中过滤；
- 增加 known cache allowlist 回归测试；
- 使用 R4 新 baseline、consumed tag、session 和 evidence 目录。

不得改变：

- frozen fixture、Goal、checkpoint、handoff/context 合同；
- provider、model、auth、reasoning、sandbox 和预算；
- Session A/B prompt、worker epoch、sentinel；
- 未知 ignored 路径、Git metadata、refs、remote 和权威 artifact 的拦截规则。

---

## 3. R4 固定身份与命名

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
baseline tag = gate-6-r4-pre-run-v1
consumed tag = gate-6-r4-consumed-v1
fake session = gate6-r4-fake-contract-001
real session = gate6-r4-real-v1
output root = .local-validation/gate-6-r4
fixture root = .tmp/gate-6-r4
clean checkout = .tmp/gate-6-r4/clean-checkout-gate6-r4-real-v1
```

---

## 4. Deterministic 准入

R4 必须取得：

```text
test_checkpoint_handoff.py = 19 passed
test_goal_cross_session.py = 3 passed
test_gate6_handoff_dogfood.py = 7 passed
Gate 6 R4 related total = 29 passed
compileall = pass
ruff = pass
git diff --check = pass
fake preflight + Session A + Session B = 3 slots
fake handoff/context = ready
```

---

## 5. Real 执行合同

真实调用前必须：

1. R3 `gate-6-r3-consumed-v1` 已存在且不可变；
2. R4 baseline、annotated tag 和 branch 已推送；
3. clean checkout 的 `HEAD` 与 R4 baseline tag 完全一致；
4. `core.autocrlf=false`、`core.eol=lf`；
5. R4 consumed tag 尚不存在；
6. 工作树干净；
7. resolved executable、Codex version、auth mode 正确。

唯一命令：

```powershell
python scripts/gate6_handoff_dogfood.py `
  --runner real `
  --confirm-real `
  --session gate6-r4-real-v1 `
  --output-root .local-validation/gate-6-r4 `
  --fixture-root .tmp/gate-6-r4
```

R4 若仍在 provider 启动后 blocked/fail，保留全部 token、execution 和安全证据，不得
重试 R4；继续处理只能建立 R5 新 baseline。
