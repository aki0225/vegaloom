# Reviewer Context Bootstrap 实验记录

> 本文件只追加已经完成的 Reviewer Context Bootstrap 实验结果，不改写预注册协议，也不替代
> 原始运行 Artifact。实验输入、Golden、顺序和门槛见
> [`../docs/REVIEWER-CONTEXT-BOOTSTRAP-PREREGISTRATION.md`](../docs/REVIEWER-CONTEXT-BOOTSTRAP-PREREGISTRATION.md)。

## RCB-01：确定性影响面候选

> 运行日期：2026-08-06 至 2026-08-08
> 物化器提交：`4e195df3f27a9ce8037d9ba6ccbd173fdd8c0105`
> 冻结 Runtime：`bec82840ae9e3815b860686095c531418c011821`
> 正式裁决：`insufficient-evidence`

### 研究问题

在任务、模型、预算、Core Review Pack 和只读 candidate worktree 相同的条件下，B 组额外
获得最多 12 个确定性影响面候选，并被要求先做一次有目标的只读检查。实验验证这种 Context
Appendix 能否比当前 Reviewer A 组更稳定地发现依赖 Diff 外上下文的真实缺陷，同时控制误报、
耗时和 Token。

本实验不评价 Vega 的整体编码成功率，也不验证 Provider 无关性、系统级隔离或无人值守长任务。

### 协议执行情况

- 五个历史案例按预注册顺序执行 A、B 各两次，共登记 20 次全新 Reviewer 会话。
- 模型固定为 `gpt-5.6-sol / high`，只读 sandbox，单次 timeout 为 900 秒。
- 每个案例的 A、B 复用字节一致的 Core Review Pack；Golden 和 oracle 没有进入 Reviewer 输入。
- 20 次运行都写出终态记录，candidate worktree 前后指纹一致。
- 17 次形成有效 Reviewer verdict；三次 Provider 或模型失败按协议消费，没有补跑。
- 运行期间没有换模型、降低推理强度、修改 Prompt、Golden、候选规则或运行顺序。

无效终态分别是：

- `08-C4-A1`：上游 HTTP 503；
- `09-C5-A1`：上游 HTTP 503；
- `19-C1-B2`：部分只读调查后发生模型容量错误，Runner fail-closed。

### Golden 与成本

主要指标只计算 C1、C2、C3 的上下文依赖机会。C4 是 Diff 自足控制，C5 原计划作为安全
负向对照。

| 指标 | A | B | 结论 |
|---|---:|---:|---|
| 已登记运行 | 10 | 10 | 顺序完整，无补跑 |
| 有效 Reviewer 终态 | 8 | 9 | 两组均有 Provider 缺失样本 |
| 上下文 Golden 有效机会 | 6 | 5 | `C1-B2` 无有效终态 |
| 上下文 Golden 命中 | 0 | 0 | B 没有观察到增量命中 |
| 全部运行墙钟中位数 | 194.367s | 341.8515s | B/A = 1.7588x |
| 有效终态墙钟中位数 | 207.273s | 314.640s | B/A = 1.5180x |
| Token 可用样本中位数 | 282,209 | 724,358 | B/A = 2.5667x |

即使缺失的 `C1-B2` 命中 Golden，B 最多也只能比 A 多命中一次，仍低于预注册要求的至少
两次增量命中。B 的耗时达到或超过 `1.5x` 门槛，Token 明显超过该门槛。

C4 的有效 A2、B1、B2 都识别出无界输出队列可能拖延 timeout、stop 和 heartbeat 的 Diff
自足 Golden。现有有效样本没有显示 B 的基础 Diff 审查能力稳定退化。

有效输出中的非 Golden finding 经 candidate 代码和项目规则复核后，没有发现明确的无依据
false positive。这些额外发现不替代预注册 Golden，也不用于事后修改标签。

### 候选检索结果

三个上下文依赖案例共冻结五个必要路径：

- C1：`src/vega/loop_integrity.py`、`src/vega/loop_evidence.py`；
- C2：`src/vega/runner.py`、`src/vega/execution_control.py`；
- C3：`src/vega/workspace_inventory.py` 的未修改区段。

第一版生成器只列出 `src/vega/loop_evidence.py`，必要路径召回为 `1/5 = 20%`。主要缺口是：

1. 候选单位是文件，不能指出 changed file 中 Diff hunk 外的必要代码区段；
2. 直接 import 和文本引用不能覆盖 Prompt builder 到 Runtime、Runner、execution control 的
   多跳执行关系；
3. 固定 12 个文件通常都被 B 组读取，但候选排序没有把 Reviewer 引向真正缺失的关系。

因此，当前失败不是上下文数量不足，而是候选关系和粒度错误。继续增加文件数量只会扩大成本。

### C5 负向对照失效

C5 的三个有效运行 A2、B1、B2 都独立指出同一个 blocker：candidate 的 `ReviewVerdict`、
输出 Schema 和示例没有 `reviewed_files`，Runtime 也没有把 Reviewer 声明与可信 changed files
做确定性覆盖校验。

