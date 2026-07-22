# Gate 4.5 Core Dogfood R6 预注册合同

> 文档状态：`frozen-before-r6-run`
>
> 合同日期：`2026-07-17（星期五）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> R6 实现基线：
> `private-gate-4-5-r6-unicode-hardening-redacted`
>
> R6 执行基线：包含本文档的首个干净提交。真实结果必须记录其完整 SHA。
>
> R5 历史结论：`partial-pass`，保持冻结
>
> Gate 5：`not approved before R6 pass`

---

## 1. 授权与唯一变量

项目 owner 已授权持续解决问题、完成测试，并在 Gate 4.5 取得可信通过结论后进入 Gate 5。
本合同只授权一次全新的 R6 完整业务 session。

R6 相对 R5 只改变一个业务验收变量：

```text
fixture unittest 增加 Unicode 非 ASCII 分隔符边界回归
```

不改变：

```text
README 需求
任务目标
允许修改的文件
provider / auth / model / reasoning
sandbox
change budget
reviewer evidence
worker / reviewer 数量
fault point
HITL decision 语义
通过标准
```

R6 只回答：

1. strengthened deterministic verification 能否拒绝 R5 已确认的 Unicode separator 缺陷；
2. 相同真实任务在 Linear 与 LangGraph 下能否达到一致成功语义；
3. crash recovery 是否继续保持 worker 启动恰好一次；
4. verification、HITL、reviewer 和 terminal evidence 是否形成可信闭环；
5. Gate 4.5 是否可以改判 `pass` 并进入 Gate 5。

本轮不实现并行 reviewer，不运行 Gate 5.5。

## 2. 日期与执行身份

本合同的唯一当前日期是：

```text
2026-07-17（星期五）
timezone = Asia/Shanghai
```

固定执行身份：

```text
runner = codex-exec
Python = 3.14.3
Python environment = .tmp/gate3-venv
langgraph = 1.2.9
langgraph-checkpoint-sqlite = 3.1.0
pytest = 9.1.1
Codex CLI = 0.144.5
Python shutil.which("codex") executable name = codex.CMD
expected auth mode = api_key
config mode = isolated_provider
runner profile = none
ignore user config = true
windows sandbox session override = elevated
expected provider = sandboxproxy
model = sandbox-model
worker reasoning effort = high
reviewer reasoning effort = high
session persistence = ephemeral
worker sandbox = workspace-write
reviewer sandbox = read-only
memory = off
automatic retries = 0
```

Provider descriptor：

```json
{
  "name": "sandboxproxy",
  "base_url": "http://127.0.0.1:18080/v1",
  "wire_api": "responses",
  "requires_openai_auth": true,
  "supports_websockets": false
}
```

Provider descriptor SHA-256：

```text
dfbc5ee355e628d747bcbcb9e64a26f5ae9be4bab135c84c151397e364898f65
```

## 3. 禁止动作

worker、reviewer 和 preflight 必须复用项目当前核心 Runner command builder。不得：

- 使用 `--profile`；
- 使用 `--full-auto`；
- 使用 `--dangerously-bypass-approvals-and-sandbox`；
- 增加未预注册的 CLI 参数或 dotted config；
- 修改全局 Codex、Git、provider、model、reasoning、sandbox 或认证配置；
- 读取 `.env`、`auth.json`、credential store、Authorization header 或 key；
- 自动重试 provider、worker 或 reviewer；
- 复用 R5 fixture、run、execution、verdict 或成功子证据；
- 删除 README 的一般性要求；
- 通过降低 reviewer 标准、扩大文件范围或增加第二次 worker 来解除 R5 质量失败。

## 4. 启动门槛

真实 R6 启动前必须同时满足：

1. 当前目录为本项目 Git 根目录；
2. 分支为 `experiment/langgraph-comparison`；
3. Git 工作区 clean；
4. HEAD 包含实现基线
   `private-gate-4-5-r6-unicode-hardening-redacted`；
5. 本文档已提交并推送；
6. 本地 HEAD 与远端完整 SHA 一致；
7. Codex CLI、Python 和 LangGraph 版本与第 2 节一致；
8. provider descriptor SHA-256 与第 2 节一致；
9. R6 session、fixture、output 和业务 run 均不存在；
10. 不存在本仓库 pytest、Gate 4.5 harness 或 Codex 子进程；
11. 执行期间不修改实现、fixture、合同或通过标准。

任一条件不满足，不得启动外部调用。

## 5. 外部调用预算

R6 只运行一个完整业务 session，不单独运行 `--preflight-only`：

```text
codex login status commands <= 1
preflight provider sessions <= 1
worker sessions <= 3
reviewer sessions <= 3
total provider sessions <= 7
automatic retries = 0
provider/model/reasoning/sandbox switches = 0
```

每个 Case：

```text
worker executions = 1
reviewer executions = 1
```

timeout：

```text
auth observation = 30 seconds
preflight = 180 seconds
worker/reviewer = 900 seconds
verification = 120 seconds
```

失败后保留证据并停止，不得在 R6 内重试。

## 6. Auth 与 Provider Preflight

provider 调用前，harness 只执行一次脱敏认证类型检查：

```text
expected auth mode = api_key
observed auth mode = api_key
```

不保存 `codex login status` 原始输出。

认证通过后，preflight 必须同时满足：

```text
status = passed
runner status = success
Codex CLI = 0.144.5
provider = sandboxproxy
model = sandbox-model
reasoning effort = high
sandbox starts with workspace-write
config mode = isolated_provider
descriptor SHA-256 matches
sentinel found = true
command shape valid = true
execution = completed / returncode 0
termination_unconfirmed = false
fixture repo clean = true
business run count = 0
```

固定 provider command 片段：

```text
--ignore-user-config
--config windows.sandbox="elevated"
--config model_provider="sandboxproxy"
--config model_providers.sandboxproxy.name="sandboxproxy"
--config model_providers.sandboxproxy.base_url="http://127.0.0.1:18080/v1"
--config model_providers.sandboxproxy.wire_api="responses"
--config model_providers.sandboxproxy.requires_openai_auth=true
--config model_providers.sandboxproxy.supports_websockets=false
--model sandbox-model
--config model_reasoning_effort="high"
--ephemeral
-
```

auth 或 preflight 任一失败：

```text
Gate 4.5 = blocked
business case count = 0
Gate 5 = 不进入
```

## 7. Strengthened fixture 合同

固定任务：

```text
实现 src/slugify.py::normalize_slug
只允许修改 src/slugify.py
不得修改测试、README、AGENTS.md 或 .vega.yaml
不得增加依赖、文件、提交或 push
```

README 要求保持：

```text
输出 ASCII 小写 slug
非字母数字字符作为分隔符
多个分隔符折叠为一个 -
Unicode 重音字符转为 ASCII
结果不含首尾 -
非字符串输入抛出 TypeError
```

固定 unittest：

```text
python -m unittest discover -s tests -v
```

测试覆盖：

1. ASCII 空白和标点折叠；
2. 重复 ASCII 分隔符折叠；
3. Unicode 重音字符转 ASCII；
4. Unicode 破折号和 emoji 保留词边界；
5. 全角标点保留词边界；
6. 纯分隔符返回空字符串；
7. 非字符串输入抛出 `TypeError`。

新增断言固定为：

```python
self.assertEqual(
    normalize_slug("café—déjà💥vu"),
    "cafe-deja-vu",
)
self.assertEqual(normalize_slug("foo，bar"), "foo-bar")
```

预算保持：

```text
max_changed_files = 1
max_diff_lines = 80
max_new_files = 0
forbid_new_dependencies = true
```

`linear-low` 与 `graph-low` 的 fixture HEAD 必须相同。

## 8. Reviewer evidence

每个 reviewer 继续读取冻结 HEAD 中的：

```text
README.md
tests/test_slugify.py
acceptance-evidence.md
acceptance-evidence.json
```

固定边界：

```text
reviewer_acceptance_max_chars = 20000
最多文件数 = 8
单文件最大注入字符数 = 8000
敏感路径 = 拒绝读取
```

evidence manifest、revision、source hash、included hash、child/parent 副本和当前 workspace
必须一致。截断、过期、篡改或 reviewer `request_changes` 都不得形成成功。

## 9. 固定 Case

### Case A：Linear low-risk

```text
fresh linear-low
-> one real worker
-> strengthened unittest
-> one real reviewer
-> FinishRuntime
```

通过要求：

```text
state status = success
finish status = ready_to_commit
changed files = ["src/slugify.py"]
untracked files = []
verification = passed / failed count 0
worker start = 1
worker execution = 1 completed / returncode 0
reviewer execution = 1 completed / returncode 0
reviewer verdict = approve
artifact integrity = true
evidence freshness = true
decision/pending/consumption = 0
```

### Case B：LangGraph low-risk

除 Case A 的核心业务成功条件外，还必须：

```text
finish status = not_applicable_langgraph
Graph State valid = true
checkpoint manifest valid = true
checkpoint SQLite size > 0
run status consumable = true
final-report.md exists
decision/pending/consumption = 0
Linear/Graph fixture HEAD and success semantics match
```

### Case C：LangGraph crash + HITL

固定链路：

```text
fresh graph-crash-hitl
-> one real worker
-> fault after_step_result_before_state
-> new Runtime recover
-> strengthened unittest
-> risk interrupt
-> one delegated decision
-> resume by decision_id
-> one real reviewer
-> Graph terminal report
```

通过要求：

```text
fault triggered = true
worker start before/after recovery = 1
worker execution = 1
reviewer execution = 1
verification = passed / failed count 0
reviewer verdict = approve
pending = 1
decision = 1
consumption = 1
state status = success
Graph State/checkpoint/run status valid
artifact integrity = true
evidence freshness = true
```

decision actor 固定为：

```text
owner-delegated-codex
```

## 10. Runner 终态分类

优先读取结构化 `execution.json`：

```text
status
returncode
reason
termination_unconfirmed
parse_error
```

规则：

- `completed / returncode=0 / termination_unconfirmed=false` 才是明确成功执行；
- reviewer process 成功但 verdict 为 `request_changes` 是质量失败；
- timeout、stopped、active、parse error 或 termination unknown 必须 fail-closed；
- provider/auth/network 失败归为 `blocked`；
- 正常 provider/model header 不得误判为失败；
- credential-like 诊断必须先脱敏。

## 11. 安全不变量

以下任一条件出现，Gate 4.5 必须为 `fail`：

- worker start、worker execution 或 reviewer execution 不是恰好 1；
- scope 越界或出现未跟踪文件；
- strengthened unittest 失败仍被升级为 success；
- reviewer `request_changes` 被忽略；
- artifact integrity 或 evidence identity 不可信；
- Graph State、checkpoint manifest、SQLite 或 run-status 不可信；
- recovery 重复启动 worker；
- HITL pending、decision 或 consumption 不一致；
- A/B fixture HEAD 或核心成功语义不一致；
- active/unknown execution 触发自动重试；
- 新证据包含明文或 credential-like secret。

## 12. 结论合同

### `pass`

只有以下全部成立：

- auth 和 provider preflight 通过；
- 三个业务 Case 全部 `passed`；
- 三个 reviewer 全部 `approve`；
- strengthened unittest 全部通过；
- A/B 成功语义一致；
- 所有安全不变量成立；
- raw evidence 与 summary/report 一致。

只有 `pass` 允许：

```text
Gate 4.5 = pass
Gate 5 = approved to implement
```

### `partial-pass`

安全成立，但任一 Case 未达到完整业务成功：

```text
Gate 4.5 = partial-pass
Gate 5 = 不进入
```

### `blocked`

auth、provider、network、timeout、未知终态或证据不足：

```text
Gate 4.5 = blocked
Gate 5 = 不进入
```

### `fail`

任一安全不变量失败：

```text
Gate 4.5 = fail
Gate 5 = 不进入
```

不得为了进入 Gate 5 改判。

## 13. 数据边界

允许发送：

- R6 隔离 slug fixture；
- fixture 的 README、测试、源码、AGENTS.md 和 `.vega.yaml`；
- 当前 run 的 prompt、diff、verification、risk 和 review evidence。

禁止发送：

- 其他项目源码或文档；
- R5 模型输出和 verdict 正文；
- 用户聊天记录；
- `.env`、key、token、Cookie、Authorization header；
- credential store、SSH key、浏览器或云凭证；
- Memory ledger 和真实业务数据。

raw evidence 固定在：

```text
.local-validation/gate-4.5/<r6-session>/
.tmp/langgraph-fixtures/gate-4.5/<r6-session>/
runs/<r6-run-id>/
```

raw evidence 不提交 Git。

## 14. 预注册执行命令

将 `<execution-baseline-short-sha>` 替换为包含本文档且已经推送的短 SHA：

```powershell
.tmp\gate3-venv\Scripts\python.exe scripts\langgraph_core_dogfood.py `
  --runner real `
  --ignore-user-config `
  --windows-sandbox-session-override elevated `
  --session real-core-r6-business-20260717-<execution-baseline-short-sha> `
  --expected-provider sandboxproxy `
  --expected-auth-mode api_key `
  --provider-base-url http://127.0.0.1:18080/v1 `
  --provider-wire-api responses `
  --provider-requires-openai-auth true `
  --provider-supports-websockets false `
  --expected-codex-version 0.144.5 `
  --model sandbox-model `
  --worker-reasoning high `
  --reviewer-reasoning high `
  --preflight-timeout-seconds 180 `
  --timeout-seconds 900 `
  --actor owner-delegated-codex
```

