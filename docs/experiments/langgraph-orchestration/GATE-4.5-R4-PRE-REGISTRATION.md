# Gate 4.5 Core Dogfood R4 预注册合同

> 文档状态：`frozen-before-r4-run`
>
> 合同日期：`2026-07-17（星期五）`
>
> 分支：`experiment/langgraph-comparison`
>
> R4 实现基线：
> `private-gate-4-5-r4-hardening-redacted`
>
> R4 执行基线：包含本文档的首个干净提交。真实结果必须记录其完整 SHA。
>
> R3 历史结论：`blocked`，保持冻结
>
> Gate 5：`not approved before R4 pass`

---

## 1. 授权与研究问题

项目 owner 已授权持续解决问题、完成测试，并在 Gate 4.5 取得可信通过结论后进入 Gate 5。
本合同只授权一次全新的 R4 完整业务 session，不授权跳过门槛、重复消费成功子证据或修改历史
结果。

R4 只回答：

1. 相同真实 slug 任务在 Linear 与 LangGraph 下能否达到一致成功语义；
2. 真实 worker 写入后在预注册 fault 点中断，LangGraph 是否会重复启动 worker；
3. recovery 后能否继续 verification、结构化 HITL、一次 decision consumption 和一次真实
   reviewer；
4. R4 新增的结构化 Runner 终态分类和有界验收证据包，能否消除 R3 的误分类与证据不足；
5. workspace、业务状态、Graph State、checkpoint、manifest、decision、execution 和 terminal
   report 是否形成可信闭环；
6. Gate 4.5 是否具备进入 Gate 5 确定性并行 reviewer 实现的资格。

本轮不实现或验证三路并行 reviewer，不评价多 reviewer 的边际收益，也不运行 Gate 5.5。

## 2. 日期与证据基线

本合同的唯一当前日期是：

```text
2026-07-17（星期五）
timezone = Asia/Shanghai
```

R4 session、run id 或本地 artifact 使用 `20260717-*` 属于当天正常证据，不得再解释为未来
时间戳。Evidence freshness 由以下身份共同决定：

```text
execution baseline SHA
session identity
fixture HEAD
run identity
workspace fingerprint
artifact hash
structured execution terminal status
```

不得只根据文件名或 run id 中的日期判定 freshness。

## 3. 不可修改的执行身份

