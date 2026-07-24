# Assurance Stage 3 有界 DML/Backfill 实验预注册

> 实验：`AV-STAGE3-001`
>
> 状态：`preregistered / not-implemented`
>
> Threat：`T-DB-DML-SCOPE`
>
> 范围：`experiment-only`
>
> 结论上限：`continue-experiment / requires_staged_rollout`

## 一、目的与边界

本实验只验证一个高频数据修改风险：面向明确租户和固定目标行的 backfill，是否能在
`row budget` 内完成；进程在部分提交后中断时，是否能依据持久化事实恢复，并确认没有修改
范围外数据。

它把合同中的 `T-DATA-SCOPE`、`T-DATA-PARTIAL`、`T-DATA-RETRY` 和
`T-DATA-INTEGRITY` 收敛为一个冻结实验 ID `T-DB-DML-SCOPE`；这个 ID 仅供本实验 artifact
使用，不替代 Stage 1 的 Threat Family ID。

本实验不创建通用 backfill runner，不注册 `vega` CLI，不写入默认 `runs/`，不接入
Runtime、Finish、Goal、Loop、`ready_to_commit` 或 `AdequacyResult`，也不改变成功语义。

它不能证明：

- PostgreSQL/MySQL 的锁行为、复制延迟或在线 DDL 安全；
- 生产数据规模、真实流量、动态租户选择或长时间批处理安全；
- 并发写入、消息重试、重复投递或跨服务一致性；
- 默认 Runtime 已具备自动修复、自动恢复或自动部署能力。

## 二、Threat 定义

`T-DB-DML-SCOPE` 关注以下失败：

1. DML 缺少租户、目标 ID 或状态条件，修改了范围外行；
2. 计划行数超过 `row budget`，但执行仍继续；
3. 重复执行再次修改已完成行，产生非幂等结果；
4. 部分提交后进程中断，恢复逻辑跳过缺失行或重复修改已有行；
5. checkpoint 声称完成，但数据库事实与计划不一致；
6. 应用读取 helper 掩盖越界修改，使 detector 与报告同时误判。

LLM 只能提出候选风险，不能自行宣布证据充分。detector、持久化事实和独立 SQL oracle
共同决定危险案例是 `reject`，还是因证据不足得到 `inconclusive`。

## 三、冻结输入

引擎固定为 SQLite。基线表：

```sql
CREATE TABLE customer (
  id INTEGER PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  legacy_handle TEXT NOT NULL,
  canonical_handle TEXT,
  backfill_version INTEGER NOT NULL DEFAULT 0
);
```

冻结数据：

```text
id=101, tenant-a, legacy_handle=ada,      canonical_handle=NULL,    version=0
id=102, tenant-a, legacy_handle=lin,      canonical_handle=NULL,    version=0
id=201, tenant-b, legacy_handle=sentinel, canonical_handle=NULL,    version=0
id=202, tenant-b, legacy_handle=kept,     canonical_handle=keep-b,  version=1
```

计划只允许：

```text
tenant_id = tenant-a
target_ids = [101, 102]
execution_order = [101, 102]
row_budget = 2
batch_size = 1
mapping = {
  101: cust-a-0101,
  102: cust-a-0102
}
```

`plan_digest` 必须由上述有序计划的规范化表示计算；租户、目标、顺序、预算、批大小或映射任一
变化，都视为不同计划。

`id=201/202` 是范围外哨兵。任一值发生变化，安全案例都不能通过。

执行前 dry-run 必须只产生计划，不得写入数据库，并记录：

```text
candidate_rows = [101, 102]
candidate_count = 2
row_budget = 2
within_budget = true
```

dry-run 的目标集合、数量、`plan_digest` 或范围外快照任一与冻结输入不一致时，执行阶段不得
开始。

## 四、危险双生

危险路径故意构造缺少租户和目标 ID 的更新：

```sql
UPDATE customer
SET canonical_handle = 'cust-' || printf('%04d', id),
    backfill_version = 1
WHERE canonical_handle IS NULL;
```

预期：

1. detector 在写入前报告
   `T-DB-DML-SCOPE:unbounded_update:tenant_or_target_scope_missing`；
2. 为验证 detector 敏感性，实验在独立危险数据库中强制执行一次；
3. `id=201` 被错误修改，独立 oracle 必须直接读取该事实；
4. 危险双生判定为 `reject`；
5. 应用层即使掩盖 `id=201`，也不能使 oracle 通过。

## 五、安全双生

安全路径必须使用冻结计划，并按单行 batch 执行：

1. 写入前重新计算目标集合，必须精确等于 `[101, 102]`；
2. dry-run 的目标数必须为 `2`，且不得超过 `row_budget=2`；
3. 每次更新同时限制 `tenant_id`、目标 `id`、`canonical_handle IS NULL` 和
   `backfill_version=0`；
4. 严格按 `execution_order=[101, 102]` 执行，每个 batch 使用独立 transaction；
5. 第一批提交 `id=101` 后注入进程中断，不得生成成功结论；
6. 恢复时重新读取数据库，不信任进程内计数；
7. 恢复只更新仍缺失的 `id=102`；
8. 每次 batch 的实际更新数只能为 `1`，或在值已精确匹配时为 `0`；其他结果必须停止；
9. 完成后再次执行，更新行数必须为 `0`；
10. reconciliation 必须逐行比较冻结映射，并确认范围外哨兵未变化。

