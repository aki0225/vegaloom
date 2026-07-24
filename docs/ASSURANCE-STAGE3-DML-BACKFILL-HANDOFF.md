# Assurance Stage 3 有界 DML/Backfill 接力说明

> 日期：2026-07-24
>
> 分支：`experiment/assurance-stage3-dml-backfill`
>
> 实现提交：`c7122d3ce41407beeb59b2285b07ee910b6ea52e`
>
> 状态：`local-candidate / Draft PR and cross-platform CI pending`

## 一、当前结论

`AV-STAGE3-001` 已完成固定 SQLite 双租户 fixture 的第一版实验实现：

- 危险 UPDATE 缺少租户和目标 ID 约束，detector 会在写入前拒绝；
- 独立危险数据库强制执行后，SQL oracle 能直接观察到 `id=201` 被越界修改；
- 安全路径 dry-run 为零写入，只接受 `[101, 102]`、两行预算和单行 batch；
- 独立子进程在提交 `id=101` 后以退出码 `97` 硬退出；
- 恢复过程不信任 checkpoint 的完成计数，重新读取数据库后只处理 `id=102`；
- 第三次执行更新零行；
- 最终 oracle 精确检查目标映射和 `id=201/202` 的完整范围外快照；
- batch 行更新与 checkpoint 更新位于同一 SQLite transaction。

实现仍保持实验隔离：

- 没有修改 `src/vega/`；
- 没有新增 `vega` CLI、默认 Loop、Runtime 状态或成功条件；
- 没有写入默认 `runs/`；
- artifact 固定保持
  `inconclusive / insufficient / runtime_integration=disabled`；
- 脚本退出码为 `1`，因为外部质量门禁尚未评估。

## 二、文件范围

实现提交只包含：

- `scripts/run_assurance_stage3_dml_backfill_experiment.py`
- `tests/test_assurance_stage3_dml_backfill_experiment.py`
- `.github/workflows/ci.yml`

本接力提交另外只更新：

- `docs/ROADMAP.md`
- `eval/assurance-validation.md`
- 本文件

`eval/assurance-validation.md` 只在末尾追加事实，没有改写历史。

## 三、当前可采信的本地证据

### 测试先行红灯

实现脚本创建前，Stage 3 测试首次执行得到：

```text
14 failed
exit code: 1
共同根因：实验脚本尚不存在
```

原始输出位于忽略目录：

```text
.tmp/pytest/stage3-red.txt
```

后续补充 transaction、checkpoint metadata 和 reconciliation 负向控制后，当前 Stage 3
文件共有 `20` 个测试节点。

### 当前实现提交验证

绑定 `c7122d3` 的本地结果：

```text
Stage 1 + Stage 2 + Stage 3 + repository/architecture targeted: 155 passed
Stage 3 targeted: 20 passed
full collection: 688 tests collected
compileall src + registered experiment scripts: passed
Ruff src + tests + registered experiment scripts: passed
Ruff C901 on Stage 3 script: passed
architecture growth: passed, C901 46->46, Python modules 54->54
repository hygiene: passed
git diff check: passed
```

### clean-head artifact

在工作区干净时执行：

```powershell
python scripts/run_assurance_stage3_dml_backfill_experiment.py `
  --output-dir .local-validation/assurance-stage3-dml-backfill-20260724-handoff
```

结果：

```text
script exit code: 1
overall_decision: inconclusive
candidate_decision: continue-experiment
safe_twin.decision: candidate-passed-local
safe_twin.evidence_bindings_valid: true
interruption.process_exit_code: 97
recovery.updated_ids: [102]
repeat.updated_ids: []
safe_twin.oracle.passed: true
snapshot.head: c7122d3ce41407beeb59b2285b07ee910b6ea52e
```

SHA-256：

```text
result.json:
2BB892547773B14D0D4917C55EF8DF0EC2F988BAAA9824FC3220D5A883BE34CA

report.md:
D871929E6E5F615627C9A48F322055C3645A66C1E9389440BAB82A94B3B6A053
```

该目录被 `.gitignore` 忽略，不会推送。它只绑定实现提交 `c7122d3`；接力文档提交会产生新的
branch head，因此最新 head 的完整测试和跨平台结果仍必须由 Draft PR CI 重新确认。

## 四、未通过或尚未完成的验证

以下尝试不能记为通过：

1. 单进程全量 pytest 运行超过 30 分钟外层预算，只到约 `31%`，进程已明确终止；
2. Windows 本地运行完整 semantics shard 时，
   `test_loop_eval_rejects_superseded_terminal_without_state_binding`
   触发 `58s` timeout；
3. 同一节点随后独立重跑为 `1 passed in 48.65s`，说明本地失败更像共享 Windows 环境的
   时序/性能问题，但不能据此把完整 shard 记为通过；
4. Python 3.11/3.12 全量、Windows、POSIX、wheel/sdist 和 package smoke 尚未绑定最新
   branch head。

因此当前裁决是：

```text
local-candidate
full-local-suite-not-established
draft-pr-ci-required
do-not-integrate
do-not-merge-yet
```

## 五、另一台电脑继续

```powershell
git fetch --prune origin
git switch --track origin/experiment/assurance-stage3-dml-backfill
git status -sb
git log -3 --oneline
```

如果本地已经有同名分支：

```powershell
git switch experiment/assurance-stage3-dml-backfill
git pull --ff-only
```

先复核：

```powershell
python -m pytest -q tests/test_assurance_stage3_dml_backfill_experiment.py
python -m pytest --collect-only -q
python scripts/check_repository_hygiene.py --base-ref origin/main
python scripts/check_architecture_growth.py --base-ref origin/main
git diff --check
```

再重放 artifact：

```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
python scripts/run_assurance_stage3_dml_backfill_experiment.py `
  --output-dir ".local-validation/assurance-stage3-dml-backfill-$stamp"
```

退出码 `1` 是预期的 fail-closed 结果；只有输出中的
`candidate=continue-experiment`、`safe_twin.decision=candidate-passed-local`、
`evidence_bindings_valid=true` 和独立 oracle 全部成立，才算本地候选重放成功。

## 六、下一步

1. 查看 Draft PR 最新 head 的 10 项 CI，任何失败都保留为事实；
2. CI 全绿后做一次只读 post-CI 审查，重点核对：
   - Stage 3 未进入 `src/vega/`、CLI、Runtime、Finish、Goal 或成功语义；
   - `eval/` 相对主线只有追加；
   - artifact 和控制台不含绝对工作区路径；
   - 子进程退出、transaction rollback、checkpoint mismatch 和 oracle tampering 测试真实运行；
3. 评估 `scripts/run_assurance_stage3_dml_backfill_experiment.py` 作为实验脚本的体积和重复结构；
   在行为证据稳定前不要抽象成通用 backfill runner；
4. 不自动合并，不删除实验分支。
