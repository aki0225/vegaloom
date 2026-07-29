# Vega 日用价值配对实验 V1

状态：实验合同已冻结；DV-B01 已因缺少上游绿态 oracle 退休，DV-B02 与替代案例 DV-B04
已达到 `runnable`。DV-B04 的 Native treatment 已发生一次基础设施失败；DV-B02 的 Vega
与 Native treatment 已按冻结顺序各正式运行一次，均超时且未启动 Reviewer。

## 1. 要回答的问题

本实验不再证明 Vega 能否启动 Worker、Reviewer 或保存 artifact；这些机械能力已有测试和历史
运行证据。V1 只回答一个更窄的问题：

> 对同一个真实 Bug 或 Feature，Vega 相比原生 Codex 工作流，是否在不降低确定性质量的前提下，
> 减少人工搬运和遗漏，并提供值得日常承担的额外追溯价值？

六个样本只能形成方向性证据，不能形成普遍成功率结论。

## 2. 唯一自变量

每个 case 使用两个 treatment：

### A. `native`

- 使用原生 Codex Worker 会话实现任务。
- Worker 完成后，使用原生独立 Review 会话审查同一 diff。
- Reviewer 不接收 Worker 的完整聊天记录或自述，只接收预注册任务、项目规则、diff 和验证证据。
- 人工负责记录验证、时间、Token、操作数和最终结论。

### B. `vega`

- 使用 `vega do bug|feature --mode auto`，或同等的 `vega loop ... --mode auto`。
- Vega 编译项目上下文、启动 Worker、执行固定验证、启动隔离只读 Reviewer，并生成 Finish artifact。
- Reviewer 同样不接收 Worker 的完整聊天记录。
- 不启用 Multi-Worker、Trellis、Memory、Goal 或 A2A。

除上述编排方式外，两组必须保持一致：

- 相同 baseline commit；
- 相同任务合同；
- 相同模型和 reasoning effort；
- 相同 timeout；
- 相同允许修改路径；
- 相同确定性验证命令；
- 相同 Reviewer 检查合同；
- 每个 treatment 只允许一次正式运行。

## 3. Case 资格门

`eval/experiments/daily-value-validation/cases.jsonl` 是 append-only case ledger。每个 case
从 `revision=1` 开始；后续资格确认只能追加更高 revision，不能改写旧行。

初始候选统一使用：

```text
status=candidate_not_frozen
```

只有同时满足以下条件，新的 revision 才能标记为 `runnable`：

1. 固定 40 位 baseline commit。
2. 在 baseline 上运行预注册 verifier，结果为红。
3. 固定上游 oracle ref，并证明同一 verifier 为绿。
4. 固定任务合同、允许修改路径和验证命令。
5. 固定模型、reasoning effort 和 timeout。
6. 在 Windows 环境完成依赖安装与 verifier smoke。
7. Issue 内容没有把上游最终 patch 直接暴露给 Worker。

任一项未确认都保持 `candidate_not_frozen`。不得因为 Issue 已关闭或上游已有实现，就推断
该 case 当前可运行。

## 4. 当前候选

| Case | 类型 | 上游任务 | 执行顺序 | 当前状态 |
|---|---|---|---|---|
| DV-B01 | Bug | PyCQA/pycodestyle #1311，f-string 集合/字典推导式误报 E201/E202 | Native → Vega | retired |
| DV-B02 | Bug | python-attrs/attrs #1348，`optional(pipe(...))` 组合回归 | Vega → Native | runnable；两个 treatment 均已运行且超时 |
| DV-B03 | Bug | pallets/werkzeug #2364，`s_maxage` 整数 setter 异常 | Native → Vega | candidate_not_frozen |
| DV-B04 | Bug | pallets/click #2836，prompt 忽略字符串 `show_default` | Native → Vega | runnable；Native 基础设施失败 |
| DV-F01 | Feature | python-attrs/attrs #814，生成 `__match_args__` | Vega → Native | candidate_not_frozen |
| DV-F02 | Feature | pallets/werkzeug #2948，支持 RFC5861 Cache-Control 扩展 | Native → Vega | candidate_not_frozen |
| DV-F03 | Feature | pallets/click #805，`style` 支持删除线 | Vega → Native | candidate_not_frozen |

