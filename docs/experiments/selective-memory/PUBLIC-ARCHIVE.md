# Selective Memory 公开实验归档

> 日期：`2026-07-22`
>
> 分支：`experiment/selective-memory-archive`
>
> 状态：`experimental / candidate-for-shadow`
>
> 默认产品行为：`off`

## 1. 公开范围

本分支归档 Selective Memory Reminder 的 Phase 0-2 离线实验，只包含：

- 实验 schema、append-only event store、snapshot replay、候选选择和确定性策略；
- 10 个合成 case、150 个 checkpoint、A/B/C/D 共 600 次离线评估；
- cases、golden labels、测试、评估报告和复现脚本；
- 独立 opt-in pytest 开关与实验专用 CI。

历史实验基线是公开标签 `v0.1.0`。私有源提交身份不进入公开文件或提交历史，统一使用
`<source-phase-0-baseline>` 等语义标签。

## 2. 公开移植修复

公开归档以 Phase 1-2 快照为主体，只从后续实现回补三个已验证的离线正确性门禁：

1. 普通无 action 范围的失败记录不能提醒无关动作；
2. 无 action 范围的高风险失败记录必须 `escalate / applicability_unknown`；
3. evaluator 必须统计精确决策类型，高风险 `block/escalate` 被弱化为 `remind` 时必须拒绝。

Runtime 专用的 `prior_verification_failure` 不在本归档中。

为避免把后续修复伪装成原始实验输出，源 Phase 2 的三份结果保留为：

```text
SOURCE-EVAL-REPORT.md
SOURCE-PHASE-2-DECISION.md
source-metrics.json
```

不带 `SOURCE-` 前缀的报告由公开归档中的强化 evaluator 重新生成。两组结果的最终分类都仍是
`candidate-for-shadow`；新版只增加精确决策指标和门禁，没有改写 case、golden label 或负面
边界。

## 3. 明确排除

本分支不包含：

```text
src/vega/experimental/**
holdout.py
provenance.py
shadow*.py
真实 Runner 或 provider 调用
worker/reviewer prompt 注入
长期 Memory 写入
runs/
memory/
```

因此本分支不能证明真实 Agent 成功率提升，也不能直接合入产品主线。

## 4. 固定结论

```text
case_count = 10
checkpoint_count = 150
offline_evaluation_count = 600
llm_request_count = 0
decision = candidate-for-shadow
default = off
runtime_integration = none
real_task_success_claimed = false
```

`candidate-for-shadow` 只表示完整离线门槛通过，不表示已经进入 Shadow，更不表示可以启用
Runtime。

## 5. 复现

默认主线测试合同不包含本实验：

```powershell
python -m pytest --collect-only -q
# 540 collected
```

实验测试必须显式启用：

```powershell
python -m pytest --include-selective-memory --collect-only -q tests/experimental/selective_memory
# 39 collected

python -m pytest -q --include-selective-memory tests/experimental/selective_memory
python scripts/run_selective_memory_phase2.py
python scripts/check_selective_memory_archive.py --base-ref origin/main
```

复现脚本只在仓库 `.tmp/` 下重生成数据集和评估结果，逐文件核对 cases/golden，并校验公开
metrics 和报告的稳定字段。

## 6. 隐私与历史

公开提交使用 GitHub noreply 身份，不 cherry-pick 私有实验历史。范围扫描拒绝：

- 白名单外文件或 `src/vega/` Runtime 修改；
- 私有仓 remote、真实本机路径和主机名；
- 私人邮箱、未知 40 位 Git 身份；
- token、Bearer 凭证和带认证信息的 URL；
- Shadow、Runtime 导入和长期 Memory 写入。

合成 fixture 中的重复 64 位哈希和动态构造的示例密钥只用于安全测试，不是实际凭证。
