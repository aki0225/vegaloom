# Reviewer 有界假设调查开发实验预注册

> 实验 ID：`RCB-03-DEV`
>
> 状态：`preregistered / not-run`
>
> 登记日期：2026-08-08
>
> 主线基线：`144dd11ae426506fed31762dfd44d46476d90a49`
>
> 预注册提交：以首次包含本文的 Git 提交为准

## 一、只回答一个问题

RCB-01 已证明“文件级候选清单 + 强制读取候选”没有增加上下文 Golden 命中，反而把 Token
提高到约 `2.57x`。RCB-02 又证明普通两跳 AST 不能可靠表达字段生产者/消费者、工厂 dispatch
和嵌套实参来源。

本轮不继续扩建静态检索器，只验证一个更小的假设：

> 在 Core Review Pack、模型、candidate worktree 和输出 Schema 相同的前提下，只要求同一个
> Reviewer 先从 Diff 形成最多三个跨文件风险假设，再用有界只读搜索逐个证伪或确认，是否能在
> C1-C3 开发集上增加 Golden 命中，同时把 Token 和耗时控制在 A 组的 `1.5x` 内？

本轮只是开发门槛，不是泛化实验。通过开发门槛也不能进入主线，只允许另行冻结新的 Holdout。

## 二、与 RCB-01 的区别

RCB-01 的 B 组已经要求只读 Reconnaissance，因此本轮不能把“再写一段调查提示”当作新变量。
唯一差异必须是调查策略：

- RCB-01 B：控制端先生成最多 12 个文件候选，Reviewer 按清单读取。
- RCB-03 B：不提供候选文件、符号图或代码区段；Reviewer 从任务和 Diff 自己形成最多三个可证伪
  的风险假设，只读取验证假设所需的位置。

如果 B 组主要变化仍只是读取更多文件，或无法形成增量 Golden 命中，本假设直接判为不值得进入
Holdout，不再通过增加候选、索引深度或模型调用挽救。

## 三、固定边界

1. 不修改默认 Runtime、Reviewer Prompt、CLI、`ReviewVerdict` Schema 或成功语义。
2. 不传递 Worker 完整对话、内部推理、oracle、Golden、未来修复或其他运行输出。
3. 不生成 Context Appendix、Repo Map、AST 图、Embedding、向量索引、LSP/SCIP 或数据库。
4. A、B 均使用一个全新 Reviewer 会话；不增加 Planner、第二个 Reviewer 或额外模型调用。
5. Reviewer 只读，不运行测试、构建、安装、格式化、代码生成或其他可能写入缓存的命令。
6. 原始 Artifact 只写入 `.local-validation/rcb-03/`，不得提交本机路径、模型正文或敏感元数据。
7. 本文形成 Git 提交并推送前，不启动正式模型调用。

## 四、固定模型与输入

```text
model = gpt-5.6-sol
reasoning_effort = high
sandbox = read-only
timeout = 900s
repetitions_per_case_arm = 1
```

继续使用 RCB-01 已物化并完成哈希校验的 C1-C3 Core Review Pack。A 组输入就是对应 Core
Review Pack；B 组只在相同字节之后追加第五节的固定实验指令。

| Case | Core Review Pack SHA-256 | UTF-8 字节数 |
|---|---|---:|
| `C1` | `35578426efd27968de3ec95378f8550899ae2f48216f27de480a73bcf07329be` | 61,540 |
| `C2` | `52f12153e895c65df5bdb66979e221729733d44d59df9162150558816cd8254c` | 24,933 |
| `C3` | `4e99efc5f8cf3013854931c57219560b8be1c9e0ff990d32b72c37f2ea61c56b` | 21,435 |

每次运行使用对应 candidate revision 的全新 detached worktree。调用前后必须记录 HEAD、tree、
index 和 `git status --porcelain=v2 --untracked-files=all --ignored=matching -z` 哈希；发生变化时该次
运行无效并停止后续调用。

## 五、B 组固定指令

以下文本以 UTF-8 和单个前导换行追加到 Core Review Pack，除预注册提交中的拼写修正外不得在
看到输出后修改：