选择标准是任务行为可测试、修改面预计较窄、已有上游 oracle。这里的“预计”不代替资格门。
DV-B01 的真实资格确认推翻了“已有 oracle”的初始假设；DV-B04 已完成独立红绿验证并替代其
活跃 Bug 名额。历史退休记录仍保留，因此 ledger 当前有七个 case identity，但同时只有三个
活跃 Bug 和三个活跃 Feature。

## 5. 结果记录

每次正式 treatment 向独立的 append-only `results.jsonl` 追加一行。记录至少包含：

- `case_id`、`treatment`、`run_id`；
- `baseline_commit`、`model`、`reasoning_effort`、`timeout_seconds`；
- `run_status` 和 `final_disposition`；
- `verification_status`；
- `reviewer_verdict`；
- `reviewer_independent_findings`；
- `wall_clock_seconds`；
- input、output、cached input Token；
- `manual_actions`；
- `recovery_used`；
- `artifact_read`；
- 仓库相对 evidence 引用和必要说明。

定义：

- `verified_success`：最终结论为 `success`，且固定 verifier 为 `passed`。
- `false_success`：最终结论为 `success`，但固定 verifier 不是 `passed`。
- `reviewer_independent_findings`：Reviewer 首次提出，并经人工或 oracle 确认为有效的缺陷数。
- `manual_actions`：任务开始后，为推进、搬运、重启、复制证据或恢复而进行的人工操作次数；
  阅读最终报告本身不计入。
- `artifact_read`：Owner 是否真实打开并使用该 treatment 的最终交付 artifact，而不是只检查
  CLI 是否退出。
- `infrastructure_failure`：Provider、网络、环境、依赖或 Harness 故障导致任务不可比较；
  它不计为能力失败，也不能被静默重跑。

Token 无法取得时记录 `null`，聚合必须显示覆盖率，不能把缺失值当作零。

## 6. 执行顺序

为降低学习效应：

1. 先完成 case 资格门并冻结 task contract。
2. 为每个 case 预先固定 treatment 顺序，Bug 与 Feature 内交替起始 treatment。
3. 每个 treatment 使用全新 worktree 和独立运行目录。
4. 不向第二个 treatment 暴露第一个 treatment 的 patch、Reviewer finding 或运行结论。
5. 两组运行结束后再打开上游 oracle。
6. 只在两组证据均封存后填写有效 finding 和人工操作计数。

如果某 treatment 发生基础设施故障，该 case 保留为不完整 pair。V1 不在同一合同下追加隐藏
重试；是否重跑必须作为新的实验版本预注册。

## 7. 聚合与结论边界

`scripts/daily_value_eval.py` 只做结构校验和描述性聚合：

- 检查 case ledger revision；
- 拒绝未达到 `runnable` 的正式结果；
- 检查每个 case 是否同时存在 `native` 和 `vega`；
- 汇总验证成功、假成功、Reviewer 独立发现、时间、Token、人工操作和恢复；
- 输出 JSON 与 Markdown。

它不克隆仓库、不启动 Provider、不修改 Runtime，也不自动宣称 Vega 有价值。

结论规则：

- 少于 6 个完整 pair：`insufficient_evidence`。
- 6 个完整 pair：`paired_results_complete`，只允许人工做方向性判断。
- 任一 treatment 出现 `false_success`：必须单独分析，不得用平均耗时或 Token 抵消。
- Token 覆盖不完整：不得形成 Token 经济性结论。
- Reviewer finding 必须经过人工或 oracle 确认后才能计数。

Owner 最终判断至少同时查看：

1. Vega 的 `verified_success` 是否低于 Native。
2. Vega 是否减少 `false_success` 或增加有效独立 finding。
3. Vega 是否减少人工操作。
4. Vega 增加的时间和 Token 是否可接受。
5. Vega 的 artifact 是否真的被阅读并帮助追溯。

## 8. 停止线

