# LangGraph 实验公开归档说明

> 冻结日期：`2026-07-20`
>
> 公开归档日期：`2026-07-21`
>
> 脱敏重建日期：`2026-07-22`
>
> 最终分类：`partial`
>
> 公开分支：`experiment/langgraph-comparison`
>
> 身份脱敏修复分支：`fix/langgraph-public-sanitization-closure`
>
> 同源基线：公开标签 `v0.1.0`（`5c492d2`）
>
> 默认产品路径：`linear + single reviewer`

## 1. 为什么保留这个分支

这个分支不是 Vega 下一版本的默认实现，也不是为了展示“使用过 LangGraph”而保留的
API 示例。它归档了一次可证伪的编排实验：

1. 让 Linear 与 LangGraph 复用同一批业务 Step Handler；
2. 验证 checkpoint、crash reconciliation 和结构化 HITL；
3. 评估 single、adaptive 与 fixed-three Reviewer topology；
4. 验证 Goal/Handoff 能否独立于 LangGraph 跨 Session 复用；
5. 在收益证据不足时接受 `partial`，而不是因为已经投入实现就强行合入主线。

最终结论是：

```text
default engine = linear
default reviewer topology = single
LangGraph = optional experimental recovery / HITL control plane
Goal / Checkpoint / Handoff = engine-neutral
```

## 2. 已证明与未证明

已证明：

- 顺序 Linear/LangGraph 可以复用业务 Handler，并取得一致业务终态；
- crash recovery 可以结合 execution、Step Result、workspace fingerprint 和 checkpoint
  对账，避免重复启动已经完成的 worker；
- HITL decision 可以绑定当前 verification、risk、policy 和 workspace evidence，并一次性消费；
- Goal/Handoff 可以不依赖 LangGraph 跨 Session 复用；
- Reviewer fan-out 可以被确定性聚合和恢复。

没有证明：

- LangGraph 顺序编排优于当前 Linear Runtime；
- adaptive/fixed-three Reviewer 能提升真实缺陷发现质量；
- LangGraph 在真实大任务中更可靠、更便宜或更高效；
- checkpoint 能把 Git workspace 和外部进程变成事务；
- 本分支已经达到默认产品路径或生产部署条件。

完整理由见：

- [`DECISION.md`](DECISION.md)
- [`CORE-DECISION.md`](CORE-DECISION.md)
- [`GATE-4.5-R6-DOGFOOD-RESULT.md`](GATE-4.5-R6-DOGFOOD-RESULT.md)
- [`GATE-5.5-RESULT.md`](GATE-5.5-RESULT.md)
- [`GATE-7-R6-RESULT.md`](GATE-7-R6-RESULT.md)

## 3. 与公开主线的关系

源实验仓的冻结历史与公开仓没有共同 ancestor，不能直接形成正常、可审查的 GitHub
分支差异。公开归档因此没有原样推送私有 ref，而是：

1. 从公开标签 `v0.1.0`（`5c492d2`）建立同源分支；
2. 整体导入冻结实验终点的 Runtime、测试、预注册合同、评测数据和结论文档；
3. 排除生成的 `src/vegaloom.egg-info/`、本地运行产物和源实验中间历史；
4. 保留公开仓截至 `v0.1.1` 已发布的治理材料、发布说明和品牌资源。

选择 `v0.1.0` 作为父历史，是因为冻结实验的 distribution/version 合同仍为 `0.1.0`；
公开 `v0.1.1`（`176ac381`）是后续安全维护发布，不应被伪装成实验的原始分叉点。保留
`v0.1.1` 的治理材料也不改变本分支的 Runtime 基线。

因此，公开归档 commit 与源实验 commit 必然不同。公开 tree 只保留“源实验冻结快照”这一
语义标签，不保留只能在私有历史中解释的 SHA。公开归档应通过本分支的 tree、测试和 CI
复核，不应假定两个仓库具有相同的 commit identity。

`2026-07-22` 的二次审计发现，既有公开归档 tree 仍残留源实验 commit 的完整和缩写身份，
并在 Gate 7 R2 失败证据中残留一个 Windows 主机名。修复分支不在旧归档提交之上追加
补丁，而是继续从 `v0.1.0` 重建新的单一归档提交，避免旧身份通过父历史继续可达。
既有 `experiment/langgraph-comparison` 保持不变，是否由 owner 更新为修复分支内容属于
后续发布治理决策。

## 4. 公开内容边界

本分支公开：

- LangGraph 可选引擎和 recovery/HITL 实现；
- Reviewer topology 负面实验实现与冻结数据；
- Goal/Handoff 实验实现；
- 自动化测试、预注册合同、结果文档和复现脚本。

本分支不公开：

- `.env`、API key、Authorization header 或 Provider 凭证；
- 本地 `runs/`、`.local-validation/`、`.tmp/` 原始产物；
- 源实验仓的中间 commit 历史；
- 本地绝对路径、个人邮箱或无关提交身份；
- 已消费远端执行身份所对应的外部系统状态。

测试中的 `sk-*`、Authorization 和邮箱字符串是 redaction / fixture 使用的虚构数据，不是
真实凭证。

为避免公开本机环境指纹，本分支还执行了语义脱敏：

- 文档中的绝对路径改为 `<python-executable>`、`<codex-wrapper>`、
  `<short-checkout>` 等占位符；
- 测试所需的 Windows 路径改为明显的 `C:/fixtures/...`；
- Provider/profile、model、域名和端口改为 `sandbox-provider`、`sandboxproxy`、
  `sandbox-model`、`provider.example.invalid` 与合成 loopback 端口；
- 会暴露跨仓冻结终点或临时公开修复链路的 commit SHA 改为语义标签。

这些替换不改变测试分支、故障分类、指标或 `partial` 决策，但公开配置不再代表原实验环境
的字面值。Gate 7 case 因身份字段变化重新计算了公开归档 SHA-256；对应 plan SHA-256
保持不变，这只是公开证据绑定更新，不是一次新的 Provider 实验。

## 5. 如何验证

基础路径不安装 LangGraph 也必须可导入和运行：

```powershell
python -m pip install -e ".[dev]"
python -c "import vega"
python -m pytest tests/test_langgraph_dependency_gate.py
```

完整实验验证需要显式安装可选依赖：

```powershell
python -m pip install -e ".[dev,langgraph]"
python -m compileall src
ruff check src tests
python scripts/check_langgraph_public_archive.py --history-base v0.1.0
python -m pytest --require-langgraph --collect-only -q
python -m pytest --require-langgraph
git diff --check
```

冻结测试合同为 `847` 个节点。完整测试超过单命令 60 秒时必须按文件或完整 node id 分片，
每个分片使用独立 `--basetemp`；timeout 不能计为通过。

二次身份脱敏的范围、计数和剩余治理动作见
[`PUBLIC-SANITIZATION-CLOSURE.md`](PUBLIC-SANITIZATION-CLOSURE.md)。

## 6. 分支与决策记录

本分支和 CI 证据已足以证明“实现、评估、拒绝默认采用”的工程判断，不需要为了形式强行
合入 `main`。公开冻结 tag 不是完成实验判断的前置条件，也不会自动创建。

如果后续希望在 GitHub UI 中保留更醒目的讨论记录，可以从本分支向 `main` 创建 Draft PR，
但完成审计后应关闭而不合并，并明确记录：

```text
decision-record
intentionally not merged
final classification = partial
```

公开 `main` 只需要保留精简实验摘要和本分支链接，不应合入完整实验 Runtime。
