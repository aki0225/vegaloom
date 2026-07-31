# CRWP-V1-02 任务合同

## 目标

修复 Sequelize 的 SQLite 方言在 `sync({ alter: true })` 重建表后丢失整数自增主键语义的
问题。公开来源为 `sequelize/sequelize#18265`，运行基线固定为仓库已准备提交。

## 行为合同

1. 原本声明为 auto-increment 的 SQLite 整数主键在重建后仍包含 `AUTOINCREMENT`。
2. `describeTable()` 对该列继续报告 `autoIncrement=true`。
3. 重建前已有数据、列值和主键值保持不变。
4. 删除当前最大主键后再插入，新主键不得复用已删除值。
5. 普通 `INTEGER PRIMARY KEY` 不得被误判为 auto-increment。
6. SQL 注释、字符串字面量、`CHECK` 或 `DEFAULT` 中出现文本 `AUTOINCREMENT` 时不得误判。
7. 修复只影响 SQLite，不改变其他 dialect 的 metadata 行为。
8. 连续两次执行 `sync({ alter: true })` 后，语义与数据仍保持稳定。

不得采用“整张表 SQL 出现 `AUTOINCREMENT` 就把任意主键标记为 true”的宽泛判断，也不得
依赖会被注释或字符串字面量欺骗的脆弱切分。

允许修改的两个测试文件必须直接覆盖：列级 SQL 与 metadata、自增表的数据保留、删除最大
主键后不复用、连续两次 alter、普通主键负对照，以及行注释和块注释中的
`AUTOINCREMENT` 负对照。不得只依赖独立 oracle 覆盖这些行为。

## 修改边界

只允许修改：

```text
packages/sqlite3/src/query-interface.ts
packages/core/test/integration/query-interface/createTable.test.js
packages/core/test/integration/sequelize.test.js
```

不得修改 lockfile、依赖、构建配置、native driver、非 SQLite dialect、项目规则、任务合同、
Vega 策略、独立 oracle 或验证命令；不得新增文件。不要 commit、push、release、删除文件、
联网检索或写入长期 Memory。

## 验证

Vega 将独立执行 workspace build、定向 SQLite integration tests、相关 ESLint、外部 SQLite
oracle 和 `git diff --check`。如需求或环境阻塞，停止并明确说明，不要跳过 SQLite 用例或放松
既有断言。
