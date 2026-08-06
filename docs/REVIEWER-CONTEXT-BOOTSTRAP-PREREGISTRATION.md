# Reviewer Context Bootstrap 对照实验预注册

> 实验 ID：`RCB-01`
>
> 状态：`preregistered / not-run`
>
> 登记日期：2026-08-06
>
> Vega Runtime 代码基线：`bec82840ae9e3815b860686095c531418c011821`
>
> 文档登记基线：`6fa3f919fc4d637055918f2449878f9851669ea3`
>
> 预注册提交：以首次包含本文的 Git 提交为准

## 一、实验目的

当前 Reviewer 已获得任务、项目规则、`project-context.md`、完整变更文件清单、Diff、验证证据
和目标仓库只读视图。现有文件覆盖门禁能够阻止 Reviewer 在 `reviewed_files` 中漏报变更文件，
但不能证明 Reviewer 已经理解未修改的调用方、被调用方、相邻测试、配置和公共契约。

本实验只回答一个问题：

> 在任务、模型、预算、Core Review Pack 和只读仓库完全相同的前提下，增加一份由 Git 事实
> 确定性生成的影响面候选清单，并要求 Reviewer 在输出 Verdict 前做一次有目标的只读检查，
> 是否能更稳定地发现依赖 Diff 外上下文的真实缺陷，同时不显著增加误报、耗时和 Token？

实验结论只允许为：

- `candidate-for-opt-in`
- `continue-experiment`
- `reject`
- `insufficient-evidence`

即使结果达到 `candidate-for-opt-in`，也只能进入默认关闭的 opt-in 或 shadow 方案讨论，不能
直接修改默认 Reviewer。

## 二、固定边界

1. 不向 Reviewer 传递 Worker 的完整聊天、内部推理、操作过程或未经验证的成功叙事。
2. A、B 两组使用同一份 `project-context.md`；B 组不重复生成“项目稳定地图”。
3. 不要求模型通读全仓，不建立向量数据库、知识图谱、通用 AST 平台、语言服务器索引或
   常驻服务。
4. 不增加第二个 Reviewer，不复用长生命周期会话，不改变 `ReviewVerdict` Schema。
5. 不新增 Runtime、CLI、默认状态、成功条件或第二套 Diff、Evidence、Risk 裁决。
6. 不把 Reviewer 的自述当作已读取文件的证据；优先使用执行 Trace，无法证明时记录
   `unknown`。
7. 本实验不验证系统级安全隔离、跨仓库泛化、模型间泛化或生产安全。
8. 本文形成可审阅提交前，不实现 B 组，不启动任何正式模型调用。

## 三、共同输入与运行参数

### 3.1 固定 Runtime 与目标仓库

- 控制端使用 `bec82840ae9e3815b860686095c531418c011821` 的 Reviewer Prompt、输出 Schema、
  Redaction 和只读 Runner 合同。
- 五个案例都来自 Vega 自身的历史变更。每次运行使用对应 `candidate` 修订的独立只读
  worktree，目标 Diff 固定为 `base..candidate`。
- accepted memory 关闭；不得把本次实验的 Golden、oracle 修订、其他运行输出或人工评分
  注入 Reviewer。
- 每个案例只物化一次 Core Review Pack 和验证证据，随后在该案例的四次运行中复用。

### 3.2 固定模型与预算

```text
model = gpt-5.6-sol
reasoning_effort = high
timeout = 900s
reviewer_diff_max_chars = 50000
core_pack_max_chars = 100000
context_appendix_max_chars = 20000
total_prompt_max_chars = 120000
repetitions_per_case_arm = 2
```

当前默认 Reviewer 字符预算不足以让全部历史案例同时保留 Full Diff。为避免把“Diff 是否被
截断”混入实验变量，A、B 两组统一使用上面的实验预算。除预算外，A 组沿用代码基线的 Reviewer
行为。

任何 Core Pack、Full Diff 或 Appendix 发生截断，该次运行都不进入有效样本。B 组不得通过
删减任务、项目规则、Full Diff、验证或风险证据为 Appendix 腾出空间。

首个模型调用前必须确认指定模型和推理强度可用。若不可用，立即停止并先追加预注册修订；
不得在运行中途换模型、降低推理强度或用其他模型补样本。

### 3.3 Core Review Pack

