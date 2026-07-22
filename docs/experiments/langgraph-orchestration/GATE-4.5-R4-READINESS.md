# Gate 4.5 R4 Readiness

> 复审日期：`2026-07-17（星期五）`
>
> R3 历史结论：`blocked`，保持冻结
>
> R4 确定性准备：`ready to freeze pre-registration`
>
> R4 真实执行：`not started`
>
> Gate 5：`not approved`

---

## 1. 结论

R3 暴露的两项确定性问题已经完成根因修复和回归验证：

1. Gate 4.5 harness 不再根据通用 `provider/model/codex` header 猜测 provider failure，
   而是优先读取 worker/reviewer 的结构化 `execution.json` 终态；
2. 隔离 reviewer 获得从冻结 Git HEAD 读取的有界验收证据包，能够看到根 README、Agent
   Brief 显式引用的需求/测试文件，以及与变更文件同名的 tracked 需求文档和基线测试。

当前结论是：

```text
R4 deterministic readiness = ready
R4 real preflight = not started
R4 business cases = not started
Gate 4.5 = blocked by pending R4 real evidence
Gate 5 = not approved
real provider calls in this readiness phase = 0
```

这表示代码和本地证据已经足以冻结独立 R4 预注册合同，但不表示 Gate 4.5 已通过，也不允许
直接进入 Gate 5。

## 2. 日期证据纠正

本次复审的唯一当前日期是：

```text
2026-07-17（星期五）
```

R3 中出现的 `20260717-*` run 和 artifact 是 2026 年 7 月 17 日凌晨正常生成，不是未来
时间戳。R3 结果文档把系统日期写成 `2026-07-16`，并据此提出“未来时间戳”风险，属于当时
AI 会话日期混乱造成的错误解释。

本轮采用以下处理：

1. 不回写 `GATE-4.5-R3-DOGFOOD-RESULT.md`，保持历史结果和当时判断冻结；
2. 本文显式记录日期纠正，后续不得再把 `20260717-*` 当作 future evidence；
3. 不新增 UTC/local/offset/clock-source 协议，也不新增未来时间戳或跨时区恢复测试；
4. evidence freshness 继续以 workspace fingerprint、run identity、artifact hash 和结构化
   execution 事实为准，不以 run id 中的日期单独判定。

因此，R3 的 `blocked` 结论仍然有效，但“未来时间戳”不再是 R4 的工程 blocker。

## 3. Provider failure 结构化分类

### 3.1 实现

`scripts/langgraph_core_dogfood.py` 新增 `RunnerExecutionFact`，从每个 worker/reviewer 的
`execution.json` 提取：

```text
status
returncode
reason
termination_unconfirmed
parse_error
```

Case 汇总同时记录：

```text
worker_status
reviewer_status
runner_executions
```

分类规则改为：

- `timed_out`、`stopped`、active/non-terminal execution、无法解析的 execution 或
  `termination_unconfirmed=true`：fail-closed，归为外部 Runner 阻塞；
- `completed / returncode=0`：不得仅因正常 header 或 transport warning 判错；
- `failed` 或非零终态：再结合结构化 reason、Runner 当前步骤和明确的 provider/model/
  network/auth failure 文本判断；
- reviewer 正常完成但返回 `needs_human`：属于业务审查结论，不是 provider failure；
- WebSocket 超时后成功回退 HTTPS 且 execution 最终成功：不是 provider failure。

### 3.2 回归覆盖

Gate 4.5 harness 已覆盖：

1. 正常 provider/model header + reviewer `needs_human`；
2. successful WebSocket → HTTPS fallback；
3. 明确 provider/model unavailable；
4. Runner timeout；
5. owned process termination 未确认。

本轮使用项目内 LangGraph 环境执行完整 harness，结果不是 skip：

```text
Gate 4.5 harness = 38 passed
```

## 4. 有界验收证据包

### 4.1 选择边界

reviewer acceptance evidence 按以下优先级选择：

1. Agent Brief 显式引用的 tracked 需求或测试文件；
2. 仓库根 README；
3. 与 changed file 同名的 tracked 需求文档；
4. 与 changed file 同名的 tracked 基线测试。

证据只从 review 时冻结的 Git HEAD 读取，不读取 worker 修改后的需求正文，也不递归注入整个
仓库。

### 4.2 预算和安全边界

默认配置：

```yaml
prompt_budget:
  reviewer_acceptance_max_chars: 20000
```

额外边界：

```text
最多文件数 = 8
单文件最大注入字符数 = 8000
敏感路径 = 拒绝读取
总内容字符数 = reviewer_acceptance_max_chars
```

每个 item 保存：

```text
path
revision
selection_reason
source_chars
included_chars
source_sha256
included_sha256
truncated
content
```

其中 `source_sha256` 绑定完整脱敏内容，`included_sha256` 绑定实际注入内容。

### 4.3 Artifact 和消费边界

新增：

```text
acceptance-evidence.md
acceptance-evidence.json
```

它们已经进入：

- standalone review pack；
- reviewer prompt metrics；
- `review-context.json` 的无正文 manifest；
- loop iteration 的本地证据副本；
- review freshness；
- loop artifact integrity；
- Finish evidence freshness。

证据包发生截断或候选省略时，即使模型返回 `approve`，runtime 也会强制降级为
`needs_human / evidence_truncated`。

新增篡改回归证明：

1. child review 的 `acceptance-evidence.json` 内容被修改后，
   `validate_review_evidence_freshness()` 返回 stale；
