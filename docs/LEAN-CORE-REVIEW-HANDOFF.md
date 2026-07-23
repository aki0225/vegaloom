# 轻量核心分支只读审查接力

> 审查日期：2026-07-23
>
> 工作分支：`refactor/lean-core`
>
> 审查基线：`48b7f37ce56e35a7a86b0dbcfa46669593e3265e`
>
> 当前裁决：`request_changes / continue-on-same-branch / do-not-merge`

## 1. 审查快照

- 主线：`origin/main@521f9b924241ec258c75b2ecc893bdaa3be91abd`
- Draft PR：`#8`
- 审查基线与当时远端 `origin/refactor/lean-core` 一致。
- 审查基线 GitHub checks：`10/10 success`。
- 差异：55 个文件，2672 行新增、1000 行删除。
- 本地完整收集：615 个节点。
- 审查过程中未修改实现；临时复现只写入被忽略的 `.tmp/`，退出时已清理。

本次审查不是重新证明所有历史 Assurance 结论，而是优先检查本分支新增的架构门禁、模块隔离、
Loop 状态重构、SQLite Stage 2 实验、打包和 CLI 边界。

## 2. 阻塞 Ready/合并的 findings

### Finding 1：架构门禁可被 package shim 和常见导入写法绕过

相关代码：

- `scripts/check_architecture_growth.py:16-30`
- `scripts/check_architecture_growth.py:242-272`
- `scripts/check_architecture_growth.py:285-293`
- `tests/test_architecture_growth.py:135-161`

已复现两类漏检：

1. `REMOVED_INTERNAL_MODULE_PATHS` 只检查 `src/vega/memory.py` 等单文件，不检查
   `src/vega/memory/__init__.py`。因此可以恢复同名 package shim，而门禁返回空问题列表。
2. Core → Experimental 检查能识别 `from .experimental import memory`，但不能识别
   `from . import experimental` 或 `from vega import experimental`。

影响：

- PR 的主要交付之一是冻结核心与实验边界；当前门禁在常见 Python 写法下不能兑现该合同。
- wheel/sdist smoke 只显式检查 `vega.assurance`，其余已移除旧路径没有完整 package-level
  校验。

建议修复：

1. 将已移除模块表示为模块名，而不是只保存 `.py` 路径；同时拒绝同名 `.py` 和 package
   目录。
2. 检查 `ImportFrom.names`，覆盖 `from . import experimental` 与
   `from vega import experimental`。
3. 对全部已移除路径做参数化负向测试，并至少增加一个 wheel 安装后的多路径 smoke。

最小复现：

```powershell
$env:PYTHONPATH = "src"
@'
import importlib.util
import sys
import tempfile
from pathlib import Path

repo = Path.cwd()
(repo / ".tmp").mkdir(exist_ok=True)
script = repo / "scripts" / "check_architecture_growth.py"
spec = importlib.util.spec_from_file_location("review_architecture_growth", script)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory(dir=repo / ".tmp") as temp_dir:
    root = Path(temp_dir)
    shim = root / "src" / "vega" / "memory" / "__init__.py"
    shim.parent.mkdir(parents=True)
    shim.write_text("from ..experimental.memory import MemoryLedgerStore\n", encoding="utf-8")
    print(module._removed_internal_module_issues(root))

for source in ("from . import experimental\n", "from vega import experimental\n"):
    with tempfile.TemporaryDirectory(dir=repo / ".tmp") as temp_dir:
        root = Path(temp_dir)
        core = root / "src" / "vega" / "core_runtime.py"
        core.parent.mkdir(parents=True)
        core.write_text(source, encoding="utf-8")
        print(module._core_import_issues(root))
'@ | python -
```

当前错误输出是三个空列表；修复后都必须返回确定性问题。

### Finding 2：SQLite 安全双生 oracle 可接受实际数据破坏

相关代码：

- `scripts/run_assurance_stage2_sqlite_experiment.py:97-134`
- `scripts/run_assurance_stage2_sqlite_experiment.py:208-214`
- `tests/test_assurance_stage2_sqlite_experiment.py:29-50`

