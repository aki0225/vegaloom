# DV-B05 资格确认记录

- 日期：2026-07-29
- Case：`DV-B05`
- 结论：`runnable`
- Provider 调用：未调用
- Worker / Reviewer 调用：未调用
- 核心 Runtime 修改：无

## 1. 上游任务

- 上游仓库：`pallets/click`
- Issue：`#3572`
- 关联修复：PR `#3653`
- baseline commit：`6ec99f89261b32f8a50848786eca055e1967659f`
- oracle commit：`fe3ad76e5807e7a401ed7520f051071c6ae1fa6e`

该问题是 Click 8.4 引入的交互提示回归：带样式的文本传给 `confirm` 或 `prompt` 后，
`color=False` 仍会输出 ANSI 代码。Issue 正文给出失败行为和预期输出，没有给出最终实现；
正式 Worker 合同不包含 Issue URL、PR、oracle ref 或实现线索。

oracle 是合并提交，baseline 是其第一父提交。两个 tree 之间只涉及：

- `CHANGES.md`
- `src/click/termui.py`
- `tests/test_termui.py`

正式 Worker 只允许修改后两个行为相关文件，不要求修改变更日志。

## 2. 固定任务与 verifier

冻结任务合同：

```text
docs/experiments/daily-value-validation/tasks/DV-B05.md
```

独立 verifier：

```text
eval/experiments/daily-value-validation/verifiers/DV-B05.py
```

verifier SHA-256：

```text
2d5e44e6432bb34c436c24a3a8254f51da916efe5676adc1574cc4b2161e2584
```

verifier 覆盖 `confirm / prompt × color=False / True × stdout / stderr` 共八个场景，同时：

- 校验进程退出码与异常；
- 校验合并输出和目标流的精确内容；
- 校验非目标流保持为空；
- 禁止验证过程写入 `.pyc`。

固定验证还运行 Click 现有 `tests/test_termui.py`，避免仅满足八个新增行为场景却破坏该模块
的既有交互语义。由于系统默认临时目录在本机不可写，命令固定使用 treatment 自有的
`--basetemp ../pytest-temp`；这不是新增 Harness。

## 3. Windows 红绿复现

- 操作系统：Windows 10 专业版，build `19045`
- Python：`3.12.10`
- 本地隔离目录：`.local-validation/daily-value-v1/qualification/DV-B05/`
- 每个 ref 使用独立源码目录和独立 venv

同一 verifier 分别连续运行三次：

| Ref | 三次退出码 | 三次输出是否一致 | 代表性输出 SHA-256 |
|---|---|---|---|
| baseline | `1 / 1 / 1` | 是 | `f96c3298e99e819b1f45974acb6e88cf6cedc9ae1c1e052f44f94cb5c4186c1c` |
| oracle | `0 / 0 / 0` | 是 | `925f672e0412f5f7c6b1c02fc9be719fa40aa9e49fb1b7fabb29d28a4d8d3b55` |

baseline 在 `color=False` 的四个场景稳定失败，在 `color=True` 的四个场景通过；oracle
八项全部通过。

## 4. 依赖与上游测试

两个 ref 均在独立 venv 中从源码安装成功：

- 安装包版本：`click 8.5.0.dev0`
- pytest：`9.1.1`
- `python -m pip check`：两个环境均输出 `No broken requirements found.`

固定上游切片 `tests/test_termui.py` 的结果：

| Ref | 结果 |
|---|---|
| baseline | `229 passed, 11 skipped` |
| oracle | `233 passed, 11 skipped` |

首次 pytest 的四个 setup error 来自系统默认临时目录权限；改用 case 自有 `--basetemp`
后完整通过，因此不计为代码失败，也没有修改项目测试。

## 5. Baseline-only workspace 预演

使用 `git archive` 从 baseline 导出全新源码树，并把 verifier 放在 workspace 同级父目录。
预演结果：

- workspace 共 150 个文件，不包含 `.git`。
- 精确检索 Issue URL、`#3572`、PR `#3653`、oracle SHA 和修复标题均无命中。
- `python ../verifier.py .` 返回退出码 `1`。
- verifier 运行前后文件清单与内容 hash 记录完全一致，SHA-256 均为
  `a0d09cf9a14a636d3c17bc972d8956576b7e5d5f580981edaecddf0f62978f0e`。

正式 treatment 必须重新创建目录，不得复用本次资格预演 workspace。

## 6. 冻结执行合同

- treatment 顺序：Native → Vega
- 模型：`gpt-5.6-sol`
- reasoning effort：`medium`
- timeout：`1200` 秒
- 允许修改：
  - `src/click/termui.py`
  - `tests/test_termui.py`
- 固定验证：
  - `python ../verifier.py .`
  - `python -m pytest -q tests/test_termui.py --basetemp ../pytest-temp`

1200 秒对应本轮要验证的 10 至 20 分钟任务尺度。`worker_token_limit` 只记录为观测预算，
不是已实现的硬门禁。

## 7. 资格门裁决

| 资格门 | 结果 | 证据 |
|---|---|---|
| 固定 baseline | passed | 40 位 commit，且是 oracle 第一父提交 |
| baseline verifier 为红 | passed | 连续三次退出码 1，输出 hash 一致 |
| 固定上游绿态 oracle | passed | 连续三次退出码 0，八项检查全绿 |
| 固定任务、路径与验证 | passed | 冻结任务合同、独立 verifier 与现有 termui 测试切片 |
| 固定模型与 timeout | passed | `gpt-5.6-sol` / `medium` / 1200 秒 |
| Windows 依赖与测试 | passed | 两个独立 venv 安装、`pip check` 和目标 pytest 通过 |
| Worker 不直接获得 patch | passed | 任务脱敏，baseline-only，无 Issue URL、PR 或 Git 历史 |

DV-B05 可以追加 `revision=2, status=runnable`。该结论只冻结未来一次 Native 与一次 Vega
直接配对的输入资格，不代表任何 treatment 已启动，也不代表 Vega 已证明日用价值。