每个案例至少冻结以下材料：

- 本文中登记的任务文本；
- `base`、`candidate`、Diff 文件清单、原始 Diff 字节数和 SHA-256；
- candidate 修订中的项目规则与 `project-context.md`；
- candidate 修订的验证命令、原始退出状态、摘要和日志哈希；
- 当前 Review Prompt、输出 Schema、风险规则和只读执行合同；
- Core Review Pack 的 UTF-8 原始字节 SHA-256。

每个案例的 A、B 两组必须使用字节完全相同的 Core Review Pack。B 组的 Context Appendix
作为独立区段追加在 Core Pack 之后，不得改写 Core Pack。每次调用前都重新计算哈希；不一致
时停止该案例，不重新生成有利版本。

验证证据在首个模型调用前按 candidate 修订实际执行并冻结，同一案例四次运行复用同一结果：

```text
python -m compileall src scripts/check_repository_hygiene.py
python -m pytest <case-specific-tests>
ruff check <case-specific-python-paths>
git diff --check <base> <candidate>
```

每条命令使用 60 秒上限。`case-specific-tests` 和 `case-specific-python-paths` 冻结如下，
不得在看到 Reviewer 输出后增删：

`C1`：

```text
python -m pytest tests/test_smoke.py::test_finish_cli_summarizes_successful_loop tests/test_smoke.py::test_finish_report_preserves_missing_reviewer_line_and_verification_statuses -q
ruff check src/vega/finish_presentation.py src/vega/finish_runtime.py tests/test_smoke.py
```

`C2`：

```text
python -m pytest tests/test_p0_regressions.py::test_auto_worker_cannot_mutate_existing_ignored_content_before_verification tests/test_p0_regressions.py::test_auto_worker_cannot_mutate_git_control_files tests/test_smoke.py::test_loop_writes_project_context_into_worker_prompt tests/test_smoke.py::test_loop_auto_stops_on_workspace_pollution_before_review -q
ruff check src/vega/loop_prompts.py src/vega/loop_runtime.py src/vega/project_context.py src/vega/run_status.py tests/test_p0_regressions.py tests/test_smoke.py
```

`C3`：

```text
python -m pytest tests/test_workspace_snapshot_budget.py::test_workspace_check_excludes_same_repo_vega_run_artifacts tests/test_workspace_snapshot_budget.py::test_review_snapshot_ignores_owned_run_changes_but_keeps_other_untracked tests/test_workspace_snapshot_budget.py::test_workspace_check_keeps_non_owned_untracked_paths_fail_closed tests/test_workspace_snapshot_budget.py::test_verification_temp_root_rejects_logical_path_resolution_mismatch -q
ruff check src/vega/codex_workspace.py src/vega/git_inventory.py src/vega/workspace_check.py src/vega/workspace_inventory.py tests/test_workspace_snapshot_budget.py
```

`C4`：

```text
python -m pytest tests/test_cli_progress.py::test_execution_progress_is_stderr_only_and_uses_safe_step_labels tests/test_execution_control_safety.py::test_codex_exec_runner_emits_only_sanitized_jsonl_progress tests/test_execution_control_safety.py::test_codex_exec_runner_rejects_success_without_final_message tests/test_execution_control_safety.py::test_owned_process_observes_complete_lines_before_child_exit tests/test_execution_control_safety.py::test_output_reader_start_failure_terminates_owned_process tests/test_smoke.py::test_codex_exec_runner_builds_allowlisted_role_command tests/test_smoke.py::test_codex_exec_runner_executes_raw_command_but_redacts_result_command -q
ruff check src/vega/cli_support.py src/vega/execution_control.py src/vega/execution_output.py src/vega/runner.py tests/test_cli_progress.py tests/test_execution_control_safety.py tests/test_smoke.py
```

`C5`：

```text
python -m pytest tests/test_execution_control_safety.py::test_codex_exec_runner_writes_output_schema_inside_execution_dir tests/test_required_risk_review_runtime.py::test_low_risk_prompt_reserves_disclosures_for_gate_ids tests/test_required_risk_review_runtime.py::test_required_risk_schema_requires_exact_disclosure_count tests/test_smoke.py::test_loop_uses_separate_worker_and_reviewer_codex_options tests/test_smoke.py::test_auto_loop_keeps_start_time_reviewer_policy_after_worker_changes_config -q
ruff check src/vega/review_runtime.py src/vega/risk_review_reporting.py src/vega/risk_review_runtime.py src/vega/runner.py tests/test_execution_control_safety.py tests/test_required_risk_review_runtime.py tests/test_smoke.py
```