```text
# RCB-03 实验性有界影响调查

在输出最终 JSON Verdict 前执行以下只读审查步骤：

1. 从任务和完整 Diff 中形成最多三个可能导致真实回归的跨文件风险假设。假设应围绕调用方或
   被调用方、状态/字段的生产者与消费者、接口/工厂/具体实现 dispatch、配置/Schema/公共契约、
   或邻近测试遗漏；不要把“需要通读全仓”作为假设。
2. 按风险从高到低，用仓库内只读搜索和文件读取逐个证伪或确认。假设一旦被证伪就停止沿该路径
   扩展；没有代码证据的猜测不能写成 finding。
3. 整次调查最多使用 12 次只读搜索/读取命令，最多读取 6 个完整变更文件清单之外的不同文件。
   不运行测试、构建、安装、格式化、代码生成或任何可能写文件/缓存的命令。
4. finding 必须引用 candidate revision 中可复核的仓库相对文件和有效行号，可以引用未修改文件。
   `reviewed_files` 仍必须精确等于 Review Pack 的完整变更文件清单，不能加入调查文件。
5. 最终仍只能输出符合既有 Schema 的一个 JSON 对象，不输出调查过程、计划或 Markdown。
```

执行 Trace 用于核验命令数和读取路径，不读取或保存模型内部推理。

## 六、开发集与标签修正

C1-C3 仍只作为已知开发集，Golden 定义沿用 RCB-01，只有 C3 的位置描述按 RCB-02 Phase 0
审计修正：

- `C1`：高风险 `needs_human` 审查仍有可信验证，但 Finish 因只为 `approve` 保留受审工作区
  指纹而把验证显示为未知。必要关系位于 `loop_integrity.py` 和 `loop_evidence.py`。
- `C2`：Prompt 禁止副作用不能阻止 Python `.pyc`；进程环境应由 `CodexExecRunner` 传递到
  `run_owned_process()`。
- `C3`：错误来自 untracked 和 ignored 共用排除集合。真正需要核对的是
  `snapshot_workspace()` 对 untracked 的过滤调用、`workspace_ignored_path_exclusions()` 固定加入
  verification 临时目录，以及 `codex_workspace.py` 的过滤实现。原标签把因 import 增行而移动的
  调用点误写成 candidate Diff，本轮不沿用该位置表述。

Golden 评分仍必须同时满足：说明错误行为、实际影响以及可复核代码位置或调用关系。只给出宽泛
建议、猜测或“建议补测试”不计命中。

## 七、固定运行顺序

```text
01 C1-A1
02 C1-B1
03 C2-B1
04 C2-A1
05 C3-A1
06 C3-B1
```

失败、503、timeout、无效 JSON 或模型容量错误均消费对应序号，不补跑、不换模型、不改变顺序。

## 八、记录指标

每次运行至少记录：

- Prompt、Core Pack、candidate、输出 Schema 和 runner 的 SHA-256；
- Runner 终态、墙钟时间、终止确认和 candidate worktree 前后指纹；
- Provider 返回的原始 Token 统计是否可用及数值；
- 最终 verdict、findings、`reviewed_files` 覆盖和解析错误；
- 完成的只读搜索/读取命令数量和可核验的仓库相对读取路径；
- B 组是否超过 12 条命令或 6 个 Diff 外文件；
- Golden 命中和 false positive 人工评分。

命令正文只在本地原始 JSONL 中保留；公开结果只记录哈希、计数和脱敏仓库相对路径。

## 九、开发门槛与裁决

只有同时满足以下条件才记为 `eligible-for-holdout`：

1. 六次运行均形成有效、可解析且 worktree 不变的终态；
2. B 组命中至少 `2/3` 个 Golden，且相对 A 组至少增加 2 次命中；
3. B 组 false positive 总数相对 A 组最多增加 1；
4. 三次 B 运行全部遵守 12 条命令和 6 个 Diff 外文件上限；
5. B/A Token 中位数和墙钟时间中位数均不超过 `1.5x`。

其他裁决：

- `reject-before-holdout`：有效样本完整，但命中、误报或成本任一门槛失败。
- `insufficient-evidence`：Provider 或环境失败导致 A/B 无法比较，或输入/标签/Trace 合同失效。

`eligible-for-holdout` 只允许新增一份独立预注册，冻结至少四个新案例和独立标签后再运行；不能
直接修改默认 Reviewer。`reject-before-holdout` 时停止 Reviewer Prompt 方向，不增加静态图、
检索服务或更多提示层。

## 十、实施顺序

1. 提交并推送本文及文档导航，冻结预注册提交。
2. 在 ignored 实验目录创建最小 runner，先完成无模型 preflight、哈希、只读 worktree 和输出
   Schema 合同检查。
3. 按固定顺序执行六次模型调用。
4. 完成 Golden 与误报人工评分，追加脱敏结果到 `eval/reviewer-context-bootstrap.md`。
5. 更新 `ROADMAP.md`，明确是否允许进入 Holdout；不论结果好坏均保留全部已登记运行。
