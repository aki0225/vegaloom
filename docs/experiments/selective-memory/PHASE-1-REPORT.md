# Phase 1：Schema、Event Replay 与 Snapshot

## 已实现

- `RunMemoryItem`、`MemoryEvent`、`MemorySnapshot`、`InterventionCandidate`、
  `ReminderDecision` 独立 schema。
- append-only JSONL event store，校验连续 seq、唯一 event ID 和 repo/run/task 绑定。
- `add / update / invalidate` 三类事件；update 不能静默修改 statement。
- verified item 必须绑定证据；worker/reviewer 只能是 inferred，工具文本只能是 untrusted。
- inferred/untrusted item 不能自动进入 active。
- snapshot 可从事件确定性重建；缺失、损坏或哈希不一致时丢弃派生缓存并重建。
- evidence hash 不一致时，verified item 不再进入 active 候选。
- 非 verification 来源不能 update/invalidate 已验证事实，避免低权限事件保留 verified 标签却
  改写其风险、适用条件或生命周期。
- Canonical State 与 Run Memory 统一为只读候选，但 Canonical State 保持更高优先级。
- C 与 D 共享相同 Top-K 候选集合和顺序。
- 确定性 `allow / remind / block / escalate` 规则与最近三个 checkpoint 提醒去重。
- 高风险 block/escalate 不受普通提醒去重；Session 恢复或候选风险、来源、适用条件变化后允许
  重新提醒。
- 冲突必须由 fixture/投影显式声明 `conflict_group`，不再把“文本不同”武断视为语义冲突。

## 明确取舍

- 没有引入数据库、向量检索、embedding 或 LLM Judge。
- 没有浮点 confidence 字段。
- 没有复制用户最新目标、审批和项目规则为第二套 Memory 状态。
- Snapshot 是派生缓存，Event 才是事实来源。
- Prompt Injection 保持 untrusted candidate，不参与自动决策。

## Phase 1 验证

实验单测覆盖 schema、事件追加、重放、重建、证据过期、冲突、候选、去重和路径边界。
公开归档补齐三个离线反例后，Phase 1–2 合计实验测试：`39 passed`。同时通过：

```powershell
python -m compileall eval\selective_memory
ruff check eval\selective_memory tests\experimental\selective_memory
git diff --check
```

独立只读审阅曾发现共享 Canonical State 门禁、指标恒真/硬编码、Relevant Item Recall 口径、
低权限事件修改 verified item、冲突误判、去重版本和成本计时等问题。上述问题均在最终报告生成前
修正，并增加对应反例测试；最终数据不是审阅前的初版结果。
