# Assurance Stage 2 expand/backfill/contract 双生实验

> 实验：`AV-STAGE2-002`
> 状态：本地候选已运行；2026-07-24 独立审查修正后等待 PR CI
> Threat：`T-DB-MIG-COMPAT`
> 结论上限：`continue-experiment / requires_staged_rollout`

## 一、目的与边界

本实验只验证一个最小发布顺序问题：给已有数据的 SQLite 表增加新标识列时，提前收紧
`NOT NULL` 约束会失败；先扩展可空列、对固定两行 fixture 做有界数据准备，再收紧约束，
是否能通过应用/schema 兼容矩阵和独立 SQL oracle。

这里的两行更新只属于 migration 实验内部的 bounded fixture data preparation，用来验证
`expand -> backfill -> contract` 的发布顺序。它不是 Stage 3 的通用 backfill 能力，不提供
租户范围、row budget、分批恢复、批处理重试或 reconciliation 框架。

本实验：

- 不注册 `vega` CLI；
- 不写入默认 `runs/`；
- 不接入 Finish、Goal、Loop、`ready_to_commit` 或 `AdequacyResult`；
- 不改变 Vega 的成功语义；
- 不证明 PostgreSQL/MySQL、生产数据规模、锁影响、并发写入或真实滚动发布安全。

## 二、冻结输入

基线数据库只有一张表和两行数据：

```sql
CREATE TABLE customer (
  id INTEGER PRIMARY KEY,
  display_name TEXT NOT NULL
);
```

```text
1, Ada
2, Lin
```

阶段定义：

```text
OldSchema:
  customer(id, display_name)

ExpandedSchema:
  customer(id, display_name, external_id NULL)

BackfilledSchema:
  external_id 仍可空，但两行必须分别为 cust-0001 / cust-0002

ContractSchema:
  external_id TEXT NOT NULL UNIQUE
```

artifact schema 固定为 `2`。顶层必须包含：

```text
schema_version
experiment_id
threat_id
engine
generated_at_utc
overall_decision
candidate_decision
evidence_adequacy
runtime_integration
decision_scope
external_quality_gates
dangerous_twin
safe_twin
limitations
artifacts
```

## 三、危险控制

危险顺序固定为：

```text
old -> expand -> contract -> backfill
```

扩展可空列后，两行 `external_id` 仍为 `NULL`。contract 阶段使用 SQLite 表重建模拟
`NOT NULL UNIQUE` 收紧：

```sql
CREATE TABLE customer__contract_tmp (
  id INTEGER PRIMARY KEY,
  display_name TEXT NOT NULL,
  external_id TEXT NOT NULL UNIQUE
);

INSERT INTO customer__contract_tmp (id, display_name, external_id)
SELECT id, display_name, external_id
FROM customer
ORDER BY id;
```

预期：

1. detector 在执行前报告
   `T-DB-MIG-COMPAT:contract_before_backfill:external_id_contains_null_rows`；
2. 为验证 detector 敏感性，实验仍绕过 detector 实际执行一次；
3. SQLite 在复制包含 `NULL` 的行时失败；
4. transaction rollback 后原 expanded schema 和两行数据保持不变；
5. 不残留 `customer__contract_tmp`；
6. 危险双生判定为 `reject`。

## 四、安全双生

安全顺序固定为：

```text
old -> expand -> bounded fixture backfill -> contract
```

步骤：

1. 增加可空 `external_id`；
2. 只更新两行固定 fixture，值为 `cust-0001` 和 `cust-0002`；
3. 第二次运行数据准备时更新行数必须为 `0`；
4. 验证没有 `NULL`、映射完全匹配且值唯一；
5. 通过表重建收紧为 `TEXT NOT NULL UNIQUE`；
6. 第二次运行 contract wrapper 必须返回 `already_contracted`。

兼容矩阵冻结为：

```text
OldApp / OldSchema
NewApp / OldSchema
OldApp / ExpandedSchema
NewApp / ExpandedSchema
OldApp / BackfilledSchema
NewApp / BackfilledSchema
OldApp / ContractSchema
NewApp / ContractSchema
```

