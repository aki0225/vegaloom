# Gate 4.5 Core Dogfood 预注册合同

> 文档状态：`frozen-before-real-run`
>
> 日期：2026-07-16
>
> 分支：`experiment/langgraph-comparison`
>
> Gate 3/4 基线：`private-gate-3-4-recovery-hitl-redacted`
>
> 执行基线：包含本文档、fixture 生成器和 dogfood harness 的首个干净提交；真实运行结果中记录
> 其完整 SHA，执行期间不允许修改 Runtime 或通过标准。

---

## 1. 研究问题

Gate 4.5 不再新增编排功能，只回答：

1. 相同真实任务在 Linear 与 LangGraph 下能否达到一致的成功语义；
2. 真实 worker 已修改 workspace 后发生预注册 crash，LangGraph 是否会重复启动 worker；
3. crash recovery 后能否继续 verification、进入结构化 HITL、消费一次批准并完成单 reviewer；
4. 真实模型延迟、输出质量、人工步骤和 Graph 控制面成本是否仍值得进入 Gate 5。

本 Gate 不验证并行 reviewer、Goal/Handoff、Memory、FastAPI 或 Vega self-dogfood。

## 2. 环境冻结

### 2.1 Runtime

```text
branch = experiment/langgraph-comparison
gate_3_4_baseline = private-gate-3-4-recovery-hitl-redacted
python = .tmp/gate3-venv/Scripts/python.exe
codex_cli = 0.144.5
memory = off
reviewer_count = 1
```

真实执行前必须：

- Git 工作区 clean；
- 当前分支与本文档一致；
- 记录执行 HEAD；
- fixture repo 与 `runs/` control root 互不包含；
- 不切分支；
- 不读取 `.env` 或其他凭证；
- 不修改 Vega Runtime 后继续沿用同一组结果。

### 2.2 模型与 Provider

2026-07-16 已执行只读 model probe：

```text
model = gpt-5.6
provider label = sandbox-provider
worker reasoning effort = high
reviewer reasoning effort = high
session persistence = ephemeral
probe result = MODEL_PROBE_OK
```

Codex CLI 同时报告：

```text
Model metadata for `gpt-5.6` not found. Defaulting to fallback metadata.
```

该 warning 作为运行环境事实保留。它不自动导致 blocked，但如果真实执行出现模型能力、计费、
上下文或协议异常，不允许静默改用其他模型继续合并结果。

## 3. 数据与外部副作用边界

允许发送给 provider 的内容：

- 本文档定义的独立 slug fixture；
- fixture 内的 `AGENTS.md`、README、源码和测试；
- Vega 为该 fixture 生成的 prompt、diff 摘要、verification、risk 和 review evidence。

禁止发送：

- 其他项目源码；
- `.env`、API key、Authorization header、Cookie；
- 用户目录、SSH key、浏览器状态、云凭证；
- Vega Memory ledger；
- 真实业务数据。

允许的外部副作用：

- 最多 3 次真实 worker session；
- 最多 3 次真实 reviewer session；
- Codex provider 网络请求；
- fixture 内 `src/slugify.py` 的修改；
- 当前项目 `runs/`、`.tmp/` 和 `.local-validation/` 内的实验 artifacts。

禁止：

- commit 或 push fixture；
- 修改测试、策略、依赖或其他 fixture 文件；
- 网络访问其他业务系统；
- provider error 后自动重试；
- 第二个 worker attempt 覆盖未知副作用现场。

## 4. Fixture 合同

所有 fixture 都是项目内 `.tmp/langgraph-fixtures/gate-4.5/<session>/` 下的独立 Git repo。

任务：

```text
实现 src/slugify.py::normalize_slug
只允许修改 src/slugify.py
不得增加依赖、修改测试或提交 Git
```

验收命令：

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

## 5. 预注册 Case

### Case A：Linear 低风险对照

```text
fresh low-risk fixture A
-> real worker
-> deterministic verification
-> single real reviewer
-> finish
```