`_case_result()` 只比较行 ID 是否为 `[1, 2]`。`display_name`、`external_id`、
`schema_mode` 和最终 `data_invariant` 虽然写入 artifact，但没有参与 `passed-local` 判定。

已用负向控制复现：在 migration wrapper 首次执行后把两行 `display_name` 都更新为
`CORRUPTED`，四格矩阵仍全部 `passed=True`，安全双生仍为 `passed-local`，总体仍为
`continue-experiment`。

影响：

- 当前实验可以对破坏实际数据的 migration 产生假阳性。
- `eval/assurance-validation.md` 中“`id` 与 `display_name` 基线不变”的本次真实观察没有被
  决策 oracle 强制保证；后续重放不能只看顶层 decision。

建议修复：

1. 为四个矩阵格分别定义完整期望行内容。
2. 明确校验旧 schema 下 `external_id=None`、`schema_mode=old_fallback`，新 schema 下
   `external_id=None`、`schema_mode=expanded`。
3. 将最终 `customer_ids` 和 `display_names` 与 migration 前基线做确定性比较，并把结果纳入
   `passed-local`。
4. 增加一个会破坏 `display_name` 的负向测试，要求安全双生和总体结论降为
   `inconclusive`。

最小复现：

```powershell
$env:PYTHONPATH = "src"
@'
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

repo = Path.cwd()
(repo / ".tmp").mkdir(exist_ok=True)
script = repo / "scripts" / "run_assurance_stage2_sqlite_experiment.py"
spec = importlib.util.spec_from_file_location("review_stage2_oracle", script)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
original_apply = module._apply_expand_only_migration

def corrupting_apply(connection):
    status = original_apply(connection)
    if status == "applied":
        connection.execute("UPDATE customer SET display_name = 'CORRUPTED'")
        connection.commit()
    return status

module._apply_expand_only_migration = corrupting_apply
with tempfile.TemporaryDirectory(dir=repo / ".tmp") as temp_dir:
    previous = Path.cwd()
    os.chdir(temp_dir)
    try:
        result = module.run_experiment(Path(".local-validation") / "case")
    finally:
        os.chdir(previous)

safe = result["safe_twin"]
print(safe["decision"])
print(result["overall_decision"])
print(safe["data_invariant"])
'@ | python -
```

当前错误输出包含：

```text
passed-local
continue-experiment
{'customer_ids': [1, 2], 'display_names': ['CORRUPTED', 'CORRUPTED']}
```

### Finding 3：`.local-validation/` 根目录链接可把输出重定向到工作目录外

相关代码：

- `scripts/run_assurance_stage2_sqlite_experiment.py:242-249`
- `tests/test_assurance_stage2_sqlite_experiment.py:53-63`
- `docs/ASSURANCE-STAGE2-SQLITE-EXPERIMENT.md:64-84`

`_validate_output_dir()` 先对允许根目录调用 `resolve()`。如果 `.local-validation` 本身是指向
外部目录的 symlink 或 Windows junction，允许根会直接变成外部真实路径，随后
`candidate.relative_to(root)` 仍然成功。

已在 Windows 使用目录符号链接复现：传入 `.local-validation/case` 后，
`result.json` 实际出现在当前工作目录外的链接目标中，顶层实验仍正常返回。

影响：

- 违反“输出只能位于当前仓库 `.local-validation/` 下”的显式合同。
- 当前测试只覆盖普通 `outside` 相对路径，没有覆盖链接根目录。

建议修复：

1. 将允许根绑定到当前工作目录下的词法路径，并拒绝允许根或其已有父组件为
   symlink/reparse point。
2. 创建目录前后都重新确认边界，避免检查与创建之间的替换。
3. POSIX 增加 symlink 测试；Windows 增加 junction 或 directory symlink 测试，不能因
   “当前平台不方便”把已支持环境中的危险案例静默算作通过。

最小复现：