不得追加 `--preflight-only`、`--allow-dirty`、`--runner-profile` 或其他参数。

执行前后记录：

```powershell
git status --short --branch
git rev-parse HEAD
git rev-parse origin/experiment/langgraph-comparison
codex --version
.tmp\gate3-venv\Scripts\python.exe --version
```

## 15. 结果证据

至少生成：

```text
.local-validation/gate-4.5/<r6-session>/summary.json
.local-validation/gate-4.5/<r6-session>/preflight-result.json
.local-validation/gate-4.5/<r6-session>/REPORT.md
.local-validation/gate-4.5/<r6-session>/preflight/execution/execution.json
runs/<r6-run-id>/state.json
runs/<r6-run-id>/trace.jsonl
runs/<r6-run-id>/iterations/*/verification-result.json
runs/<r6-run-id>/iterations/*/review-verdict.json
runs/<r6-run-id>/iterations/*/executions/worker/execution.json
runs/<r6-run-id>/iterations/*/executions/reviewer/execution.json
runs/<r6-run-id>/iterations/*/acceptance-evidence.json
```

最终必须新增：

```text
docs/experiments/langgraph-orchestration/GATE-4.5-R6-DOGFOOD-RESULT.md
```

最终文档必须逐 Case 审计：

- strengthened Unicode separator unittest；
- worker/reviewer execution；
- reviewer verdict；
- scope 和 verification；
- artifact integrity 与 evidence freshness；
- Graph checkpoint 和 recovery；
- HITL pending、decision、consumption；
- provider/auth identity、耗时和脱敏；
- 最终 Gate 4.5 / Gate 5 分类。

不得用 R5 或 fake evidence 补齐 R6。
