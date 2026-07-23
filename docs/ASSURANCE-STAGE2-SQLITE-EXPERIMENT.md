# Assurance Stage 2 SQLite migration 双生实验

> 状态：独立本地实验；不注册 Vega CLI，不写入默认 `runs/`，不改变 Finish、Goal 或 Loop
> 的成功语义。

## 一、目的与边界

本实验只验证一个可复放的数据库 schema migration 风险：已有数据时，SQLite 的
`ALTER TABLE ... ADD COLUMN ... NOT NULL` 若没有非空默认值，会被数据库拒绝。SQLite 官方
`ALTER TABLE ADD COLUMN` 文档明确列出该约束；事务控制语义参考 SQLite 官方 Transaction
文档。

- ALTER TABLE：<https://www.sqlite.org/lang_altertable.html>
- Transaction：<https://www.sqlite.org/lang_transaction.html>

实验对应候选 Threat：`T-DB-MIG-COMPAT`。

它不是通用 SQL parser、生产 migration executor 或数据库服务能力。SQLite 只是让危险控制和
安全双生案例能在无外部依赖的干净环境中实际执行的最小载体。

## 二、预注册问题

1. 对已有行的表新增无默认值 `NOT NULL` 列时，受控 detector 能否在执行前给出明确拒绝？
2. 绕过 detector 后，SQLite 是否实际拒绝该 migration，并且 schema 与已有数据保持基线？
3. 一个 expand-only 的可空列迁移，是否能通过最小 OldApp/NewApp × OldSchema/NewSchema
   兼容矩阵？
4. 安全双生是否至少能证明受控 wrapper 的重复执行不会二次执行 DDL？
5. 如果矩阵读取字段或最终行内容被破坏，确定性 oracle 是否会把结论降为
   `inconclusive`？
6. `.local-validation/` 或任一已有输出路径组件是 symlink、Windows junction 或其他
   reparse point 时，脚本是否在写入前 fail-closed？

## 三、双生设计

### 3.1 危险控制

```sql
ALTER TABLE customer ADD COLUMN external_id TEXT NOT NULL
```

- 基线表已写入两行数据。
- 受控 detector 只识别本实验注册的 `ADD COLUMN ... NOT NULL` 且无 `DEFAULT` 语法。
- 仍实际执行一次，用 SQLite 返回的错误和执行后 schema/data 快照验证 detector 的敏感性。
- 预期：`reject`。

### 3.2 安全双生

```sql
ALTER TABLE customer ADD COLUMN external_id TEXT
```

- 新列保持可空，避免把旧行或旧应用立即推入不兼容状态。
- `NewApp` 最小适配器在旧 schema 下显式回退为 `external_id=None`；`OldApp` 只读取旧列。
- 覆盖 `OldApp/OldSchema`、`NewApp/OldSchema`、`OldApp/NewSchema`、`NewApp/NewSchema`。
- 每格按完整有序行内容判定，不只比较主键；`NewApp` 还必须绑定 `external_id` 和
  `schema_mode`。
- migration wrapper 先读取 schema；第二次执行必须是 `already_present`，而非重新执行 DDL。
- 矩阵完成后分别读取 OldApp/NewApp 行内容，并通过独立 SQL 直接读取持久化
  `external_id`；只有基线姓名、空 `external_id` 和 `schema_mode=expanded` 全部保持时，
  `data_invariant.passed` 才为真。
- 预期：安全双生仅可得到 `passed-local`；整体最多为 `continue-experiment`。

## 四、重放

在仓库根目录执行：

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
python scripts/run_assurance_stage2_sqlite_experiment.py `
  --output-dir ".local-validation/assurance-stage2-sqlite-$stamp"
```

输出只能位于当前仓库的 `.local-validation/` 下：

```text
result.json
report.md
dangerous.sqlite
safe.sqlite
```

`result.json` 是机器可读事实源；`report.md` 只渲染同一结论。所有输出默认被 `.gitignore`
排除，公开验证记录只在 `eval/assurance-validation.md` 追加摘要和 SHA-256。

当前 artifact schema 为 `2`。脚本使用当前工作目录下的词法 `.local-validation/` 作为允许
根，并在创建输出目录前后拒绝任一已有路径组件为 symlink、junction 或 reparse point。
这是本地 fail-closed 边界，不构成对恶意并发替换路径的操作系统级隔离证明。

## 五、停止条件与结论上限

本实验通过的最低条件：

1. 危险案例被 detector 标记，并在实际 SQLite 执行时失败。
2. 危险执行失败后 schema 与两行基线数据不变。
3. 安全案例的四格兼容矩阵全部通过。
4. 安全 wrapper 第二次执行只报告 `already_present`。
5. 四格矩阵和最终 `data_invariant` 均按完整行内容、`external_id` 和 `schema_mode`
   精确匹配；持久化 `external_id` 必须由独立 SQL 快照验证，不能只信任 NewApp 适配器。
6. 任何输出目录不在 `.local-validation/` 下，或已有路径组件是链接/reparse point 时，
   脚本 fail-closed 拒绝写入。

即使以上全部成立，结论仍不能超过 `continue-experiment`。未覆盖的关键生产问题包括：

- PostgreSQL/MySQL 等实际数据库版本的锁、在线索引、权限和发布编排；
- 真实应用代码与真实 schema 的兼容矩阵；
- DML/backfill、数据校验、恢复/roll-forward、并发写入和复制延迟；
- 生产数据规模、时间预算、监控和 staged rollout。

因此该实验绝不触发 `sufficient_for_merge`，不接入默认 Runtime，也不能作为生产数据库安全声明。