出现以下任一情况，停止扩建 Harness，先处理实验设计：

- 为了让 case 可跑而修改 Vega 核心 Runtime；
- 同一痛点尚未在至少两个真实 case 重复出现；
- 需要加入 Multi-Worker、Memory、A2A 或 Web UI 才能完成比较；
- verifier 无法固定或 baseline 不能稳定复现红；
- treatment 之间模型、任务、路径或验证条件不一致；
- 结果需要人工“润色”才能解释为成功。

## 9. 本地命令

只校验当前 candidate ledger：

```powershell
python scripts/daily_value_eval.py
```

资格确认并追加 `runnable` revision 后，使用正式结果生成本地摘要：

```powershell
python scripts/daily_value_eval.py `
  --results <results-jsonl> `
  --output-dir .local-validation/daily-value-v1
```

本地输出默认不提交。正式证据是否进入 `eval/`，必须在运行完成后按 append-only 规则单独审核。

## 10. 资格确认记录

- `DV-B01`：2026-07-29 退休。baseline 红、Windows 与依赖安装均已确认，但上游关闭 Issue
  后没有接受修复，当前上游 `main` 同一复现仍为红，无法满足 oracle 资格门。完整记录见
  `eval/experiments/daily-value-validation/qualifications/DV-B01.md`。
- `DV-B04`：2026-07-29 达到 `runnable`。Windows/Python 3.14.3 下同一独立 verifier
  连续三次稳定得到 baseline 红、oracle 绿，两个 ref 均完成安装并通过 `pip check`。正式
  Worker 只接收脱敏任务合同和 baseline-only workspace，不接收 Issue URL、PR、oracle ref
  或完整 Git 历史。完整记录见
  `eval/experiments/daily-value-validation/qualifications/DV-B04.md`。
- `DV-B02`：2026-07-29 达到 `runnable`。独立 verifier 连续三次稳定得到 baseline 红、
  oracle 绿；两个 ref 均从源码安装并通过 `pip check`。正式执行采用
  `CODEX-EXECUTION-PROFILE.md`，保留 Provider 路由并显式关闭实验外 feature。完整记录见
  `eval/experiments/daily-value-validation/qualifications/DV-B02.md`。

## 11. 正式运行记录

- `DV-B04/native`：2026-07-29 启动一次正式 Worker 调用。为了隔离本机 hooks、Memory、
  Goal 和其他用户配置，启动命令使用了 `--ignore-user-config`；该选项同时移除了本机
  自定义 Provider 路由，导致调用落到不匹配的默认路由并返回 `401`。Worker 没有获得模型
  输出、没有修改文件，固定 verifier 仍为红，Reviewer 未启动。该结果登记为
  `infrastructure_failure`，按 V1 合同不进行隐藏重跑。公开记录见
  `eval/experiments/daily-value-validation/runs/DV-B04-native-20260729.md`。
- `DV-B02/vega`：2026-07-29 按冻结 profile 启动一次正式 Vega auto loop。Worker 在
  `600` 秒内没有返回；终止确认后 Runtime 停止 verification 和 Reviewer。超时现场只修改
  一个允许文件，但 patch 引用了未导入的 `Converter`，封存阶段固定 verifier 四项均失败。
  该结果登记为 `timed_out`，不进行隐藏重跑。公开记录见
  `eval/experiments/daily-value-validation/runs/DV-B02-vega-20260729.md`。
- `DV-B02/native`：2026-07-29 使用同一 profile、模型、reasoning 和 timeout 启动一次
  原生 Codex Worker。Worker 在最终 smoke 返回前超时，Reviewer 未启动。超时现场修改两个
  允许文件并补充回归测试；封存阶段固定 verifier 四项全部通过，但未把部分绿态改写为成功。
  该结果登记为 `timed_out`，不进行隐藏重跑。公开记录见
  `eval/experiments/daily-value-validation/runs/DV-B02-native-20260729.md`。

DV-B02 的配对分析、实验缺口和最小 V2 条件见
`docs/experiments/daily-value-validation/DV-B02-PAIR-ANALYSIS.md`。
