# DV-B02 冻结任务合同

> 状态：`runnable`
>
> 该文件是 Vega 与 Native 两个 treatment 共用的唯一 Worker 任务描述。

## 目标

修复 attrs 中 `converters.optional` 与 `converters.pipe` 组合使用时的回归。

当前行为会在非 `None` 输入进入组合 converter 时抛出缺少 `instance` 和 `field` 参数的
`TypeError`。预期行为：

1. `None` 输入继续直接返回 `None`，不执行内部 pipeline。
2. 非 `None` 输入按顺序执行 `pipe` 中的全部 converter。
3. pipeline 中需要实例或字段上下文的 converter 能收到正确上下文。
4. `optional` 包装普通单参数 callable 的既有行为保持不变。
5. 补充覆盖上述行为的回归测试。

## 允许修改路径

- `src/attr/converters.py`
- `tests/test_converters.py`

不得修改其他文件。

## 固定验证

正式运行目录必须把只读 verifier 放在 workspace 的同级父目录：

```text
<treatment-root>/
├── verifier.py
└── workspace/
```

在 `workspace/` 中执行：

```powershell
python ../verifier.py .
```

verifier 的固定 SHA-256 为：

```text
36111049980d30ad126ddbd1fc0247c870f068eeb3db01def559312f099b1b47
```

退出码 `0` 才算验证通过。运行前后都必须核对该 hash；Worker 不得修改
`../verifier.py`。

## 执行合同

- 模型：`gpt-5.6-sol`
- reasoning effort：`medium`
- timeout：`600` 秒
- treatment 顺序：Vega → Native
- 每个 treatment 只允许一次正式运行
- Codex 执行配置：
  `docs/experiments/daily-value-validation/CODEX-EXECUTION-PROFILE.md`

## 盲测边界

- workspace 只包含冻结 baseline 的源码树，不包含可读取上游修复的 Git 历史。
- Worker 不接收 Issue URL、关联 PR、oracle ref、上游 diff 或另一 treatment 的任何产物。
- 不进行外部检索，不使用 Trellis、Multi-Worker、Memory、Goal 或 A2A。
- Reviewer 只接收本任务、项目规则、当前 diff 和固定验证证据，不接收 Worker 完整对话。