只读复核确认该缺陷由 candidate 代码和任务要求直接支持，不是根据后续 oracle 倒推。因此：

- 该 finding 不计为 false positive；
- 预注册 Golden 不被改写；
- C5 失去安全负向对照资格；
- 整轮实验按预注册只能判为 `insufficient-evidence`。

### 20 次运行登记

| Run | 有效终态 | Golden | Findings | 说明 |
|---|---|---|---:|---|
| `01-C1-A1` | 是 | 未命中 | 3 | - |
| `02-C1-B1` | 是 | 未命中 | 2 | - |
| `03-C2-B1` | 是 | 未命中 | 1 | - |
| `04-C2-A1` | 是 | 未命中 | 1 | - |
| `05-C3-A1` | 是 | 未命中 | 1 | - |
| `06-C3-B1` | 是 | 未命中 | 1 | - |
| `07-C4-B1` | 是 | 命中 | 3 | Diff 自足控制 |
| `08-C4-A1` | 否 | 不适用 | 0 | HTTP 503 |
| `09-C5-A1` | 否 | 不适用 | 0 | HTTP 503 |
| `10-C5-B1` | 是 | 不适用 | 2 | 负向对照失效 |
| `11-C5-B2` | 是 | 不适用 | 2 | 负向对照失效 |
| `12-C5-A2` | 是 | 不适用 | 2 | 负向对照失效 |
| `13-C4-A2` | 是 | 命中 | 3 | Diff 自足控制 |
| `14-C4-B2` | 是 | 命中 | 3 | Diff 自足控制 |
| `15-C3-B2` | 是 | 未命中 | 3 | - |
| `16-C3-A2` | 是 | 未命中 | 1 | - |
| `17-C2-A2` | 是 | 未命中 | 1 | - |
| `18-C2-B2` | 是 | 未命中 | 4 | - |
| `19-C1-B2` | 否 | 不适用 | 0 | 模型容量错误 |
| `20-C1-A2` | 是 | 未命中 | 3 | - |

### 裁决

正式裁决为 `insufficient-evidence`，因为存在三个无效 Reviewer 终态、C5 负向对照失效，且
Token 字段并非 20/20 可用。

方向性判断不依赖缺失样本：当前 Context Appendix 不支持进入 opt-in、shadow 或默认 Runtime。

- B 没有观察到上下文 Golden 增量命中；理论最大增量仍低于预注册门槛。
- 必要路径召回只有 20%。
- B 的 Token 约为 A 的 2.57 倍，耗时约为 1.52 至 1.76 倍。
- 默认 Reviewer、Verdict Schema、CLI 和成功语义保持不变。

本结果不证明“任何 Reviewer 上下文增强都无效”。它只否定当前以文件级 import、路径和文本
命中生成 Context Appendix 的实现。下一步只有在离线必要路径和代码区段召回先达到门槛后，
才允许提出新的模型对照实验。计划草案见
[`../docs/REVIEWER-CONTEXT-RETRIEVAL-OFFLINE-PLAN.md`](../docs/REVIEWER-CONTEXT-RETRIEVAL-OFFLINE-PLAN.md)。

### 证据边界

原始 Reviewer 输出、运行日志和 candidate worktree 保留在本地 ignored 验证目录，没有提交。
本记录来自全部 20 次登记结果的脱敏汇总，不包含凭据、本机绝对路径或原始模型正文。公开记录
能够审查样本完整性和裁决逻辑，但不能替代本地原始 Artifact 的逐字复核。

## RCB-02 Phase 0：关系可达性审计

> 审计日期：2026-08-08
> 阶段裁决：`stopped-before-holdout`

本轮只审计 C1-C3 的 Diff 种子到必要区段是否能由冻结的两跳 AST 关系解释，没有调用模型，
没有修改 Runtime，也没有读取或评分独立 Holdout。

审计结果：

- C1 的必要区段位于 `validated_review_workspace_fingerprint()` 和
  `_validate_iteration_review()`。从 changed `build_finish_summary()` 到这两个位置需要反向
  caller、`LoopArtifactIntegrity` 字段生产者/消费者及约五段调用关系，不符合普通两跳边界。
- C2 可由静态关系解释，但必须识别 `Runner` Protocol、`make_runner()` 工厂回退、具体
  `CodexExecRunner.run()` dispatch 和 `run_owned_process()`，简单同名调用不能作为高置信证据。
- C3 的真正必要调用点 `snapshot_workspace()` 没有被 candidate 修改，只因前方 import 增行而
  从 base 行号平移。它通过嵌套关键字实参取得 `workspace_ignored_path_exclusions()`，并非与
  changed `ignored_coverage_level()` 存在同文件语义关系。
- RCB-01 control 没有冻结 RCB-02 所需的机器可读 symbol/span 标签，正式区段评分合同不完整。

受限原型的预算、稳定输出、tracked control 和简单调用关系测试为 4 个通过；C1-C3 精确区段
断言为 3 个失败。通过提高关键词权重或把共享 import 当作因果边可以制造表面命中，但无法满足
“候选都有可复核关系链”的门槛，因此没有把原型提交为能力，也没有进入 Holdout。