OldApp 只读取 `id/display_name`。NewApp 在旧 schema 下显式回退为
`external_id=None, schema_mode=old_fallback`；之后必须分别报告
`expanded_nullable`、`backfilled_nullable` 和 `contracted_not_null`。

## 五、独立 Oracle

应用适配器不能同时充当被测对象和最终 oracle。contract 完成后必须关闭原连接，重新打开
SQLite 文件并直接检查；oracle 不得复用 NewApp、detector 或 contract 的行/schema 读取
helper：

```sql
PRAGMA table_info(customer);
SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'customer';
SELECT id, display_name, external_id FROM customer ORDER BY id;
SELECT COUNT(*) FROM customer WHERE external_id IS NULL;
SELECT COUNT(DISTINCT external_id), COUNT(*) FROM customer;
SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name;
```

最终必须精确满足：

```text
columns = [
  {name: id, type: INTEGER, primary_key: true},
  {name: display_name, type: TEXT, not_null: true},
  {name: external_id, type: TEXT, not_null: true}
]
external_id.not_null = true
external_id 具有非 partial UNIQUE
rows = [
  {id: 1, display_name: Ada, external_id: cust-0001},
  {id: 2, display_name: Lin, external_id: cust-0002}
]
null_external_id_count = 0
distinct_external_id_count = 2
customer__contract_tmp 不存在
```

## 六、负向敏感性

至少注册以下故障注入：

1. 跳过一行数据准备，contract 必须失败或最终结论降为 `inconclusive`；
2. 写入错误但唯一的 `external_id`，应用读取层即使能读取也不能骗过独立 SQL oracle；
3. NewApp 掩盖持久化错误时，独立 SQL oracle 必须使结论降为 `inconclusive`；
4. contract 表遗漏 `NOT NULL` 时不能得到 `passed-local`；
5. transaction 失败后残留临时表时不能得到 `passed-local`；
6. 输出目录越过 `.local-validation/`，或任一已有组件是 symlink、junction、reparse point
   时必须在写入前 fail-closed。
7. 应用层与 detector 共享的行读取 helper 同时掩盖错误映射时，独立 oracle 仍必须读取真实
   SQLite 行并把结论降为 `inconclusive`；
8. contract 表丢失既有 `id PRIMARY KEY` 或 `display_name NOT NULL` 约束时，不能被误报为
   `already_contracted`，也不能得到 `passed-local`；
9. 重复 `external_id` 必须得到专用的 `external_id_not_unique` detector issue，不能被较宽泛
   的映射错误分类吞掉。

## 七、停止条件

只有同时满足以下条件，安全双生才可以得到 `passed-local`：

1. 危险顺序被 detector 标记，实际执行失败且 rollback 后事实保持基线；
2. 安全顺序的全部兼容矩阵通过；
3. bounded fixture data preparation 首次更新两行，第二次更新零行；
4. contract wrapper 首次成功，第二次报告 `already_contracted`；
5. 独立 SQL oracle 精确验证 schema、完整行映射、非空和唯一性；
6. 所有注册的负向控制都能使对应案例得到 `reject` 或 `inconclusive`；
7. 输出路径边界 fail-closed；
8. 定向测试、完整测试、静态检查和跨平台 CI 均明确通过。

单次本地脚本不能自行证明完整测试、静态检查和跨平台 CI 已通过，因此执行 artifact 固定保持：

```text
overall_decision = inconclusive
candidate_decision = continue-experiment
evidence_adequacy = insufficient
runtime_integration = disabled
external_quality_gates.status = not_evaluated
```

只有追加式 eval 证据把负向控制、完整测试、静态检查和同一最新 head 的跨平台 CI 全部关闭
后，组合裁决才最多提升为：

```text
continue-experiment / requires_staged_rollout / do-not-integrate
```

它仍不支持 `sufficient_for_merge`、生产 migration 安全、可恢复 backfill 安全或默认
Runtime 集成结论。