命令失败可以作为真实证据进入 Core Pack；只有环境未准备、命令未实际执行、输出损坏或哈希
不一致时，该案例才停止。物化提交只登记原始结果和哈希，不得改写上述命令。

## 四、A/B 唯一变量

### A 组：当前 Reviewer 行为

A 组只接收 Core Review Pack，并在 candidate worktree 的只读视图中完成现有审查。不得额外
提示它建立调用关系、检查影响面候选或先执行 Reconnaissance。

### B 组：Context Appendix

B 组在相同 Core Review Pack 后只追加：

1. 确定性生成的 `impact-candidates.json`；
2. 一段固定指令：在输出最终 JSON Verdict 前，先按候选顺序做一次有目标的只读
   Reconnaissance，并只读取与任务和 Diff 直接相关的必要文件。

`impact-candidates.json` 第一版字段固定为：

```json
{
  "schema_version": 1,
  "source_revision": "<candidate-sha>",
  "changed_files_sha256": "<sha256>",
  "generator_revision": "<experiment-implementation-sha>",
  "candidates": [
    {
      "path": "src/example.py",
      "role": "caller",
      "reason": "changed symbol is referenced here",
      "rank": 1
    }
  ]
}
```

`role` 只允许：

```text
caller
callee
test
config
contract
architecture
```

候选生成规则：

1. 只使用 `git ls-files`、`git grep`、仓库相对路径、文件命名、manifest 和简单 import
   启发式。
2. 不允许按案例手工写入 Golden 路径、oracle 修订路径或人工指定 seed。
3. 每个案例最多 12 个候选，按 `rank`、`role`、`path` 稳定排序；相同输入必须产生相同字节。
4. 只登记 candidate 修订中受 Git 跟踪的普通文件。
5. 排除 symlink、submodule 越界、二进制文件、超过 200,000 字节的文件、generated、vendor、
   依赖缓存和高置信凭据候选。
6. rename 或 delete 同时保留旧路径和新路径的关系，不跟随链接越过目标仓库。
7. 路径和原因先经过现有 Redaction，再进入 Appendix。
8. Appendix 必须记录自身 SHA-256，并绑定 candidate、Diff 和生成器修订。

本实验不向 `ReviewVerdict` 增加 `context_evidence` 字段。Reviewer 是否实际读取候选文件，
优先由执行 Trace 或 Runner 可核验事件判断；只有 Reviewer 自述时记录为 `claimed`，没有
可核验信息时记录为 `unknown`。

## 五、冻结案例

### 5.1 案例总表

| ID | 来源 | 类型 | Golden severity | base | candidate | oracle |
|---|---|---|---|---|---|---|
| `C1` | PR `#43` → `#44` | 上下文依赖正例 | `major` | `f7c8e853d58223339a5423f641d361758a4c5c46` | `3b25afc45cb67ce737cffa5021d3963d1fcba64b` | `0b14792c17c5722261d9a0f8293a4e43e588e09e` |
| `C2` | PR `#46` → `#47` | 上下文依赖正例 | `major` | `6c106c2f02c0e128770bf30d25e400ce88a8befe` | `7b1f283b4506902510e5b3d3f3e5caab91969f25` | `7d49f6ae83c5488616b9ca0088b0cb2428f65a72` |
| `C3` | PR `#33` 内部修正 | 上下文依赖正例 | `blocker` | `12a2f0d1529780e0c5f5423d57f76a80901a7808` | `dce7d35ad681a3efc1923a132e502dcaff2eabe6` | `721de1fc78700cd87986f423b673aa41b7aa3902` |
| `C4` | PR `#35` 初始补丁 | Diff 自足正例 | `blocker` | `1f68e3a8b3c224d9946f3cdb2aa8af19285a0c5b` | `0ceb78945c2ef3287cd9acdc53bde099939b679c` | `c46551d901b79869a3d7f6a2a8e4ece51f3c785b` |
| `C5` | PR `#48` | 安全负向对照 | 无已知 finding | `09c1001eee0b04f9ebbaaf8b7d3183aa7ac1c308` | `20461acb889870fa608b18f9d2eb8eff9f35d7ad` | 不适用 |

