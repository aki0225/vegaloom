# DV-B04 冻结任务合同

> 状态：`runnable`
>
> 该文件是 Native 与 Vega 两个 treatment 共用的唯一 Worker 任务描述。

## 目标

修复 Click 选项启用交互式提示后，字符串 `show_default` 没有按标签语义显示的问题。

当前行为会在提示行显示真实默认值，或者在没有真实默认值时完全不显示自定义标签。预期行为：

1. `show_default` 为非空字符串时，提示行显示该字符串标签，不显示真实默认值。
2. 即使真实默认值为 `None`，非空字符串标签仍然显示，用户仍可输入实际值。
3. `show_default` 为空字符串时，不显示真实默认值。
4. `show_default=True` 与 `show_default=False` 的既有行为保持不变。
5. 补充覆盖上述行为的回归测试。

## 允许修改路径

- `src/click/core.py`
- `src/click/termui.py`
- `tests/test_options.py`
- `tests/test_termui.py`

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
3d549fc0f033cd6fe126946eb63f59536439ed9fac0a6c1c7384cd6cc4367bf0
```

退出码 `0` 才算验证通过。运行前后都必须核对该 hash；Worker 不得修改
`../verifier.py`。

## 执行合同

- 模型：`gpt-5.6-sol`
- reasoning effort：`medium`
- timeout：`600` 秒
- treatment 顺序：Native → Vega
- 每个 treatment 只允许一次正式运行

## 盲测边界

- workspace 只包含冻结 baseline 的源码树，不包含可读取上游修复的 Git 历史。
- Worker 不接收 Issue URL、关联 PR、oracle ref、上游 diff 或另一 treatment 的任何产物。
- 不进行外部检索，不使用 Trellis、Multi-Worker、Memory、Goal 或 A2A。
- Reviewer 只接收本任务、项目规则、当前 diff 和固定验证证据，不接收 Worker 完整对话。
