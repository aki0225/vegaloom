# Task Contract

## 来源与隔离

- 目标：`pallets/click` Issue #2939。
- 基线：Click `8.2.1`，源码修订 `fd183b2ced1cb5857784fe7fb22f4982f671f098`。
- 上游 oracle：修订 `93c6966eb3a575c2b600434d1cc9f4b3aee505ac` 仅在独立参考缓存中封存；
  worker 执行副本没有 remote、该提交或其 diff。

## 修改边界

- 允许修改：`src/click/testing.py`、`tests/test_chain.py`。
- 禁止修改：任务、策略、独立 oracle、依赖、版本和项目规则。
- 禁止动作：commit、push、release、删除文件、联网检索和长期 memory 写入。

## 行为合同

- 链式命令中的 `click.File("r")` 绑定 stdin 后，应正常消费最后一行并以零状态结束。
- 正常耗尽输入后不得出现 `Aborted!`，也不得留下异常。
- 交互式 prompt 在真正 EOF 时仍保留既有的中止语义。

## 验证

- 定向 `tests/test_chain.py`。
- 独立 stdin 链式迭代 oracle。
- 完整 pytest。
- `git diff --check` 与三阶段精确路径范围门禁。
