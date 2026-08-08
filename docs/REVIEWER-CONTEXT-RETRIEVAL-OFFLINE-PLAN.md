# RCB-02 Reviewer 上下文离线检索计划

> 实验 ID：`RCB-02`
> 状态：`draft / not-started`
> 登记日期：2026-08-08
> 前置结果：[`eval/reviewer-context-bootstrap.md`](../eval/reviewer-context-bootstrap.md)

本文是 RCB-01 之后的计划草案，不是开始模型调用的授权，也不是主线 Runtime 变更申请。
只有本文的输入、标签、指标和停止条件再次冻结后，才允许执行离线脚本；离线门槛通过后，
还需要单独预注册 RCB-03 Reviewer A/B 对照实验。

## 一、要回答的问题

RCB-01 的 Context Appendix 没有增加 Golden 命中，必要路径召回只有 `1/5`，但 B 组的 Token
约为 A 组的 `2.57x`。下一轮先不问“模型是否更聪明”，只问一个更小的问题：

> 以 Diff 中实际改变的符号和代码区段为种子，使用确定性的符号、定义、引用和有限调用关系，
> 能否在最多 8 个候选代码区段内召回真正需要审查的文件和位置？

这里的核心原则是：

```text
Diff 决定检索起点
代码关系决定扩展范围
task 文本只辅助排序
候选预算决定最终输出
```

因此本实验是 **Diff-driven，但不是 Diff-only**。不把全仓库摘要或一批相似文件直接交给
Reviewer。

## 二、为什么采用符号图

RCB-01 暴露的是粒度和关系问题，而不是文件数量问题：

- C1 的必要位置是变更函数依赖的 `loop_integrity.py` 和 `loop_evidence.py`；
- C2 需要从 Prompt builder 经过 Loop Runtime 找到 Runner 和 execution control；
- C3 的必要位置位于 changed file 的 Diff hunk 外，文件级候选无法表达；
- B 组通常读取了候选文件，但仍没有命中 Golden，说明“多读文件”不是有效上下文。

外部方案也支持从结构关系入手，但各有边界：

