# Verification Summary

- 基线独立 oracle：在 Click 8.2.1 上失败，stdin 链式迭代输出包含 `Aborted!` 并返回非零状态。
- 真实 run 的四条验证命令均通过。
- 定向测试：`tests/test_chain.py` 通过。
- 独立 oracle：stdin 链式迭代正常完成，同时验证 prompt EOF 仍走既有中止语义。
- 完整 pytest：`688 passed, 72 skipped, 1 xfailed`。
- `git diff --check`：通过。

## 验证边界

独立 oracle 不复用 worker 新增或修改的测试辅助代码。它以公共 Click API 构建最小链式命令，并分别
断言 stdin 文件迭代与 prompt EOF 两种行为。
