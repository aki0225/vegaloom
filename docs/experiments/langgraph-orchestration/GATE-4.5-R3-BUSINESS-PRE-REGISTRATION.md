# Gate 4.5 Core Dogfood R3 业务预注册合同

> 文档状态：`frozen-before-r3-business-run`
>
> 日期：2026-07-16
>
> 分支：`experiment/langgraph-comparison`
>
> R3 preflight 执行基线：
> `private-gate-4-5-r3-preflight-contract-redacted`
>
> R3 preflight 结论：`preflight-passed`
>
> 单 reviewer 硬门槛实现：
> `private-gate-4-5-single-reviewer-test-redacted`
>
> 业务执行基线：包含本文档的首个干净提交。真实结果必须记录其完整 SHA。

---

## 1. 授权与研究问题

项目 owner 已在当前会话明确授权：

> 按冻结 Gate 合同继续完成 R3；只有 Gate 4.5 通过时才进入并完成 Gate 5。

本轮业务运行只回答：

1. 相同真实 slug 任务在 Linear 与 LangGraph 下能否达到一致成功语义；
2. 真实 worker 写入后在预注册 fault 点中断，LangGraph 是否会重复启动 worker；
3. recovery 后能否完成 verification、结构化 HITL、一次 decision consumption 和一次真实
   reviewer；
4. workspace、Graph State、checkpoint、manifest、decision、execution 和 terminal report
   是否形成可信闭环；
5. Gate 4.5 是否具备进入 Gate 5 deterministic parallel reviewer 实现的资格。

本轮不验证真实并行 reviewer，不评价三 reviewer 的业务收益，也不运行 Gate 5.5。

## 2. 不可修改的执行身份

```text
runner = codex-exec
Python shutil.which("codex") = <codex-wrapper>
Codex CLI = 0.144.4
authentication = existing ChatGPT login
config mode = ignore_user_config
runner profile = none
windows sandbox session override = elevated
expected provider = openai
model = sandbox-model
worker reasoning effort = high
reviewer reasoning effort = high
session persistence = ephemeral
worker sandbox = workspace-write
reviewer sandbox = read-only
memory = off
reviewer count per business case = 1
automatic retries = 0
```

worker 与 reviewer 必须由项目现有 `CodexExecRunner` 构造。不得：

- 使用 `--profile`；
- 使用 `--full-auto`；
- 使用 `--dangerously-bypass-approvals-and-sandbox`；
- 增加未预注册的 `--config` 或 CLI 参数；
- 修改用户全局 Codex 配置；
- 切换 provider、model、reasoning 或 sandbox；
- 读取 `.env`、credential store、Authorization header 或明文 key。

## 3. 干净基线

真实业务 session 启动前必须同时满足：

1. 当前分支为 `experiment/langgraph-comparison`；
2. Git 工作区 clean；
3. 当前 HEAD 包含 R3 preflight 结果和
   `private-gate-4-5-single-reviewer-test-redacted`；
4. 本文档已经提交并推送；
5. 本地 HEAD 与 `origin/experiment/langgraph-comparison` 完整 SHA 一致；
6. Codex CLI 版本仍为 `0.144.4`；
7. 使用全新业务 session、fixture 和结果目录；
8. 不复用 R0、R1、R2、R3 preflight 或 fake harness 的 fixture、run、execution；
9. 执行期间不修改 Runtime、harness、合同或通过标准；
10. 不存在仍在运行的 pytest、Gate 4.5 execution 或相关 Codex 子进程。

任一条件不满足，不得启动外部调用。

## 4. 外部调用预算

R3 已完成的独立 preflight：

```text
preflight sessions already consumed = 1
session = real-core-r3-preflight-20260716-private-gate-4-5-r3-preflight-contract-redacted
```

当前 harness 在完整业务 session 开始时会再次执行一次同源 preflight。该调用被明确接受并
计入总预算，不能省略、隐藏或改成复用旧结果。

```text
R3 total preflight sessions <= 2
R3 business-session preflight sessions <= 1
worker sessions <= 3
reviewer sessions <= 3
R3 total external sessions <= 8
automatic retries = 0
provider/model switches = 0
```

每个业务 Case 最多：

```text
worker executions = 1
reviewer executions = 1
```

timeout 固定为：

```text
preflight timeout = 180 seconds
worker/reviewer timeout = 900 seconds
```

Codex CLI 内部 transport reconnect 或 WebSocket 到 HTTPS fallback 可以作为同一个 execution
的诊断信息保留，但 Vega 不得因此启动第二个 attempt。