- [Aider Repository Map](https://aider.chat/docs/repomap.html) 从 Tree-sitter 标签和文件依赖图
  生成定义/引用地图，再按当前文件和标识符做图排序；
- [Tree-sitter Code Navigation](https://tree-sitter.github.io/tree-sitter/4-code-navigation.html)
  提供跨语言的 definition/reference 标签模型；
- [LSP Call Hierarchy](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/#textDocument_prepareCallHierarchy)
  和 [SCIP](https://sourcegraph.com/docs/code-search/code-navigation/precise_code_navigation)
  可以提供更精确的引用与调用关系，但需要语言服务器、索引器或构建环境；
- [Agentless](https://arxiv.org/abs/2407.01489) 与
  [AutoCodeRover](https://arxiv.org/abs/2404.05427) 都采用文件、符号、代码位置的分层定位，
  而不是把整个仓库作为上下文。

本轮只借鉴这些方案的最小共同部分，不复制它们的完整平台：当前案例都是 Python，先用标准库
`ast` 做实验专用索引，不新增 Tree-sitter、LSP、SCIP、向量数据库或常驻服务依赖。以后若
需要支持多语言，再替换符号提取器，不改变实验合同。

## 三、固定边界

1. 只在实验分支运行，不修改 `review_runtime.py`、默认 CLI、Reviewer Prompt、Verdict Schema
   或成功条件。
2. 第一阶段完全离线，不调用 Provider、不启动 Worker/Reviewer，不使用模型生成候选。
3. 只读取 candidate revision 的 Git tracked 普通文本文件；不读取 oracle 内容、其他运行输出、
   accepted memory 或本机配置。
4. 不建立数据库、向量索引、知识图谱、语言服务器平台或跨仓库缓存。
5. 候选必须以仓库相对路径、符号名、行区间和关系链表示；不得写入本机绝对路径或凭据。
6. 所有中间结果放在项目内 `.local-validation/rcb-02/`，测试临时目录放在
   `.tmp/pytest/runs/`；不把中间结果放入 `runs/`、`memory/` 或仓库父目录。
7. 不复跑或覆盖 RCB-01 的 20 个历史结果。

## 四、数据集与防泄漏

### 4.1 开发集

RCB-01 的 C1、C2、C3 只作为开发集，用来检查关系提取器是否能解释已知缺口：

- C1：直接依赖和验证/证据关系；
- C2：跨 Runtime 的多跳执行关系；
- C3：changed file 内 Diff hunk 外的区段关系。

开发集命中不能作为“泛化收益”证据，因为实现者已经知道这些 Golden。

### 4.2 Holdout 集

另选至少 4 个新的历史变更案例，满足：

- 至少两个需要未修改文件或未修改区段；
- 至少一个 Diff 自足控制；
- 至少一个没有已知 finding 的安全对照；
- candidate 有稳定的 base、candidate 和独立修复或人工复核依据；
- 不需要网络、安装新依赖或特殊操作系统状态。

在实现检索器前，由不参与实现的人独立冻结：

- 必要路径；
- 必要符号或代码区段；
- 错误行为与影响；
- 是否属于 Diff 自足或上下文依赖。

检索器只接收 task、base、candidate 和 Diff。oracle 只在控制端用于冻结标签和最终评分，
不得出现在候选生成过程、日志或输出中。

## 五、离线检索器设计

### 5.1 Diff 种子提取

对 base 和 candidate 分别解析统一 Diff，并把 hunk 行号映射到最小 enclosing symbol：

- module；
- class；
- function/method；
- 顶层赋值、注册表、配置键等可命名结构。

同时保存 base/candidate 两侧的符号，覆盖新增、删除、重命名和签名变化。每个种子记录：

```json
{
  "path": "src/example.py",
  "symbol": "Example.run",
  "side": "candidate",
  "changed_lines": [42, 49],
  "change_kind": "modified"
}
```

### 5.2 轻量关系图

节点是 `path + symbol + span`，不是只有文件。第一版只建立以下边：

- `defines`：文件定义符号；
- `imports`：模块或符号导入；
- `calls`：可静态解析的本地函数调用；
- `references`：符号或属性的引用；
- `inherits/implements`：类继承和 Protocol/接口实现的可识别关系；
- `tested_by`：测试符号导入或调用目标符号；
- `same_file_unmodified_span`：同一 changed file 中与种子相关的未修改区段。

解析不确定的动态反射、字符串拼接和无法解析的第三方调用只记录为低置信候选，不沿其无限
扩展。所有边都带来源行号和关系类型，方便人工复核。

### 5.3 有界扩展

从 Diff 种子做最多两跳普通关系扩展；如果经过接口或共享执行函数才能到达具体实现，允许
将这一条已识别的 dispatch 链折叠为一次关系，最多保留三段可解释链。每个种子设置上限，
避免一个高频符号占满全部候选。

候选优先级：

1. changed symbol 的直接 caller/callee；
2. changed file 中同一函数/类的未修改相关区段；
3. 直接引用 changed symbol 的测试；
4. 公共契约、Schema、配置或入口文件；
5. 任务文本和 Diff 关键词的 BM25/词频相关度，仅作为同层候选的次级排序。

通用名称、被大量文件引用的名称和没有可核验关系链的文本命中降权。最终最多输出 8 个
区段，总字符预算暂定 12,000；实际预算在预注册时冻结。

### 5.4 候选输出合同

```json
{
  "schema_version": 1,
  "source_revision": "<candidate-sha>",
  "changed_files_sha256": "<sha256>",
  "candidates": [
    {
      "path": "src/example.py",
      "symbol": "Example.run",
      "span": [42, 68],
      "role": "caller",
      "relation_chain": ["changed:Example.step", "called_by:Example.run"],
      "confidence": "high",
      "rank": 1
    }
  ]
}
```

输出必须满足：相同输入产生相同字节；路径在 candidate worktree 内；区段可由 Git 版本和行号
复核；原因不能是模型自述。

## 六、分阶段执行

### Phase 0：关系可达性审计

不写生产代码，先为 C1-C3 和 Holdout 记录：

- Diff 种子是什么；
- 必要区段到种子的最短关系链；
- 关系是否能由 `ast` 解析；
- 是否需要动态语义、LSP 或模型才能判断。

如果多数必要区段没有可解释的静态关系链，立即停止这条方案，不用模型调用来掩盖检索器
能力不足。

### Phase 1：开发集检索

只实现实验脚本和离线测试。要求：

- C1-C3 的必要路径全部可达；
- C3 必须能输出 changed file 的 Diff 外区段；
- C2 必须能输出跨 Runtime/Runner 的关系链；
- 生成结果稳定、无越界、无 oracle 泄漏。

### Phase 2：Holdout 评估

冻结 Holdout 后运行一次，不根据结果调整算法。输出一份 Markdown 报告和一个 JSON 评分表，
两者都只放在 `.local-validation/rcb-02/`，报告中只记录脱敏的仓库相对路径。

### Phase 3：是否提出 RCB-03 模型对照

只有 Phase 2 通过，才另行预注册 RCB-03 Reviewer A/B：

- A 仍是当前 Reviewer；
- B 只追加新的符号区段候选和固定 Reconnaissance 指令；
- 不改变 Runtime、Schema、默认配置或成功语义；
- 使用新的安全负向对照，不复用已失效的 C5；
- 失败、503、timeout 或无效终态仍按协议消费，不挑选性重跑。

## 七、指标与门槛

### 离线必记指标

- `required_path_recall_at_8`：必要文件被至少一个候选区段覆盖的比例；
- `required_span_recall_at_8`：必要符号/区段被正确覆盖的比例；
- `relevant_span_precision_at_8`：候选中经独立标签确认有直接审查价值的比例；
- `candidate_count`、`candidate_chars`；
- `materialization_seconds`；
- 输出哈希和 oracle/path 泄漏检查结果。

### 进入模型实验的最低条件

1. 开发集必要路径召回达到 `5/5`，且 C3 的未修改区段被明确覆盖；
2. Holdout 必要路径召回至少 `80%`，必要代码区段召回至少 `70%`；
3. Holdout 的 `relevant_span_precision_at_8` 至少 `40%`；
4. 候选数量不超过 8，字符预算不超过冻结上限；
5. 候选都有可复核关系链，不能靠模糊关键词单独入选；
6. 相同输入的输出字节完全一致；
7. 没有读取 oracle、未来修复提交、其他运行 Artifact 或凭据。

任何一项不满足，都不启动 Reviewer A/B，也不讨论 opt-in 或 shadow。

## 八、停止与后续选择

- Phase 0 无法建立关系链：停止符号图路线，记录为不可由当前静态信息解决的问题。
- Phase 1 能覆盖开发集、但 Holdout 召回不足：只允许修正一个关系提取或排序问题，不能同时
  引入 Embedding、LSP 和多跳平台。
- Holdout 通过但候选过多或成本超预算：先收紧区段和排序，不扩大图的深度。
- 只有后续 RCB-03 的新 A/B 达到预注册门槛，才讨论默认关闭的 opt-in；否则保持默认 Reviewer。

下一轮实现分支建议从更新后的 `main` 单独创建：
`experiment/rcb02-diff-symbol-retrieval`。本计划分支只承载结果和计划，不在这里混入检索器代码。