```text
runner = codex-exec
Python = 3.14.3
Python environment = .tmp/gate3-venv
langgraph = 1.2.9
langgraph-checkpoint-sqlite = 3.1.0
pytest = 9.1.1
Codex CLI = 0.144.5
Python shutil.which("codex") executable = codex.CMD
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

worker、reviewer 和 preflight 必须由项目现有 `CodexExecRunner` 构造。不得：

- 使用 `--profile`；
- 使用 `--full-auto`；
- 使用 `--dangerously-bypass-approvals-and-sandbox`；
- 增加未预注册的 `--config` 或 CLI 参数；
- 修改用户全局 Codex、Git、provider、model、reasoning 或 sandbox 配置；
- 读取 `.env`、credential store、Authorization header 或明文 key；
- 将其他项目、用户聊天记录或历史模型输出加入 prompt。

Codex 可执行文件的绝对 PATH 解析结果和完整命令只保存在本地 raw evidence；Git 文档只冻结
可执行文件名、CLI 版本、command hash 和结构化 runner identity。

## 4. 干净基线与启动门槛

真实 session 启动前必须同时满足：

1. 当前仓库根目录为本项目 checkout；
2. 当前分支为 `experiment/langgraph-comparison`；
3. Git 工作区 clean；
4. 当前 HEAD 包含实现基线
   `private-gate-4-5-r4-hardening-redacted`；
5. 本文档已经提交并推送；
6. 本地 HEAD 与 `origin/experiment/langgraph-comparison` 完整 SHA 一致；
7. Codex CLI 版本仍为 `0.144.5`；
8. Python 和 LangGraph 依赖版本仍与第 3 节一致；
9. R4 session、fixture、output 和业务 run 均为全新身份；
10. 不复用 R0、R1、R2、R3 或 fake harness 的 fixture、run、execution 和结果；
11. 不存在仍在运行的 pytest、Gate 4.5 harness 或相关 Codex 子进程；
12. 执行期间不修改 Runtime、harness、合同、fixture 或通过标准。

任一条件不满足，不得启动外部调用。

## 5. 外部调用预算

R4 采用一个完整业务 session，harness 内置 fail-fast preflight，不单独运行
`--preflight-only`：

```text
preflight sessions <= 1
worker sessions <= 3
reviewer sessions <= 3
R4 total external sessions <= 7
automatic retries = 0
provider/model/reasoning/sandbox switches = 0
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
verification timeout = 120 seconds
```

Codex CLI 内部 transport reconnect 或 WebSocket 到 HTTPS fallback 可以作为同一个 execution
的诊断信息保留，但 Vega 不得因此启动第二个 attempt。

如果 session 因 provider、timeout、未知终态或安全不变量失败而不能通过：

1. 保留本轮 raw summary、REPORT、execution 和 run artifacts；
2. 不回写或选择性拼接本轮成功 Case；
3. 不在 R4 中自动重试；
4. 修复必须先补确定性测试并形成新的干净提交；
5. 后续真实重跑必须冻结独立 R5 合同、session、fixture 和预算。

## 6. 内置 Preflight

完整业务 session 必须先创建独立最小 preflight fixture，并验证：

1. Runner status 为 `success`；
2. sentinel 来自 assistant/codex 输出；
3. Codex CLI 为 `0.144.5`；
4. provider 为 `openai`；
5. model 为 `sandbox-model`；
6. reasoning effort 为 `high`；
7. observed sandbox 以 `workspace-write` 开头；
8. command shape、command hash 和 Runner identity 一致；
9. `execution.json` 为 `completed / returncode=0`；
10. `termination_unconfirmed=false`；
11. fixture 前后 clean；
12. preflight 阶段业务 run 数量为 0。

任一断言失败：

```text
Gate 4.5 = blocked
business case count = 0
Gate 5 = 不进入
```

失败后不重试、不切换配置、不继续创建业务 fixture。

## 7. Fixture 合同

所有 fixture 都是项目内的独立 Git 仓库：

```text
.tmp/langgraph-fixtures/gate-4.5/<r4-session>/
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
```

`linear-low` 与 `graph-low` 的 fixture commit 必须完全相同。

## 8. 有界 Reviewer 验收证据

每个 reviewer 必须使用与当前业务 run 绑定的冻结验收证据包：

```text
acceptance-evidence.md
acceptance-evidence.json
```

证据只允许从 review 时冻结的 Git HEAD 读取，并按以下优先级选择：

1. Agent Brief 显式引用的 tracked 需求或测试文件；
2. 仓库根 README；
3. 与 changed file 同名的 tracked 需求文档；
4. 与 changed file 同名的 tracked 基线测试。

固定边界：

```text
reviewer_acceptance_max_chars = 20000
最多文件数 = 8
单文件最大注入字符数 = 8000
敏感路径 = 拒绝读取
```

R4 通过还要求：

- evidence manifest、源文件哈希和注入内容哈希一致；
- child review 与 loop iteration 的证据副本通过 integrity/freshness 校验；
- 未跟踪或 worker 中途修改的 `.vega.yaml` 不进入 reviewer prompt；
- 若发生证据截断、候选省略或 evidence package 不可信，即使模型返回 `approve`，也必须
  降级为 `needs_human`，该 Case 不得计为 passed。

## 9. 固定 Case

### 9.1 Case A：Linear 低风险对照

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
- worker/reviewer execution 都是明确成功终态；
- acceptance evidence、artifact integrity 与 evidence freshness 为 true；
- decision、pending 与 consumption 数量均为 0。

### 9.2 Case B：LangGraph 低风险对照

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
- decision、pending 与 consumption 数量均为 0；
- A/B 的 fixture HEAD、state、verification、changed files 和业务成功语义一致。

### 9.3 Case C：LangGraph Crash + HITL

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
- worker/reviewer execution 都是明确成功终态；
- pending decision identity 与 evidence refs 可信；
- decision ledger entry 恰好为 1；
- pending artifact 恰好为 1；
- decision consumption artifact 恰好为 1；
- verification 为 `passed` 且 failed count 为 0；
- acceptance evidence、artifact integrity 与 evidence freshness 为 true；
- 最终 `state_status=success`；
- Graph State、checkpoint manifest、SQLite、run-status 和 terminal report 可信；
- `finish_status=not_applicable_langgraph`。

Case C 的受控 fault injection 不是操作系统真实 abrupt exit，不能用于关闭 Windows
abrupt-exit 残余风险。

## 10. Runner 终态分类

R4 结论必须优先读取 worker/reviewer 的结构化 `execution.json`：

```text
status
returncode
reason
termination_unconfirmed
parse_error
```

分类规则：

- `completed / returncode=0 / termination_unconfirmed=false` 是明确成功执行事实；
- reviewer 明确成功执行但返回 `needs_human` 是业务质量结论，不是 provider failure；
- 正常 provider/model/Codex header 不是 provider failure；
- WebSocket 超时后成功回退 HTTPS 且 execution 最终成功，不是 provider failure；
- `timed_out`、`stopped`、active/non-terminal、无法解析 execution 或
  `termination_unconfirmed=true` 必须 fail-closed；
- `failed` 或非零终态必须结合结构化 reason 和明确 provider/model/network/auth 文本判断，
  不得扫描通用 header 后改写真实根因。

如果再次出现 `Not a git repository`，必须保留对应 execution、完整 argv、目标 fixture
repo identity 和产生 warning 的具体诊断；仅凭 warning 不得假定 Vega 传错 reviewer cwd。

## 11. 安全不变量

以下任一条件出现，Gate 4.5 必须为 `fail`：

- worker start 不是恰好 1；
- worker execution artifact 不是恰好 1；
- reviewer execution artifact 不是恰好 1；
- changed 或 untracked files 越界；
- verification failure 被升级为 success；
- success 仍存在 eval failure；
- acceptance evidence、artifact integrity 或 evidence freshness 不可信；
- Graph State、checkpoint manifest、SQLite 或 run-status 不可信；
- checkpoint、WAL、journal 与 manifest 出现不一致；
- LangGraph success 缺少 `final-report.md`；
- 低风险 Case 产生 decision、pending 或 consumption；
- HITL Case 的 fault、pending、decision 或 consumption identity 不一致；
- recovery 重复启动 worker 或重复未知外部副作用；
- A/B fixture HEAD 或核心成功语义不一致；
- 证据被旧 session、旧 snapshot 或其他 run 污染；
- Runner active、未知终态或 termination 未确认时仍发生自动重试。

## 12. 结论合同

### `pass`

只有以下条件全部成立：

- 内置 preflight 通过；
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

## 13. 数据出站与持久化边界

允许发送给 provider：

- 本合同定义的隔离 slug fixture；
- fixture 内的 `AGENTS.md`、README、源码、测试和 `.vega.yaml`；
- Vega 为该 fixture 生成的 prompt、冻结验收证据、diff 摘要、verification、risk 和 review
  evidence。

禁止发送：

- 其他项目源码、diff 或文档；
- R0、R1、R2、R3 的原始模型输出；
- 用户聊天记录；
- `.env`、key、token、Cookie 或 Authorization header；
- Codex credential store；
- 用户目录、SSH key、浏览器状态或云凭证；
- Vega Memory ledger；
- 真实业务数据。

raw evidence 固定在：

```text
.local-validation/gate-4.5/<r4-session>/
.tmp/langgraph-fixtures/gate-4.5/<r4-session>/
runs/<r4-business-run-id>/
```

raw evidence、fixture、run、SQLite、日志和认证信息不得提交 Git。Git 只提交本合同、最终结果
文档和 Gate 通过后允许的实现。

## 14. 预注册执行命令

将 `<execution-baseline-short-sha>` 替换为包含本文档且已经推送的执行基线短 SHA：

```powershell
.tmp\gate3-venv\Scripts\python.exe scripts\langgraph_core_dogfood.py `
  --runner real `
  --ignore-user-config `
  --windows-sandbox-session-override elevated `
  --session real-core-r4-business-20260717-<execution-baseline-short-sha> `
  --expected-provider openai `
  --expected-codex-version 0.144.5 `
  --model sandbox-model `
  --worker-reasoning high `
  --reviewer-reasoning high `
  --preflight-timeout-seconds 180 `
  --timeout-seconds 900 `
  --actor owner-delegated-codex