这个结果不证明所有 Diff-driven 检索都无效。它证明当前“标准库 AST + 普通两跳 + 最多 8 个
区段”的复杂度预算无法覆盖已知开发集；继续实现需要先修正标签和重新预注册关系深度，不能在
看到开发集后直接放宽原协议。

## RCB-03：有界假设调查开发实验

> 运行日期：2026-08-08
>
> 开发集裁决：`reject-before-holdout`

本轮按预注册顺序使用 `gpt-5.6-sol / high / read-only` 完成 C1-C3 的六次调用。A 组只使用
RCB-01 Core Review Pack；B 组不接收候选文件，只追加“最多三个风险假设、最多 12 条只读
搜索/读取命令、最多读取 6 个 Diff 外完整文件”的固定指令。默认 Runtime、Reviewer Prompt、
Verdict Schema 和成功语义均未修改。

### 样本有效性

- 六次调用均产生可解析终态，`reviewed_files` 精确覆盖完整变更文件清单；
- 六个 candidate worktree 的 HEAD、tree、index 和 status 指纹前后一致；
- 六次进程终止均已确认，Token 字段均可用；
- 没有 Provider、timeout、无效 JSON 或工作区污染样本。

| Run | 终态 | 命令 | Diff 外完整文件 | 预算 |
|---|---|---:|---:|---|
| `01-C1-A1` | 有效 | 28 | 不适用 | A 组不受 B 组调查预算约束 |
| `02-C1-B1` | 有效 | 4 | 4 | 通过 |
| `03-C2-B1` | 有效 | 12 | 5 | 通过 |
| `04-C2-A1` | 有效 | 0 | 不适用 | A 组不受 B 组调查预算约束 |
| `05-C3-A1` | 有效 | 23 | 不适用 | A 组不受 B 组调查预算约束 |
| `06-C3-B1` | 有效 | 3 | 0 | 通过 |

`03-C2-B1` 的本地静态分类器曾给出预算不通过。逐条审计原始命令后确认这是分类器误报：它把
`rg` 搜索字面量里的路径当成完整文件读取，并把搜索文本中的 `git add`、`python -m pytest`
当成实际写入或执行。该次实际刚好使用 12 条命令，完整读取 5 个 Diff 外文件，没有运行测试、
构建、格式化或写入命令。原始 trace 未被改写，公开表格采用预注册合同对“完整文件读取”的
语义审计结果。

### Golden 与误报

| Case | A | B | 评分说明 |
|---|---|---|---|
| C1 | 未命中 | 命中 | B 明确指出高风险 `needs_human` 下已通过验证被 Finish 显示为缺少受信验证；A 未指出该问题。 |
| C2 | 未命中 | 未命中 | 两组都只指出完整验证证据不足，未识别 `.pyc` 与 Runner 进程环境问题。 |
| C3 | 未命中 | 未命中 | 两组都发现了 porcelain rename/copy 第二路径误判的真实新边界，但未命中 untracked/ignored 共用排除集合的 Golden。 |

B 命中 `1/3`，A 命中 `0/3`，增量只有 1 次。候选 revision 中的其他 finding 均可由源码、
项目规则或缺失的必跑验证证据支持；人工复核后的 false positive 为 A=`0`、B=`0`。C3 的
rename/copy finding 是非 Golden 的真实发现，不用于事后修改冻结标签，也不算作 Golden 命中。

### 成本

Token 采用最后一个 `turn.completed` 的 `input_tokens + output_tokens`，墙钟时间使用 Runner
记录值。

| 指标 | A 中位数 | B 中位数 | B/A |
|---|---:|---:|---:|
| Token | 682,980 | 526,043 | 0.7702x |
| 墙钟时间 | 332.266s | 296.531s | 0.8925x |

中位成本低于 `1.5x` 门槛，但单案方差很大：C2-B 为 1,071,938 tokens / 238.781s，C2-A 为
51,601 tokens / 40.687s。有界指令没有稳定地把调查成本限制在每个案例的相近区间。

### 裁决

正式裁决为 `reject-before-holdout`：

- B 只命中 `1/3` Golden，低于至少 `2/3`；
- B 相对 A 只增加 1 次命中，低于至少 2 次；
- false positive、只读预算和中位成本门槛均通过，但不能替代命中门槛。

因此不冻结新 Holdout，不把本 Prompt 接入 opt-in、shadow 或默认 Reviewer，也不通过增加静态图、
检索服务、更多提示层或第二 Reviewer 挽救该方向。结果只否定本轮“让 Reviewer 自主形成最多三个
风险假设”的固定协议，不证明所有项目上下文增强都无效。后续回到真实日常使用观察；只有出现
新的、可重复且无法由当前 Core Review Pack 处理的真实审查失败，才重新提出独立预注册。

### 证据边界

原始 JSONL、模型正文、candidate worktree、freeze 和人工评分保留在本地 ignored 验证目录，
不提交本机路径、凭据或原始模型内容。公开记录包含足以复核样本完整性、预算口径、评分和裁决的
脱敏汇总，但不能替代本地原始 Artifact 的逐条审计。
