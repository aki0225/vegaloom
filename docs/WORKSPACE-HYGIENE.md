# 工作区文件规范

Vega 的仓库根目录只保留源码、测试源码、项目配置、文档和必要的静态样例。
测试、验证和运行过程中产生的文件必须进入有明确职责的专用目录，不得擅自散落在仓库根目录
或写入其他项目。

## 目录职责

| 目录 | 用途 | 是否提交 |
|---|---|---|
| `src/` | Vega 源码 | 是 |
| `tests/` | 测试源码和静态测试 fixture | 是 |
| `.tmp/pytest/runs/` | pytest 的 `tmp_path`、隔离仓库和运行中间文件 | 否 |
| `.tmp/pytest/cache/` | pytest cache | 否 |
| `.tmp/ruff/cache/` | Ruff cache | 否 |
| `.tmp/pytest/legacy-*/` | 历史测试运行目录，仅用于本地迁移留存 | 否 |
| `.local-validation/` | 人工验证日志、检查报告和本地诊断输出 | 否 |
| `runs/` | Vega 运行 artifacts | 否，除非是脱敏样例 |
| `memory/` | 本地 memory ledger 和 proposal 数据 | 否，除非是脱敏样例 |
| `dist/` | wheel、sdist 等构建产物 | 否 |
| `build/` | Python 打包中间产物 | 否 |

## 强制规则

1. 测试代码只能放在 `tests/`；静态 fixture 放在 `tests/fixtures/`。
2. 测试运行产生的临时文件必须放在 `.tmp/pytest/runs/`，不得放进 `tests/`、仓库根目录、`runs/` 或 `.local-validation/`。
3. pytest 和 Ruff 的工具缓存分别放在 `.tmp/pytest/cache/` 与 `.tmp/ruff/cache/`。
4. 手工 dogfood、lint、打包和安全检查的最终日志、报告与诊断脚本必须放在 `.local-validation/`；测试执行工作区仍属于 `.tmp/pytest/runs/`。
5. Vega 的 `state`、`trace`、review、verification 和其他正式 run 输出必须放在 `runs/`。
6. 本地 memory ledger、proposal 和实验性 memory 输出必须放在 `memory/` 或对应的 `runs/` 目录。
7. 构建产物只能放在 `dist/` 和 `build/`。
8. 不得在仓库根目录创建 `tmp-*`、`pytest-*`、`test-output-*`、`validation-*` 等生成目录。
9. 不得把生成物写入 `ai-agent-learning-lab`、`AgentToolGate` 或其他目标仓库。
10. 发现路径不明确时，先选择 `.tmp/`，并在验证结束后保留可复现所需的最小证据。
11. 清理旧临时目录前必须先确认其中没有需要保留的验证证据；不得用无确认的批量删除。

## 推荐命令约定

```powershell
python -m pytest
python scripts\dogfood_eval.py --workspace . --runner none
```

`tests/conftest.py` 会为普通 pytest 运行分配 `.tmp/pytest/runs/pytest-<pid>`。
需要并行或分片运行时，应显式提供互不相同的目录：

```powershell
python -m pytest tests\core\test_smoke.py --basetemp .tmp\pytest\runs\smoke-$PID
python -m pytest tests\security\test_runtime_safety_integration.py --basetemp .tmp\pytest\runs\runtime-$PID
```

手工验证可以把日志重定向到 `.local-validation\`，例如：

```powershell
python -m pytest -q *> .local-validation\pytest.log
ruff check src tests *> .local-validation\ruff.log
```

这份规范只约束文件位置，不改变 v0.1 的运行语义，也不要求把本地运行 artifacts
提交到 Git。

## 本地产物保留与清理

- `.tmp/`、`dist/`、`build/` 和 `__pycache__/` 在没有活动进程且验证结果已登记后可视为可丢弃。
- `runs/` 先区分 active、被文档引用和普通历史 run；只按明确 run_id 清理，不使用“保留最近
  若干个”替代证据判断。
- `.local-validation/` 只保留尚未写入权威文档的最终诊断。已经由 CI、Release、`eval/` 或
  `examples/evidence/` 接管的重复日志可以列为清理候选。
- `memory/` 由人工逐条判断，不能因功能处于 Experimental 就批量删除 ledger。
- 清理前记录目标相对路径、文件数、占用空间和活动进程；删除动作必须另行获得明确确认。
