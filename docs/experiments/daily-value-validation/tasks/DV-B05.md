# DV-B05 冻结任务合同

> 状态：`runnable`
>
> 该文件是未来 Native 与 Vega 两个 treatment 共用的唯一 Worker 任务描述。

## 目标

修复 Click 交互式确认和输入提示在禁用颜色时仍输出 ANSI 样式代码的回归。

预期行为：

1. `color=False` 时，`confirm` 和 `prompt` 的提示文本移除颜色与样式代码。
2. `color=True` 时，提示文本继续保留颜色与样式代码。
3. `err=False` 时提示写入标准输出，标准错误保持为空。
4. `err=True` 时提示写入标准错误，标准输出保持为空。
5. `confirm` 接收 `y`、`prompt` 接收 `Bob` 时均正常退出，并在对应输出流回显输入。
6. 补充覆盖上述行为的回归测试。

## 允许修改路径

- `src/click/termui.py`
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
python -m pytest -q tests/test_termui.py --basetemp ../pytest-temp
```

verifier 的固定 SHA-256 为：

```text
2d5e44e6432bb34c436c24a3a8254f51da916efe5676adc1574cc4b2161e2584
```

两个命令都以退出码 `0` 结束才算验证通过。运行前后都必须核对 verifier hash；Worker
不得修改 `../verifier.py`。每个 treatment 使用独立的 `../pytest-temp`，不得复用资格
确认目录或另一 treatment 的临时目录。

## 执行合同

- 模型：`gpt-5.6-sol`
- reasoning effort：`medium`
- timeout：`1200` 秒
- treatment 顺序：Native → Vega
- 每个 treatment 只允许一次正式运行

`worker_token_limit` 仅可作为观测预算记录，不是 Provider 侧或 Runtime 侧硬门禁，不得据此
宣称超额请求会被强制终止。

## 盲测边界

- workspace 只包含冻结 baseline 的源码树，不包含可读取上游修复的 Git 历史。
- Worker 不接收 Issue URL、关联 PR、oracle ref、上游 diff 或另一 treatment 的任何产物。
- 不进行外部检索，不使用 Trellis、Multi-Worker、Memory、Goal 或 A2A。
- Reviewer 只接收本任务、项目规则、当前 diff 和固定验证证据，不接收 Worker 完整对话。
- 本合同只冻结未来直接配对输入；资格确认阶段未调用 Provider、Worker 或 Reviewer。