```

不得追加 `--preflight-only`、`--allow-dirty`、`--runner-profile` 或其他未预注册参数。

执行前后必须记录：

```powershell
git status --short --branch
git rev-parse HEAD
git rev-parse origin/experiment/langgraph-comparison
codex --version
.tmp\gate3-venv\Scripts\python.exe --version
```

只有 HEAD、远端 SHA、本文档、Codex 版本、Python 环境和工作区状态全部一致，才允许使用本次
结果更新 Gate 4.5 结论。

## 15. 结果证据

至少生成：

```text
.local-validation/gate-4.5/<r4-session>/summary.json
.local-validation/gate-4.5/<r4-session>/preflight-result.json
.local-validation/gate-4.5/<r4-session>/REPORT.md
.local-validation/gate-4.5/<r4-session>/preflight/execution/execution.json
runs/<r4-business-run-id>/state.json
runs/<r4-business-run-id>/trace.jsonl
runs/<r4-business-run-id>/iterations/*/executions/worker/execution.json
runs/<r4-business-run-id>/iterations/*/executions/reviewer/execution.json
runs/<r4-business-run-id>/iterations/*/review/acceptance-evidence.json
```

最终必须新增：

```text
docs/experiments/langgraph-orchestration/GATE-4.5-R4-DOGFOOD-RESULT.md
```

最终文档必须逐 Case 记录：

- execution 数量与结构化终态；
- scope、verification 和 reviewer verdict；
- acceptance evidence、integrity 与 freshness；
- Graph 控制面与 checkpoint；
- HITL pending、decision 和 consumption；
- transport warning、耗时和最终分类。

不得只引用 harness 聚合标签，也不得用 R3 或其他 session 的成功子证据补齐 R4。
