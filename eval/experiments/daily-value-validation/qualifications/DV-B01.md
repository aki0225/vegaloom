# DV-B01 资格确认记录

- 日期：2026-07-29
- Case：`DV-B01`
- 结论：`retired`
- Provider 调用：未调用
- 核心 Runtime 修改：无

## 1. 上游事实

- 上游仓库：`PyCQA/pycodestyle`
- Issue：`#1311`
- Issue 报告版本：`2.14.0`
- baseline tag：`2.14.0`
- baseline commit：`814a0d1259444a21ed318e64edaf6a530c2aeeb8`
- 资格确认时观察的上游 `main`：
  `c6ccf5df495828eb6090530d91168c1b961024f1`

Issue 于 2026-01-20 创建并在当天关闭。维护者建议通过括号降低 f-string 表达式歧义，
但没有接受行为修复；Issue timeline 没有关联修复提交，仓库历史也没有匹配 `#1311`
的提交。Issue 内容没有暴露最终 patch，但上游没有可用的绿态 oracle。

## 2. Windows 复现环境

- 操作系统：Windows
- Python：`3.14.3`
- 隔离目录：`.local-validation/daily-value-v1/qualification/DV-B01/`
- 安装方式：在独立 venv 中分别从 baseline 与上游 `main` 构建并安装 wheel
- 依赖检查：两个 ref 的 `python -m pip check` 均通过
- 安装版本：两个 ref 均报告 `pycodestyle 2.14.0`

本地 clone、venv、fixture 与原始日志均位于 `.local-validation/`，不会提交。

## 3. 固定复现

复现 fixture：

```python
set_comp = f"{ {i for i in ()} }"
dict_comp = f"{ {i: i for i in ()} }"
```

fixture SHA-256：

```text
f9764c7e394c6f878794b56b21dc3f27d6aa60f417405eb986517eabe2eeb521
```

在 baseline 和上游 `main` 上运行相同 pycodestyle CLI，结果均为退出码 `1`，并产生相同
四个诊断：

```text
issue_1311.py:1:15: E201 whitespace after '{'
issue_1311.py:1:31: E202 whitespace before '}'
issue_1311.py:2:16: E201 whitespace after '{'
issue_1311.py:2:35: E202 whitespace before '}'
```

两份诊断日志内容相同，SHA-256 均为：

```text
f14b53a45e4fd8dcd50dc91de41350946e2f9e8fd9877d7e96871dce71e9a51c
```

括号化 smoke fixture 在两个 ref 上均以退出码 `0` 通过：

```python
set_comp = f"{({i for i in ()})}"
dict_comp = f"{({i: i for i in ()})}"
```

这只能证明维护者给出的 workaround 可用，不能充当原任务的修复 oracle。

## 4. 资格门裁决

| 资格门 | 结果 | 证据 |
|---|---|---|
| 固定 baseline | passed | tag `2.14.0` 固定到 40 位 commit |
| baseline verifier 为红 | passed | 退出码 1，固定产生 4 个 E201/E202 |
| Windows 依赖安装 | passed | 两个 ref 均成功构建、安装并通过 `pip check` |
| Issue 不泄露最终 patch | passed | Issue 没有接受或关联修复 patch |
| 固定上游绿态 oracle | unavailable | Issue 关闭但未修复，上游 `main` 同一 verifier 仍为红 |

由于 oracle 资格门不可满足，DV-B01 不得成为 `runnable`。ledger 只追加
`revision=2, status=retired`，不把括号 workaround 当成修复，也不启动 Native 或 Vega
treatment。

## 5. 后续动作

使用新的 case ID 登记一个具备明确 baseline 红、上游 oracle 绿和窄验证面的替代 Bug。
DV-B01 的历史记录保留，不复活、不改写。