Diff 使用以下固定命令生成：

```text
git diff --no-ext-diff --no-renames --binary --full-index <base> <candidate> --
```

| ID | 变更文件数 | Diff 字节数 | Diff SHA-256 |
|---|---:|---:|---|
| `C1` | 8 | 46,902 | `5c1a1d4c054e6f9a383211f74ba29a03fbc891f551778fd30b66dfe3a9427849` |
| `C2` | 6 | 10,248 | `31ea589e9479ebd3f6c567db5b332fc28334ebe6886006c77baa02da50cf7be4` |
| `C3` | 4 | 8,396 | `be495418ccd5aec794acad7568dedcda3558fb66e32f32a2c48045c882a309a2` |
| `C4` | 11 | 34,281 | `c3a182b479115c5926fb146658f5721030ba9c8ebe6cdd313c7f347f06594123` |
| `C5` | 7 | 15,889 | `86229dd5e3cec182b007433afb7476fbd6a17837d27796b4e7acb26050e66fc0` |

Golden 和 oracle 修订只供控制端评分，不进入任何 Reviewer 输入。

### 5.2 `C1`：高风险审查下验证证据被误显示为未知

Reviewer 任务：

> 在不增加模型调用、命令、状态或第二套裁决的前提下，重排 Finish 第一屏，使用户能判断
> 实际变更、确定性 Gate、验证、Reviewer、证据限制和下一步；保持既有成功语义与高风险
> 人工确认语义。

Golden：

- 命中 `risk.required_reviews` 后，完整披露的 Reviewer verdict 会被固定为 `needs_human`。
- candidate 的 `validated_review_workspace_fingerprint()` 只为 `approve` 保留受审工作区指纹。
- 因此已经通过且与审查快照一致的验证，在 Finish 中会被显示为未知并要求补跑。

必须解释的影响：

- 高风险任务仍应交由人工确认，但不能把已经通过的确定性验证误报为缺失。
- 该错误会降低 Finish 第一屏的可信度，并诱导用户重复执行验证。

Diff 外必要上下文：

- `src/vega/loop_integrity.py:380-400`
- `src/vega/loop_evidence.py:739-744`

candidate Diff 中 `src/vega/finish_runtime.py:82` 只是消费
`trusted_verification_passed()` 的结果，不是根因。

### 5.3 `C2`：Prompt 约束不能阻止 Python `.pyc` 污染

Reviewer 任务：

> 收紧 Worker 自检边界，避免自检留下 ignored、未跟踪文件或 Git 状态变化，同时保留
> Vega 的固定验证、fail-closed、人工清理和 Finish 行为。

Golden：

- candidate 只在 Worker Prompt 中禁止副作用。
- Worker 仍可执行项目 Python 脚本并生成 ignored `.pyc`。
- 应从 Codex Runner 的进程环境注入 `PYTHONDONTWRITEBYTECODE=1`，不能只依赖模型遵守文本。

必须解释的影响：

- `.pyc` 会污染 Workspace Gate，并可能把本来有效的修改转成 `needs_human`。
- Prompt 是行为要求，不是可靠的进程级副作用控制。

Diff 外必要上下文：

- `src/vega/runner.py:311-322`
- `src/vega/execution_control.py:304-345`

candidate Prompt 位于 `src/vega/loop_prompts.py:82-88`。

### 5.4 `C3`：普通 verification 临时文件被错误隐藏

Reviewer 任务：

> 隔离 Codex 或 Vega 自己产生的 workspace 噪声，同时保持普通项目文件、未跟踪文件和
> ignored 文件的 fail-closed 检查，不扩大 harness-owned 豁免范围。

Golden：

- candidate 将 ignored 路径过滤器复用于 untracked 状态。
- `workspace_ignored_path_exclusions()` 总是包含 `.tmp/vega-verification`。
- 该目录下普通未跟踪文件因此也会被隐藏，放松了 fail-closed。

必须解释的影响：

- Worker 或其他进程可以把普通未跟踪文件留在验证临时目录而不被 Workspace Gate 发现。
- harness-owned ignored artifact 的排除规则不能直接复用于普通 untracked 状态。

关键位置：