2. loop iteration 本地副本被修改后，Finish 的 artifact integrity 返回 invalid。

### 4.4 配置快照边界

复审过程中发现并修复了一条真实回归：standalone review 曾把工作树中未跟踪的
`.vega.yaml` 作为 prompt 配置传入 `project-context.md`。

最终边界是：

- standalone review 的 Runner 可以按现有行为读取当前执行配置；
- reviewer 上下文和验收证据默认从冻结 HEAD 加载配置与 tracked 内容；
- 只有 auto loop 显式传入启动时冻结的 `project_config` 时，review 阶段才复用该配置快照；
- 未跟踪或 worker 中途改写的 `.vega.yaml` 不得进入 reviewer prompt。

现有 `test_reviewer_prompt_excludes_untracked_file_content` 已锁定该边界。

## 5. `Not a git repository` 复审

本轮没有修改 reviewer cwd。

确定性代码和测试已经证明：

1. `ReviewRuntime` 向 Runner 传入 `repo_path.resolve()`；
2. Codex Runner 使用该路径生成 `codex exec --cd <resolved repo>`；
3. 新增测试锁定 reviewer 收到的就是目标 fixture Git 仓库。

现有证据不足以证明 R3 的 `Not a git repository` warning 来自 Vega 传错 cwd。它更可能来自
Codex 会话内部某次工具调用或命令上下文。为避免补丁式修复，本轮保持现有 cwd 逻辑；如果 R4
再次出现该 warning，必须同时保存对应 execution、完整 argv、目标 repo identity 和产生
warning 的具体工具调用，再决定是否修复。

## 6. 本轮验证范围

本轮只进行本地确定性验证：

- 不调用真实模型；
- 不读取 credential store；
- 不修改全局 provider、model、profile 或 sandbox；
- 不创建 R4 真实 preflight 或业务 session；
- 不实施 Gate 5 parallel reviewer。

最终明确通过的节点：

| 验证范围 | 结果 |
| --- | ---: |
| Gate 4.5 harness（项目 LangGraph venv） | `38 passed` |
| 直接相关 review/loop smoke | `12 passed` |
| Evidence freshness | `20 passed` |
| Finish artifact integrity | `19 passed` |
| Review artifact integrity | `18 passed` |
| Runtime safety integration | `18 passed` |
| P0 regressions | `12 passed` |
| Security evidence | `14 passed` |
| LangGraph decision binding | `15 passed` |
| Linear / LangGraph semantic parity | `15 passed` |
| Project config hardening | `3 passed` |
| Redaction | `28 passed` |
| Workspace snapshot budget | `8 passed` |
| Path / eval contract | `22 passed` |
| Execution control safety | `12 passed` |
| **合计** | **`254 passed`** |

其中：

```text
test_five_round_linear_and_graph_programs_remain_equivalent
= 1 passed in 124.53s
```

该节点已经是不可再拆的完整 node id，因此作为独立慢测执行。探索性组合分片中出现的 timeout
和共享 pytest cache 争用均未计入通过数；相关进程已清零，直接相关节点已使用独立
`--basetemp` 和 `cache_dir` 重跑。

静态验证：

```text
python -m compileall src = passed
ruff check src tests scripts = passed
git diff --check = passed
```

本轮不宣称整个非 LangGraph core suite 或全部 experimental suite 已一次性全量通过；
readiness 结论只使用上表中明确完成的受影响与支撑范围。timeout、skip 或历史日志不能替代
本轮 passed 证据。

## 7. 仍未关闭的风险

1. R4 尚未获得新的真实 provider、worker、reviewer 和 transport 证据；
2. Windows abrupt process exit、SQLite/WAL 和 checkpoint manifest 的真实硬退出风险仍在；
3. 验收证据选择是有界启发式，不声称覆盖仓库全部需求；发生截断时必须人工处理；
4. transport fallback 的延迟和稳定性仍需 R4 真实 session 评估；
5. R3 raw evidence 不得复用为 R4 任一业务 Case；
6. Gate 5 三路 reviewer 的隔离、reducer 和边际收益仍未实现、未验证。

## 8. R4 准入顺序

完成本 readiness 后，下一步只能按以下顺序推进：

1. 复审并提交本轮确定性修复与 readiness；
2. 以该干净 commit 的完整 SHA 冻结独立 R4 pre-registration；
3. 使用新 session、新 fixture、新 run 和新外部调用预算；
4. 先执行唯一一次 R4 preflight，不自动重试，不切换 provider/model/reasoning/sandbox；
5. preflight 通过后，再执行 Linear、LangGraph low-risk、LangGraph crash + HITL 三个业务
   Case；
6. 三个 Case 全部 passed 且所有安全不变量成立，Gate 4.5 才能改判 `pass`；
7. 只有 Gate 4.5 `pass`，才允许进入 Gate 5 实现。

## 9. Readiness 判定

```text
provider classifier deterministic fix = ready
acceptance evidence deterministic fix = ready
reviewer cwd patch = not justified
time-system patch = rejected as unnecessary
R4 pre-registration = approved to prepare
R4 real execution = not approved by this document
Gate 5 = not approved
```

R4 的下一项工作不是继续加编排功能，而是先完成本轮全量受影响验证、形成干净提交，再冻结
独立预注册合同。真实调用必须等待该合同明确记录基线 SHA、预算、provider/model identity 和
停止条件。
