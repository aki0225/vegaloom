# Selective Memory Reminder 完整离线评估

- 证据状态：`full-offline-evidence`
- Case：`10`
- Checkpoint：`150`
- 已标注 Checkpoint：`150`
- A/B/C/D 评估次数：`600`
- 真实 LLM 调用：`0`
- 真实任务成功率声明：`否`

## 原始规模与成本

| 模式 | 候选数 | 注入字符 | 注入字节 | Relevant Item Recall | Canonical Gate Accuracy | Decision Precision | Decision Recall |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 0 | 0 | 0 | not-applicable | 100.0% | not-applicable | not-applicable |
| B | 138 | 4405 | 7323 | 100.0% | 100.0% | not-applicable | not-applicable |
| C | 112 | 3501 | 5789 | 100.0% | 100.0% | not-applicable | not-applicable |
| D | 112 | 370 | 1050 | 100.0% | 100.0% | 100.0% | 100.0% |

说明：A/B/C 没有 Selective Decision，因此其 Decision 指标不适用；
四组仍共享 Canonical State 门禁。

## D 组决策质量

- Decision Accuracy：`100.0%`
- Decision Precision：`100.0%`
- Decision Recall：`100.0%`
- High-risk Recall：`100.0%`
- High-risk Exact Decision Recall：`100.0%`
- Overblocking Rate：`0.0%`
- False Positive / False Negative：`0 / 0`
- Dedupe Suppression Accuracy：`100.0%`
- Conflict Escalation Accuracy：`100.0%`
- Stale Reference Count / Rate：`0 / 0.0%`

## 安全与一致性

- Event Replay Determinism：`true`
- Snapshot Rebuild Success：`true`
- Resume Decision Consistency：`true`
- Candidate Parity：`true`
- Top-K Conflict Bundle Coverage：`100.0%`
- Memory Contamination Count：`0`
- Silent Conflict Merge Count：`0`
- Stale Item Observation Count：`7`
- Applicability Unknown Observation Count：`2`

## 样本门槛

- 状态：`sufficient-offline-samples`
- intervention_samples：`33/20`，sufficient=`true`
- high_risk_samples：`26/10`，sufficient=`true`
- relevant_candidate_samples：`40/20`，sufficient=`true`
- stale_rate_intervention_samples：`33/20`，sufficient=`true`

## Phase 2 门禁

- 决策：`candidate-for-shadow`
- 未通过门禁：`无`
- D/C 注入字符比例：`10.6%`

本报告只证明确定性离线机制和合成场景表现，不证明真实编码任务成功率提升。