预期：

- `status=success`；
- `finish_status=ready_to_commit`；
- changed files 只有 `src/slugify.py`；
- verification passed；
- worker start 为 1；
- reviewer execution 为 1。

### Case B：LangGraph 低风险对照

```text
fresh low-risk fixture B
-> real worker
-> LangGraph sequential
-> deterministic verification
-> single real reviewer
-> Graph terminal report
```

A/B 的 fixture commit 必须相同。预期：

- Case A 除 Linear Finish 之外的全部业务成功条件成立；
- `finish_status=not_applicable_langgraph`，不调用仅支持 Linear 的 `FinishRuntime`；
- Graph State、checkpoint manifest 和 SQLite 可校验；
- `run-status` 可消费可信 Graph 终态，`final-report.md` 存在；
- worker start 为 1；
- 没有 pending decision；
- 不要求生成字节完全一致的实现，只要求验收、scope 和成功语义一致。

### Case C：LangGraph Crash + HITL

```text
fresh high-risk fixture C
-> real worker
-> fault = after_step_result_before_state
-> 新 Runtime recover
-> verification
-> risk interrupt
-> decision ledger approval
-> resume by decision_id
-> single real reviewer
-> Graph terminal report
```

项目 owner 已在当前会话明确授权代理把该隔离 fixture 实验推进到结论。pending artifact 仍必须
在写 decision 前由执行代理读取和校验；ledger actor 记录为
`owner-delegated-codex`，不得伪写为直接人工点击。

预期：

- fault injector 命中；
- recover 前后 worker start 始终为 1；
- external worker execution artifact 始终为 1；
- pending identity 可信；
- decision ledger entry 为 1；
- consumption artifact 为 1；
- reviewer execution 为 1；
- verification passed；
- 最终 success，Graph State、checkpoint、`run-status` 和 `final-report.md` 可信；
- `finish_status=not_applicable_langgraph`。

## 6. 停止与分类

### `pass`

- 三个 Case 全部满足预注册预期；
- A/B 成功语义一致；
- duplicate worker / external effect / consumption 为 0；
- 无 silent workspace、policy 或 evidence drift；
- 真实 HITL 闭环可审计；
- 没有未关闭 Blocker / High。

### `partial-pass`

- deterministic 安全不变量全部成立；
- 但真实模型质量、reviewer 质量、操作成本或 Runtime 开销使完整可选 engine 收益有限；
- 必须写清楚保留 checkpoint/HITL 的哪些部分。

### `blocked`

- provider unavailable、model metadata/fallback 导致协议异常；
- 单次 runner timeout；
- 真实 runner 环境或身份无法确定；
- 证据不完整，无法判断；
- 不允许通过换模型、重试或放宽标准消除 blocked。

### `fail`

- duplicate worker start 或重复外部副作用；
- unsafe resume；
- verification failed 被升级为 success；
- scope 越界后仍 success；
- Graph/ledger/consumption identity 不一致却继续；
- LangGraph success 缺少可信 Graph State、checkpoint manifest 或 terminal report；
- A/B 核心业务成功语义被 Runtime 破坏。

## 7. 指标与证据

每个 Case 记录：

- Vega branch 和 HEAD；
- fixture commit；
- model、provider label、reasoning effort；
- run id；
- elapsed seconds；
- state/current step；
- worker/reviewer execution count；
- verification status/failed count；
- changed/untracked files；
- Graph State 和 checkpoint manifest；
- checkpoint size；
- decision/pending/consumption count；
- artifact integrity/freshness；
- integrity/freshness 的 issue 明细；
- finish status；
- 原始失败或 warning。

最终输出：

```text
.local-validation/gate-4.5/<session>/summary.json
.local-validation/gate-4.5/<session>/REPORT.md
docs/experiments/langgraph-orchestration/GATE-4.5-DOGFOOD-RESULT.md
```

fake harness 结果只证明 harness 可运行，不进入真实模型质量结论。