- candidate Diff：`src/vega/codex_workspace.py:67-73`
- candidate Diff：`src/vega/workspace_check.py:171-175`
- 同一变更文件的未修改区段：`src/vega/workspace_inventory.py:192-202`

本案例用于确认“Diff 外上下文”也可能位于变更文件的未修改区段，不只存在于其他文件。

### 5.5 `C4`：无界实时输出拖延 timeout 和 stop

Reviewer 任务：

> 为 Codex JSONL 执行显示实时进度，同时保证原始输出完整落盘，并保持 timeout、stop、
> heartbeat 和最终消息解析的既有语义。

Golden：

- `ProcessOutputCapture` 使用无界 `queue.SimpleQueue`。
- `poll()` 会一直消费到队列为空。
- 高频输出或慢 observer 会让主控制循环长时间停留在 `poll()`，拖延 timeout、stop 和
  heartbeat，并持续占用内存。

关键位置全部位于 candidate Diff：

- `src/vega/execution_output.py:26`
- `src/vega/execution_output.py:51-65`
- `src/vega/execution_output.py:98-103`
- `src/vega/execution_control.py:391-396`

本案例不要求读取 Diff 外文件，用于检查 B 组是否只是在所有案例中增加文件数量，而没有
改善需要上下文的 finding。

### 5.6 `C5`：Reviewer 风险披露输出契约

Reviewer 任务：

> 加固 Reviewer 对必审高风险类别和完整变更文件清单的输出契约，保持现有 fail-closed、
> 风险披露和架构增长门禁，不增加新的默认 Runtime 或成功状态。

本案例没有冻结已知 finding，是安全负向对照。PR `#49` 解决的是后续独立的完整变更文件覆盖
能力，不能倒推为 PR `#48` 的已知缺陷。

若 Reviewer 找到新的、能够由 candidate 代码和项目规则证明的真实缺陷：

1. 不把它计为 false positive；
2. 不改写本预注册的 Golden；
3. `C5` 失去负向对照资格；
4. 本轮总结果固定为 `insufficient-evidence`，先独立复核新 finding。

## 六、固定运行顺序

每个运行都使用全新独立会话，不复用 session、缓存结论或其他运行输出。总调用数固定为：

```text
5 cases × 2 arms × 2 repetitions = 20
```

第一轮：

```text
01 C1-A1
02 C1-B1
03 C2-B1
04 C2-A1
05 C3-A1
06 C3-B1
07 C4-B1
08 C4-A1
09 C5-A1
10 C5-B1
```

第二轮反向：

```text
11 C5-B2
12 C5-A2
13 C4-A2
14 C4-B2
15 C3-B2
16 C3-A2
17 C2-A2
18 C2-B2
19 C1-B2
20 C1-A2
```

Provider 错误、timeout、无效 JSON 或终止未确认不自动重试，也不在末尾补跑替代样本。

## 七、评分规则

### 7.1 Golden 命中

Golden 语义匹配不要求逐字一致或精确到同一行号，但必须同时说明：

1. 错误行为；
2. 实际影响；
3. 可复核的代码位置或调用关系。

只说“可能有问题”“建议补测试”或复述任务要求，不计命中。严重级别与 Golden 不同但语义
完整时仍计命中，严重级别偏差单独记录。

主要命中指标是 `C1`、`C2`、`C3` 的 6 次上下文依赖机会。`C4` 单独记录，用于确认两组在
Diff 自足缺陷上的基础能力没有明显退化。

### 7.2 False positive

finding 只有在无法由 candidate 代码、项目规则、测试或可复核调用关系支持时才计 false
positive。措辞保守但有具体证据的 finding 不因不在 Golden 中自动算误报。

同一运行中对同一根因的重复表述合并为一个 finding；不同运行分别计数。

### 7.3 上下文与成本

每次运行记录：

- 是否命中 Golden；
- false positive 数及严重级别；
- `impact-candidates.json` 的候选数；
- 能由 Trace 证明实际读取的候选路径；
- Reviewer 声称读取但 Trace 无法证明的路径；
- `relevant_context_precision`；
- Runner 墙钟时间；
- Provider 返回的原始 Token 字段；
- 最终 verdict；
- `needs_human` 的具体原因。

`relevant_context_precision` 定义为：

```text
对正确判断提供直接证据的候选路径数 / Appendix 列出的候选路径数
```

