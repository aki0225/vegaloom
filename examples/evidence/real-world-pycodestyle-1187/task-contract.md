# Task Contract

## 共同边界

- 目标仓库：`PyCQA/pycodestyle` 的公开源码快照。
- 问题：Issue #1187 中反向类型比较的 E721 漏报。
- 允许修改：仅 `pycodestyle.py`。
- 禁止修改：测试、项目策略、依赖和项目规则。
- 禁止动作：commit、push、release、删除文件和长期 memory 写入。

## 初始案例

初始合同要求反向内建类型比较报告 `E721`，并保持已有的 `is`、`is not` 与 `isinstance`
行为。它没有显式覆盖普通小写变量与 `type(...)` 比较的歧义语义。

## Follow-up 案例

据本地运行记录，follow-up 在运行前加入 reviewer 指出的正反例；当前公开 Git 历史不能独立
证明该先后顺序：

- `int == type(obj)`、`str != type(obj)` 与 `MyType == type(result)` 报告 `E721`。
- `expected == type(obj)` 与 `value != type(other)` 不报告 `E721`。
- `type(obj) is int` 与 `isinstance(obj, int)` 不报告 `E721`。

这使 follow-up 成为扩展后的行为合同，不应回写为“初始合同本来就已覆盖”。