```powershell
$env:PYTHONPATH = "src"
@'
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

repo = Path.cwd()
(repo / ".tmp").mkdir(exist_ok=True)
script = repo / "scripts" / "run_assurance_stage2_sqlite_experiment.py"
spec = importlib.util.spec_from_file_location("review_stage2_link", script)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory(dir=repo / ".tmp") as temp_dir:
    base = Path(temp_dir)
    workspace = base / "workspace"
    outside = base / "outside"
    workspace.mkdir()
    outside.mkdir()
    link = workspace / ".local-validation"
    link.symlink_to(outside, target_is_directory=True)
    previous = Path.cwd()
    os.chdir(workspace)
    try:
        module.run_experiment(Path(".local-validation") / "case")
    finally:
        os.chdir(previous)
    print((outside / "case" / "result.json").is_file())
    link.unlink()
'@ | python -
```

当前错误输出为 `True`。如果平台不允许创建符号链接，应明确记录未验证，不能记录为安全。

## 3. 已审查且暂未发现阻塞问题的区域

- `loop_runtime.py` 的累积 `iteration_state` 重构与旧分支逐段构造的字段语义一致；现有
  中断、scope gate、verification 和 reviewer 回归在审查基线 CI 中通过。
- 原 `goal_evidence.py` 的 53 个顶层定义中，49 个函数 AST 保持不变；其余核心/Goal
  拆分未发现 fail-closed 成功语义放松。
- Goal 对 Loop、Gate、Finish 证据仍重新校验状态、身份、freshness 和 artifact integrity。
- `eval/assurance-validation.md` 本分支只在末尾追加 87 行，没有改写历史记录。
- 仓库卫生检查通过；差异中未发现普通工作区绝对路径、真实 `.env`、密钥或私密文件。
- Python 稳定接口限定为 `vega.__version__` 的文档、源码和 wheel/sdist smoke 方向一致。

这些结论表示“本轮未发现”，不表示对未覆盖平台、真实数据库或外部使用者作百分之百证明。

## 4. 下一台机器恢复与修复顺序

```powershell
git fetch origin
git switch -c refactor/lean-core --track origin/refactor/lean-core
git status -sb
git rev-parse HEAD
git ls-remote --heads origin refactor/lean-core
```

如果已有同名分支：

```powershell
git switch refactor/lean-core
git pull --ff-only
```

开始修改前先确认：

```powershell
$env:PYTHONPATH = "src"
python -c "import vega; print(vega.__file__)"
python -m pytest --collect-only -q -p no:cacheprovider
python scripts/check_repository_hygiene.py
git status --porcelain=v1 --untracked-files=all
```

必须打印当前 checkout 下的 `src/vega/__init__.py`。如果加载到其他 editable 安装目录，先修正
`PYTHONPATH`，不要把另一份仓库的结果写入本分支证据。

建议严格按以下三个小提交推进：

1. `修复：补齐核心实验边界门禁`
2. `修复：收紧 SQLite 安全双生判定`
3. `修复：阻止 Stage 2 输出目录链接逃逸`

每个提交只改一个问题和对应测试。三项完成前不要开始第二轮 `loop_runtime.py` 拆分。

## 5. 最小验证与 PR 门禁

定向验证：

```powershell
$env:PYTHONPATH = "src"
python -m pytest -q -p no:cacheprovider `
  tests/test_architecture_growth.py `
  tests/test_assurance_stage2_sqlite_experiment.py
python scripts/check_architecture_growth.py --base-ref origin/main
python scripts/check_repository_hygiene.py --base-ref origin/main
git diff --check origin/main...HEAD
```

完整本地门禁：

```powershell
python -m compileall -q src
ruff check src tests scripts/check_repository_hygiene.py scripts/check_architecture_growth.py scripts/run_assurance_stage2_sqlite_experiment.py --no-cache
python -m pytest -q -p no:cacheprovider
```

然后推送同一个 `refactor/lean-core`，等待最新 head 的 10 项 checks 全部完成。只有三项 findings
都有负向测试、最新 CI 全绿并经人工复核后，才讨论把 Draft 转为 Ready；不要自动合并，也不要
在验证阶段删除远端分支。
