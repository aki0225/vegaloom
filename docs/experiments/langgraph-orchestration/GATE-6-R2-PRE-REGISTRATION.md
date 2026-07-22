# Gate 6 R2 Goal / Checkpoint / Handoff 续跑预注册合同

> 文档状态：`frozen-before-r2-real-run`
>
> 日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> R1 baseline：`gate-6-pre-run-v1`
>
> R1 consumed tag：`gate-6-consumed-v1`
>
> R1 结论：`blocked / preflight launcher`

---

## 1. R2 目的

R1 在 provider 请求前被 Windows Codex executable resolution 阻断。R2 只回答一个问题：

> 修复真实 Codex launcher 的 Windows 路径和版本解析后，Gate 6 的真实 preflight、
> Session A 和 fresh Session B 能否按原合同完成。

R1 的 baseline、consumed tag、summary、report 和结论保持不可变。R2 不是 R1 的重试，
不复用 R1 的 execution tag、consumed tag、fixture session 或结果目录。

---

## 2. R2 允许的代码变化

R2 只允许以下启动兼容性变化：

- real runner 使用 `shutil.which("codex")` 返回的实际 executable 路径；
- Windows `.CMD` wrapper 可以被 preflight 的版本和登录状态检查直接启动；
- `codex-cli 0.144.5`、`OpenAI Codex v0.144.5` 等等价版本输出归一化为
  `0.144.5`；
- 增加 executable 缺失和 `.CMD` 路径解析回归测试；
- R2 的 baseline/consumed tag 使用新名称。

以下内容不得改变：

- `eval/gate-6/handoff-case.json` 内容和 SHA-256；
- Goal objective、checkpoint task、acceptance 和 final API；
- source chat canary、memory mode、DLP 规则；
- provider、base URL、wire API、model、reasoning、auth；
- provider session hard limit、automatic retries 和终态分类；
- handoff/context 的安全边界和 drift/tamper 失败条件。

---

## 3. 冻结身份与预算

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

固定 session identity：

```text
preflight = gate6-preflight
Session A = gate6-session-a
Session B = gate6-session-b
source worker epoch = gate6-epoch-a
target worker epoch = gate6-epoch-b
handoff version = v0001
```

R2 使用新实验命名空间：

```text
fake session = gate6-r2-fake-contract-001
real session = gate6-r2-real-v1
clean checkout = .tmp/gate-6-r2/clean-checkout-gate6-r2-real-v1
evidence root = .local-validation/gate-6-r2
fixture root = .tmp/gate-6-r2
```

---

## 4. R2 不可变执行标签

R2 baseline 提交后必须创建两个 annotated tag：

```text
baseline tag = gate-6-r2-pre-run-v1
consumed tag = gate-6-r2-consumed-v1
```

真实 harness 只允许在以下条件全部成立时启动：

1. R1 的 `gate-6-consumed-v1` 已存在且保持不变；
2. R2 `gate-6-r2-pre-run-v1` 是 annotated tag；
3. clean checkout 的 `HEAD == gate-6-r2-pre-run-v1^{commit}`；
4. clean checkout `core.autocrlf=false`、`core.eol=lf`；
5. clean checkout 工作树完全干净；
6. R2 `gate-6-r2-consumed-v1` 尚不存在；
7. R2 branch 和两个 tag 已推送到远端。

真实 harness 启动前先原子创建 R2 consumed tag。创建成功后，即使后续 blocked 或 fail，
也不得删除、移动或覆盖该 tag。

---

## 5. Preflight 合同

preflight 必须先完成以下检查，任何一项失败都停止后续 session：

- `codex` 通过 `shutil.which` 解析为可执行路径；
- 版本命令能实际启动并归一化为 `0.144.5`；
- `codex login status` 报告 `chatgpt`；
- real runner 的 command shape、provider、model、reasoning 和 sandbox header 符合固定合同；
- execution artifact 为 `completed`，`returncode=0`，终止状态已确认；
- synthetic fixture 在 preflight 前后保持 clean；
- source chat 和 accepted memory 继续由 Windows 独占共享保护；
- provider session budget 尚未超过 3。

preflight 失败时：

```text
R2 decision = blocked
Session A/B = not-started
automatic retries = 0
```

不得通过放宽 sandbox、切换 provider/model/auth、修改用户全局配置或重试同一 session
来包装成功。

---

## 6. Deterministic 验证

R2 必须重新取得明确终态：

```text
test_checkpoint_handoff.py
test_goal_cross_session.py
test_gate6_handoff_dogfood.py
```

此外必须通过：

```text
python -m compileall -q src scripts/gate6_handoff_dogfood.py
ruff check Gate 6 source/tests/scripts
git diff --check
```

R2 fake harness 必须完成：

```text
preflight + Session A + Session B = 3 slots
handoff status = ready
context status = ready
source chat included = false
accepted memory writes = 0
Session A/B distinct = true
failures = []
```

fake 证据不替代 real provider 证据，但 fake 失败时不得启动 real。

---

## 7. 唯一真实执行命令

R2 baseline 和 clean checkout 验证完成后，只允许执行一次：

```powershell
python scripts/gate6_handoff_dogfood.py `
  --runner real `
  --confirm-real `
  --session gate6-r2-real-v1 `
  --output-root .local-validation/gate-6-r2 `
  --fixture-root .tmp/gate-6-r2
```

执行过程中：

- 不自动重试；
- 不切换 provider、model、auth 或 reasoning；
- 不启动第二个 preflight；
- 不复用 R1 或其他 Gate 的 provider 预算；
- 不把 transport reconnect 伪装成新的 Vega session；
- 不把 `blocked` 或 `fail` 改写为 `success`。

---

## 8. 允许的结果

R2 只允许以下机器终态：

```text
retain-as-langgraph-extension
reuse-independent-of-langgraph
experiment-only
reject-handoff
blocked
fail
```

R2 若真实 preflight 或 worker 因新的本机启动问题 blocked，必须保留 R2 consumed tag 和
完整证据；只有修复后建立新的 R3 baseline，才能继续，不得重跑 R2。

---

## 9. 冻结状态

```text
R1 history = immutable
R2 synthetic fixture = same frozen case
R2 launcher fix = scoped
R2 real provider calls = 0 before baseline execution
R2 execution baseline = pending explicit commit
R2 baseline tag = gate-6-r2-pre-run-v1
R2 consumed tag = gate-6-r2-consumed-v1
```