## 5. 业务 Session Preflight

业务 session 必须先创建独立最小 preflight fixture，并重新验证：

1. Runner status 为 `success`；
2. sentinel 来自 assistant/codex 输出；
3. Codex CLI 为 `0.144.4`；
4. provider 为 `openai`；
5. model 为 `sandbox-model`；
6. reasoning effort 为 `high`；
7. observed sandbox 以 `workspace-write` 开头；
8. command shape、command hash 和 Runner identity 一致；
9. execution artifact 为明确成功终态；
10. fixture 前后 clean；
11. preflight 阶段业务 run 数量为 0。

任一断言失败：

```text
Gate 4.5 = blocked
business case count = 0
Gate 5 = 不进入
```

失败后不重试、不切换配置、不继续创建业务 fixture。

## 6. Fixture 合同

所有 fixture 都是项目内的独立 Git 仓库：

```text
.tmp/langgraph-fixtures/gate-4.5/<business-session>/
```

固定业务 Case：

```text
linear-low
graph-low
graph-crash-hitl
```

固定任务：

```text
实现 src/slugify.py::normalize_slug
只允许修改 src/slugify.py
不得增加依赖、修改测试或提交 Git
```

固定验收：

```text
python -m unittest discover -s tests -v
```

测试覆盖：

- 空白和标点折叠；
- 连续分隔符折叠；
- Unicode 重音转 ASCII；
- 纯分隔符返回空字符串；
- 非字符串输入抛出 `TypeError`。

预算：

```text
max_changed_files = 1
max_diff_lines = 80
max_new_files = 0
forbid_new_dependencies = true
verification_timeout_seconds = 120
runner_timeout_seconds = 900
```

`linear-low` 与 `graph-low` 的 fixture commit 必须完全相同。

## 7. 固定 Case

### 7.1 Case A：Linear 低风险对照

```text
fresh linear-low fixture
-> one real worker
-> deterministic verification
-> one real reviewer
-> FinishRuntime
```

通过要求：

- `state_status=success`；
- `finish_status=ready_to_commit`；
- changed files 恰好为 `src/slugify.py`；
- untracked files 为空；
- verification 为 `passed` 且 failed count 为 0；
- worker start 恰好为 1；
- worker execution artifact 恰好为 1；
- reviewer execution artifact 恰好为 1；
- artifact integrity 与 evidence freshness 为 true；
- decision 与 consumption 数量为 0。

### 7.2 Case B：LangGraph 低风险对照

```text
fresh graph-low fixture
-> one real worker
-> LangGraph sequential
-> deterministic verification
-> one real reviewer
-> Graph terminal report
```

除 Case A 的核心业务成功条件外，还必须：

- `finish_status=not_applicable_langgraph`；
- Graph State 终态可验证；
- checkpoint manifest 和 SQLite 可验证；
- checkpoint SQLite 大于 0；
- `run-status` 可消费可信终态；
- `final-report.md` 存在；
- decision 与 consumption 数量为 0；
- A/B 的 state、verification、changed files 和业务成功语义一致。

### 7.3 Case C：LangGraph Crash + HITL

```text
fresh graph-crash-hitl fixture
-> one real worker
-> fault = after_step_result_before_state
-> new Runtime recover
-> deterministic verification
-> risk interrupt
-> validate pending evidence
-> delegated decision ledger approval
-> resume by decision_id
-> one real reviewer
-> Graph terminal report
```

decision actor 固定为：

```text
owner-delegated-codex
```

通过要求：

- fault injector 命中；
- recover 前后 worker start 始终恰好为 1；
- worker execution artifact 恰好为 1；
- reviewer execution artifact 恰好为 1；
- pending decision identity 与 evidence refs 可信；
- decision ledger entry 恰好为 1；
- pending artifact 恰好为 1；
- decision consumption artifact 恰好为 1；
- verification 为 `passed` 且 failed count 为 0；
- 最终 `state_status=success`；
- Graph State、checkpoint manifest、SQLite、run-status 和 terminal report 可信；
- `finish_status=not_applicable_langgraph`。

Case C 的受控 fault injection 不是操作系统真实 abrupt exit，不能用于关闭 Windows
abrupt-exit 残余风险。

## 8. 安全不变量

以下任一条件出现，Gate 4.5 必须为 `fail`：

