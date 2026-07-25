# Verification Summary

- 完整测试：`768 passed, 5 skipped`。
- 定向行为 oracle：8 个正反断言全部通过。
- 静态检查：`python -m compileall pycodestyle.py`、`python -m pycodestyle pycodestyle.py`
  与 `git diff --check` 均通过。
- 范围检查：只检测到 `pycodestyle.py`。

## 行为覆盖

- `type(obj) == int`、`int == type(obj)`、`str != type(obj)` 和
  `MyType == type(result)` 报告 `E721`。
- `expected == type(obj)` 与 `value != type(other)` 不报 `E721`。
- `type(obj) is int` 与 `isinstance(obj, int)` 不报 `E721`。