部分提交不是成功。首次中断 artifact 必须保持：

```text
overall_decision = inconclusive
candidate_decision = continue-experiment
evidence_adequacy = insufficient
runtime_integration = disabled
```

恢复后的本地安全案例最多得到 `candidate-passed-local`，不能自行升级整体结论。该候选结论
还要求本次运行的结构化 verification 为 `verified`；中断 run 的 verification 必须为
`interrupted`，不得因已经提交第一批而变成 `verified`。

## 六、独立 SQL Oracle

oracle 必须关闭执行连接，重新打开 SQLite 文件，并直接执行 SQL/PRAGMA；不得复用 backfill、
detector、checkpoint 或应用读取 helper。

至少检查：

```sql
SELECT id, tenant_id, legacy_handle, canonical_handle, backfill_version
FROM customer
ORDER BY id;

SELECT COUNT(*)
FROM customer
WHERE tenant_id = 'tenant-a'
  AND id IN (101, 102)
  AND canonical_handle IS NOT NULL
  AND backfill_version = 1;

SELECT id, tenant_id, legacy_handle, canonical_handle, backfill_version
FROM customer
WHERE tenant_id <> 'tenant-a'
ORDER BY id;
```

`id=202` 原本已有 `keep-b/version=1`，范围外检查必须比较完整冻结快照，不能把合法基线误报为
越界修改。

最终事实必须精确为：

```text
101 -> cust-a-0101, version=1
102 -> cust-a-0102, version=1
201 -> NULL,        version=0
202 -> keep-b,      version=1
```

## 七、负向控制

实现至少覆盖：

1. 删除租户条件，必须在写入前停止；
2. 目标集合意外包含 `id=201`，必须在写入前停止；
3. 计划行数超过 `row budget`，不得写入任何行；
4. `id=101` 已存在错误值时，不得覆盖后伪装成幂等；
5. 第一批提交后中断，artifact 不得报告成功；
6. 恢复时 checkpoint 声称完成但 `id=102` 仍缺失，必须继续或降为 `inconclusive`；
7. checkpoint 的计划摘要与当前冻结计划不一致，必须停止；
8. 重复执行第三次必须更新零行；
9. 应用 helper 与 detector 同时掩盖 `id=201` 的越界修改时，独立 oracle 仍必须失败；
10. reconciliation 漏掉目标行、错误值或范围外变化时，不能得到 `candidate-passed-local`。

## 八、Artifact 最小合同

实现 artifact 至少包含：

```text
schema_version
experiment_id
threat_id
generated_at_utc
plan_digest
overall_decision
candidate_decision
evidence_adequacy
runtime_integration
decision_scope
external_quality_gates
dangerous_twin
safe_twin
interruption
recovery
reconciliation
limitations
artifacts
```

每个危险、dry-run、中断、恢复和重复执行事实都必须带可追溯的 `evidence` 条目，至少记录：

```text
id
kind
producer
command
environment
snapshot
input.fixture_sha256
oracle
result
covers
artifacts
limitations
```

`snapshot` 必须绑定同一实验的 HEAD、策略和 fixture；`plan_digest`、SQLite 文件哈希或
reconciliation 报告与该 snapshot 不一致时，整体 `evidence_adequacy` 必须为
`insufficient`。哈希只证明 artifact 未被替换，不能替代独立 SQL oracle。

路径边界、异常消息脱敏和链接目录拒绝继续沿用仓库已有实验约束；不得把绝对工作区路径写入
artifact、控制台、文档或提交内容。

## 九、停止条件

只有同时满足以下条件，安全双生才可得到 `candidate-passed-local`：

1. 危险 DML 被 detector 拒绝，强制执行后独立 oracle 观察到真实越界修改；
2. 安全计划精确冻结租户、目标 ID、映射、row budget 和 batch size；
3. 第一批提交后中断不会产生成功结论；
4. 恢复过程只处理剩余目标，第三次执行更新零行；
5. 独立 oracle 精确验证全部目标和范围外哨兵；
6. 所有负向控制都能得到 `reject` 或 `inconclusive`；
7. 每条声明的 evidence 都绑定同一最新 HEAD、策略、fixture、计划和结构化 verification；
8. 定向测试、完整测试、静态检查和跨平台 CI 均绑定同一最新 head。

即使全部满足，组合结论最高仍为：

```text
continue-experiment / requires_staged_rollout / do-not-integrate
```

这里的 `do-not-integrate` 指不接入默认 Runtime 和成功语义，不否定实验代码与证据本身可以
进入主线供复核。

## 十、实现准入

本预注册文件进入主线前，不编写 Stage 3 脚本或测试。后续实现必须：

- 单独提交，不顺便增加 CLI、状态或默认命令；
- 先让注册的负向控制失败，再做最小实现；
- 保持 SQLite fixture 固定，不扩展成通用 runner；
- 将失败与成功 run 如实追加到 `eval/assurance-validation.md`；
- 发现预注册条件不足时先修改合同并重新审查，不用实现结果反向改写停止条件。