- worker start 不是恰好 1；
- worker execution artifact 不是恰好 1；
- reviewer execution artifact 不是恰好 1；
- changed 或 untracked files 越界；
- verification failure 被升级为 success；
- success 仍存在 eval failure；
- artifact integrity 或 evidence freshness 不可信；
- Graph State、checkpoint manifest、SQLite 或 run-status 不可信；
- checkpoint、WAL、journal 与 manifest 出现不一致；
- LangGraph success 缺少 `final-report.md`；
- 低风险 Case 产生 decision consumption；
- HITL Case 的 fault、pending、decision 或 consumption identity 不一致；
- recovery 重复启动 worker 或重复未知外部副作用；
- A/B fixture HEAD 或核心成功语义不一致；
- 证据被旧 session、旧 snapshot 或其他 run 污染。

如果 worker 或 reviewer execution 处于 active、终态未知或
`termination_unconfirmed=true`，不得重试；结论按证据可判断程度进入 `blocked` 或 `fail`，
不能选择性忽略。

## 9. 结论合同

### `pass`

只有以下条件全部成立：

- 业务 session preflight 通过；
- 三个业务 Case 全部为 `passed`；
- A/B 核心成功语义一致；
- 所有安全不变量成立；
- 没有未关闭 Blocker / High；
- raw evidence 与 summary/report 一致。

只有 `pass` 允许：

```text
Gate 4.5 = pass
Gate 5 = approved to implement
```

### `partial-pass`

安全不变量成立，但任一业务 Case 只达到质量失败或未达到完整成功语义：

```text
Gate 4.5 = partial-pass
Gate 5 = 不进入
```

### `blocked`

provider unavailable、timeout、身份漂移、未知终态或证据不足：

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

不得为了进入 Gate 5 把 `partial-pass`、`blocked` 或 `fail` 改判为 `pass`。

## 10. 数据出站与持久化边界

允许发送给 provider：

- 本文档定义的隔离 slug fixture；
- fixture 内的 `AGENTS.md`、README、源码和测试；
- Vega 为该 fixture 生成的 prompt、diff 摘要、verification、risk 和 review evidence。

禁止发送：

- 其他项目源码或 diff；
- R0、R1、R2、R3 preflight 原始模型输出；
- 用户聊天记录；
- `.env`、key、token、Cookie 或 Authorization header；
- Codex credential store；
- 用户目录、SSH key、浏览器状态或云凭证；
- Vega Memory ledger；
- 真实业务数据。

raw evidence 固定在：

```text
.local-validation/gate-4.5/<business-session>/
.tmp/langgraph-fixtures/gate-4.5/<business-session>/
runs/<business-run-id>/
```

raw evidence、fixture、run、SQLite、日志和认证信息不得提交 Git。Git 只提交最终结果文档和
后续通过 Gate 合同允许的实现。

## 11. 预注册执行命令

将 `<baseline-short-sha>` 替换为包含本文档且已经推送的执行基线短 SHA：

```powershell
.tmp\langgraph-validation-venv\Scripts\python.exe scripts\langgraph_core_dogfood.py `
  --runner real `
  --ignore-user-config `
  --windows-sandbox-session-override elevated `
  --session real-core-r3-business-20260716-<baseline-short-sha> `
  --expected-provider openai `
  --expected-codex-version 0.144.4 `
  --model sandbox-model `
  --worker-reasoning high `
  --reviewer-reasoning high `
  --preflight-timeout-seconds 180 `
  --timeout-seconds 900 `
  --actor owner-delegated-codex
```

执行前后必须记录：

```powershell
git status --short --branch
git rev-parse HEAD
git rev-parse origin/experiment/langgraph-comparison
codex --version
```

只有 HEAD、远端 SHA、本文档、Codex 版本和工作区状态全部一致，才允许使用本次结果更新
Gate 4.5 结论。

## 12. 结果证据

至少生成：

```text
.local-validation/gate-4.5/<business-session>/summary.json
.local-validation/gate-4.5/<business-session>/preflight-result.json
.local-validation/gate-4.5/<business-session>/REPORT.md
.local-validation/gate-4.5/<business-session>/preflight/execution/execution.json
runs/<business-run-id>/state.json
runs/<business-run-id>/trace.jsonl
runs/<business-run-id>/iterations/*/executions/worker/execution.json
runs/<business-run-id>/iterations/*/executions/reviewer/execution.json
```

最终必须新增：

```text
docs/experiments/langgraph-orchestration/GATE-4.5-R3-DOGFOOD-RESULT.md
```

最终文档必须逐 Case 记录 execution 数量、scope、verification、Graph 控制面、HITL、
integrity/freshness、transport warning、耗时和结论，不得只引用 harness 的聚合标签。