Token 不可用时写 `unavailable`，禁止用字符数估算。`needs_human` 必须区分：

- 风险门禁强制人工；
- Reviewer 判断证据不足；
- Provider、timeout、无效输出或终止失败。

### 7.4 人工评分

- Golden 在首个模型调用前冻结。
- 评分时只使用 Reviewer 最终输出、candidate 代码、项目规则和可核验 Trace。
- 不读取 oracle 修订来寻找额外问题；oracle 只用于确认已冻结 Golden。
- 无法判定的新 finding 标记 `needs-adjudication`，不强行归入命中或误报。
- 所有 20 次运行完成或停止后再汇总，不根据中间结果调整 Prompt、候选规则或顺序。

## 八、结论门槛

### `candidate-for-opt-in`

必须同时满足：

1. B 相对 A 在 `C1`、`C2`、`C3` 的 6 次机会中多命中至少 2 次；
2. 增量命中覆盖至少 2 个不同的上下文依赖案例；
3. B 的 false positive 总数相对 A 最多增加 1；
4. B 的 Reviewer Token 中位数不超过 A 的 `1.5x`；
5. B 的 Reviewer 墙钟时间中位数不超过 A 的 `1.5x`；
6. `C4` 不出现稳定退化；
7. `C5` 不在两次 B 组运行中重复产生同一项无依据 `major` 或 `blocker` finding；
8. 20 次调用都形成可解析、可核验的终态。

Token 不可用、任一 arm 缺少有效终态或 `C5` 失去负向资格时，不得给出
`candidate-for-opt-in`。

### `continue-experiment`

出现可信改善，但样本完整性、成本字段、标签一致性或单一候选启发式仍不足。下一步只允许补
样本或修正一个已证明有问题的候选规则，不能同时扩大 Schema、Runtime 和工具链。

### `reject`

满足任一条件：

- B 没有增加上下文依赖 Golden 命中；
- 主要变化只是读取更多文件；
- 误报明显增加；
- Token 或耗时持续超过门槛；
- Diff 自足案例出现稳定退化。

结果为 `reject` 时停止实现，不通过增加向量库、知识图谱、多 Reviewer 或新 Runtime 挽救
假设。

### `insufficient-evidence`

包括但不限于：

- 模型、输入、Core Pack 或 candidate worktree 未按预注册冻结；
- A/B Core Pack 字节不一致；
- Full Diff 或必要证据被截断；
- 多个运行因 Provider 或环境失败而无法比较；
- `C5` 发现新的可证实真实缺陷；
- Golden 或评分规则在首个模型调用后被改变。

## 九、Artifact 与记录

原始运行只保留在忽略目录 `runs/` 或独立本地实验目录，不提交完整 Prompt、会话记录、本机
路径或 Provider 原始敏感元数据。每次运行至少保留：

- case、arm、repetition 和固定顺序编号；
- base、candidate、Runtime、生成器和模型配置；
- Core Pack、Diff、Appendix 及各自 SHA-256；
- candidate worktree 指纹；
- Runner 状态、时间、可用 Token 和终止信息；
- 原始 Reviewer 输出、解析后的 Verdict 和解析错误；
- 可核验的只读文件访问 Trace；
- Golden 命中、false positive 和人工评分记录。

公开结果如后续获准提交，追加到新的 `eval/` 证据记录中。不得改写本文、删除失败运行或只
保留有利样本。公开材料必须使用仓库相对路径并完成凭据、本机路径和用户信息扫描。

## 十、实施顺序

1. 先审阅并提交本文、`ROADMAP.md` 和文档导航；本提交不改 Runtime。
2. 下一提交只实现实验专用的 deterministic materializer、候选生成器和离线校验，不接入
   默认 CLI 或 Reviewer。
3. 在任何模型调用前物化五个案例，登记 case-specific 验证命令、全部 Artifact 哈希和模型
   可用性。
4. 若物化阶段发现本文存在错误，只能先追加带日期、原因和影响面的 amendment，并形成独立
   提交。
5. 依照固定顺序运行 20 次；失败不重跑、不换模型、不改 Prompt。
6. 完成盲化评分与独立复核后，再追加公开结果和路线结论。
7. 未达到 `candidate-for-opt-in` 时，不实现默认 Reviewer 变更。
