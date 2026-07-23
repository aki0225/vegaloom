# 轻量核心精简分支接力说明

> 更新时间：2026-07-23
>
> 工作分支：`refactor/lean-core`
>
> Draft PR：[#8](https://github.com/aki0225/vegaloom/pull/8)
>
> 当前裁决：`continue-on-same-branch / manual-merge-only`

## 1. 基线与提交

- 主线基线：`origin/main@521f9b924241ec258c75b2ecc893bdaa3be91abd`
- 清单与决策：`d895e1a`，`文档：建立轻量核心清单与精简决策`
- 增量门禁：`1f20f64`，`工程：加入架构复杂度增量门禁`
- 非核心隔离：`5056c0f`，`重构：隔离非核心能力并精简 Loop 状态`
- Stage 2 实验：`5f62ad2`，`实验：加入 SQLite 迁移双生验证`
- 空白门禁修正：`49cfc65`，`文档：修正轻量核心计划空白门禁`

本文所在提交负责补全远端接力记录。后续修复继续推送同一个 `refactor/lean-core`，不要为
每轮 CI 新建微型分支。

## 2. 本轮完成范围

### 清单与门禁

- `docs/LEAN-CORE-PLAN.md` 已记录模块、命令、artifact、状态和依赖关系。
- 模块已标记为核心、可选实验或待废弃范围。
- `scripts/check_architecture_growth.py` 已阻止新增 C901、受控巨型模块继续增长，以及
  Core → Experimental 静态依赖扩散。

### 非核心隔离

- Assurance、Goal、Memory、Adapter 和兼容 Inspection Runtime 已移动到
  `src/vega/experimental/`。
- 默认 Loop 需要的证据逻辑保留在核心 `src/vega/loop_evidence.py`。
- 单次 run 的 Memory proposal artifact 保留在核心
  `src/vega/memory_artifacts.py`，长期 ledger 位于实验模块。
- CLI 对实验能力使用延迟导入；仓库内旧模块导入已更新。
- `src/vega/loop_runtime.py` 通过累积 `iteration_state` 去除一轮重复状态拼装，但没有重写
  编排语义。

量化结果：

- C901：`48 → 46`
- Python 模块：`47 → 54`
- `src/vega/cli.py`：`1038 → 1006` 行
- `src/vega/project_profile.py`：`672 → 667` 行
- `src/vega/loop_runtime.py`：`3485 → 3291` 行

### Stage 2 独立实验

- 新增 `scripts/run_assurance_stage2_sqlite_experiment.py`。
- 新增 `tests/test_assurance_stage2_sqlite_experiment.py`。
- 新增 `docs/ASSURANCE-STAGE2-SQLITE-EXPERIMENT.md`。
- 只向 `eval/assurance-validation.md` 末尾追加 `AV-STAGE2-001`，未改写历史记录。
- 未注册 Vega CLI、未写默认 `runs/`、未新增默认状态或成功条件。

## 3. 本地验证证据

本地环境为 Windows、Python `3.14.3`。它不能替代 PR 中的 Python 3.11、3.12、POSIX、
Windows 和 wheel/package CI。

静态门禁：

- `python -m compileall -q src`：通过。
- `ruff check ... --no-cache`：通过。
- `python scripts/check_architecture_growth.py --base-ref origin/main`：通过。
- `python scripts/check_repository_hygiene.py`：通过。
- `git diff --check origin/main...HEAD`：通过。
- CI YAML 可解析。
- 完整收集：`613 tests collected`。

CI 同构唯一分片覆盖全部 613 个节点：

- smoke：`102 passed`
- p0-cli-lock：`109 passed`
- artifacts-runtime-security：`59 passed, 1 skipped`
- semantics-evidence-review：`141 passed`
  - Assurance Stage 1 + Stage 2：`61 passed`
  - Assurance verification semantics：`14 passed`
  - evidence freshness：`19 passed`
  - review artifact integrity：`18 passed`
  - success semantics：`29 passed`
- remaining：`201 passed`

`tests/test_recovery_chaos.py` 已包含在 remaining 中，并另行复跑为 `10 passed`。第一次
p0-cli-lock 分片暴露 `test_cli_recovery_hardening.py` 仍 monkeypatch 旧导入路径；修正后完整
重跑为 `109 passed`。本地保留了第一次失败日志，没有把它计为通过。

Draft PR 首个 head `5f62ad2` 的静态任务在全分支 whitespace 门禁失败，原因是计划文档顶部
使用 Markdown 双空格换行。该问题由 `49cfc65` 修正。旧 head 的失败必须保留为失败记录，
不能被后续重跑覆盖或描述为通过。

本地日志位于 `.tmp/pytest/lean-core/`，该目录不提交；另一台机器应按本文命令重新生成。

## 4. SQLite 实验结论

- 危险 migration：detector 拒绝，SQLite 实际执行失败，schema/data 与基线一致，判定
  `reject`。
- 安全双生：四格兼容矩阵通过，首次 wrapper 为 `applied`，第二次为
  `already_present`，判定 `passed-local`。
- 总体结论只能是 `continue-experiment`。

本地机器事实：

- `.local-validation/assurance-stage2-sqlite-20260723-142449/result.json`
  - SHA-256：
    `D6CE0892952DEC6C77855CCEFFD16BFBDE5D59A72F0FDCEA7B84406A39B62067`
- `.local-validation/assurance-stage2-sqlite-20260723-142449/report.md`
  - SHA-256：
    `5ED4631A0B67AB083CB033E93F2D44A0E3DED3D3AB08134DC82492C211882D3B`

`.local-validation/` 不提交。其他机器可重放实验，但新生成 artifact 的时间和哈希允许不同；
必须重新核对结构化结论，不能复制本机结论冒充 live evidence。

## 5. 主线评估仍需关注的风险

1. 每个新 head 仍必须完成 Python 3.11/3.12、Windows、POSIX 和 wheel/package CI。
2. `src/vega/loop_runtime.py` 仍有 3291 行，巨型状态机债务没有消失。
3. 当前仍有 46 个 C901；本轮只禁止增长并减少两个，不代表复杂度已经健康。
4. 已决定旧 Python 模块路径属于内部实现，不恢复兼容 shim。仓库外偶然导入旧路径的代码需要
   迁移；下一次发布说明必须明确该边界。
5. 实验模块仍由既有 CLI 命令延迟加载，当前是隔离依赖而非删除产品表面。
6. SQLite 个案没有覆盖 PostgreSQL/MySQL、锁、在线索引、backfill、并发写、恢复、复制延迟
   或真实生产规模。

## 6. 另一台机器恢复

```powershell
git fetch origin
git switch -c refactor/lean-core --track origin/refactor/lean-core
git status -sb
git log --oneline --decorate -6
```

如果本地已经有同名分支：

```powershell
git switch refactor/lean-core
git pull --ff-only
```

恢复后先验证远端 head 和工作区：

```powershell
git rev-parse HEAD
git ls-remote --heads origin refactor/lean-core
git status --porcelain=v1 --untracked-files=all
```

最小门禁：

```powershell
python -m compileall -q src
ruff check src tests scripts/check_repository_hygiene.py scripts/check_architecture_growth.py scripts/run_assurance_stage2_sqlite_experiment.py --no-cache
python scripts/check_repository_hygiene.py
python scripts/check_architecture_growth.py --base-ref origin/main
python -m pytest --collect-only -q -p no:cacheprovider
git diff --check origin/main...HEAD
```

重放 Stage 2：

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
python scripts/run_assurance_stage2_sqlite_experiment.py `
  --output-dir ".local-validation/assurance-stage2-sqlite-$stamp"
python -m pytest -q -p no:cacheprovider `
  --basetemp ".tmp/pytest/runs/lean-core-stage2" `
  tests/test_assurance_stage2_sqlite_experiment.py
```

## 7. PR 与主线门禁

Draft PR #8 在最新 handoff 提交推送后会重新运行 CI。按以下顺序处理：

1. 等待最新 head 的全部 required checks 完成。
2. 任一检查失败时，在同一 `refactor/lean-core` 分支修复并重跑，不新建分支。
3. 检查 Python 3.11/3.12、Windows、POSIX、wheel 安装和 package smoke 是否全部通过。
4. 确认 Python 接口边界测试和“禁止恢复旧 shim”的架构门禁通过。
5. 为下一次发布准备内部模块路径迁移说明。
6. 只有上述条件满足后，才把 Draft 转为 Ready for review；仍不要自动合并。
7. 最终人工合并后再删除远端分支；Draft 和验证阶段保留该分支用于接力。

## 8. 建议的下一步

先不要继续扩大 Stage 2 Threat Family。兼容性决策已经完成，下一步只做第二轮 Loop 精简设计：

1. 画出 `loop_runtime.py` 的阶段边界和可提取纯函数清单。
2. 先补 characterization tests，再拆一个最小阶段。
3. 不立即重写整个状态机，不新增默认命令、状态或成功条件。
