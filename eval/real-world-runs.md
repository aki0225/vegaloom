# 真实任务运行记录（Real-world runs）

在公开开源项目的真实 Issue 上运行 Vega 的记录。目的不是刷成功率，而是验证核心语义在
真实仓库上是否成立：worker 与 reviewer 上下文隔离、确定性验证终态确认、证据不足时 fail-closed。

## 协议

- **预注册**：运行前登记目标 Issue 与验证要求，不换题、不挑成绩重跑。
- **oracle 封存**：对有官方修复的 Issue，执行前封存官方修复（oracle），运行期间只保留哈希，
  运行结束后才 materialize 对比。
- **隔离**：worker 在可写沙箱中修改，reviewer 在只读上下文中仅依据 diff 与测试证据评审，
  不接触 worker 的对话内容。
- **fail-closed**：验证失败、基础设施受阻或证据不足时停止自动执行，保留现场交还人工。

## 运行记录

| Issue | 仓库规模 | 结果 |
|---|---|---|
| [pycodestyle #1072](https://github.com/PyCQA/pycodestyle/issues/1072) | 小型 | ✅ 单轮通过；diff 1 增 2 删，与官方修复（PR #1073）逐字节一致（blob 哈希相同）。全程约 222 秒 |
| [attrs #1085](https://github.com/python-attrs/attrs/issues/1085) | 中型 | ✅ 通过验证与隔离审查，完成闭环 |
| [CPython #82369](https://github.com/python/cpython/issues/82369) | 6000+ 文件 | ✅ 在超大仓库上完成修改、验证与隔离审查的完整闭环 |
| [Django #33368](https://code.djangoproject.com/ticket/33368) | 大型 | ⛔ 定位正确，Windows sandbox 阻止写入 → **fail-closed 安全停止**，现场保留交还人工 |

fail-closed 的记录与成功记录同等保留：它验证的是"证据不足或基础设施受阻时不硬跑"这条
语义真的会触发，而不是仅停留在设计文档里。

## 这些运行不能证明什么

- 样本量小且经过选择，**不构成成功率统计**，不应从表格推导出任何百分比。
- pycodestyle 一例与官方修复逐字节一致，**不能排除该修复存在于模型训练数据中的可能**；
  多仓库运行降低了单点记忆效应的解释力，但没有消除它。
- 未覆盖长周期任务、request_changes → fix 多轮循环的全部路径，以及高频日用下的 token 成本。

## 产物

每次运行的 state、trace、验证输出与审查报告按 [ARCHITECTURE](../docs/ARCHITECTURE.md)
所述结构保留在本地运行档案中，包含各阶段产物哈希，可按需提供复核。
