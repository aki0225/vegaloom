# Assurance Stage 3 有界 DML/Backfill 接力说明

> 日期：2026-07-24
>
> 分支：`experiment/assurance-stage3-dml-backfill`
>
> 实现提交：`c7122d3ce41407beeb59b2285b07ee910b6ea52e`
>
> 审查修复提交：`25a7efc0058de62d2fc665c99b501f890ff5d3e9`
>
> 状态：`review-findings-fixed / implementation-head PR CI 10/10 passed /
> latest-head live CI is the merge gate`

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
- SQL scope detector 只把独立列名 `id = ?` 识别为目标约束，不再把
  `tenant_id = ?` 中的后缀误判为目标 ID；
- 每条 evidence 都绑定全部声明 artifact 的 SHA-256，单独篡改 oracle JSON 会使绑定失效；
- policy 文件缺失、不可读或哈希不是实际 64 位 SHA-256 时，snapshot 直接 fail-closed。

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

后续补充 transaction、checkpoint metadata、reconciliation、missing target scope、
oracle artifact tampering 和 policy hash sentinel 负向控制后，当前 Stage 3 文件共有
`23` 个测试节点。

### 审查修复提交验证

绑定 `25a7efc` 的本地结果：

```text
Stage 1 + Stage 2 + Stage 3 + repository/architecture targeted: 158 passed
Stage 3 targeted: 23 passed
full collection: 691 tests collected
compileall src + registered experiment scripts: passed
Ruff src + tests + registered experiment scripts: passed
Ruff C901 on Stage 3 script: passed
architecture growth: passed, C901 46->46, Python modules 54->54
repository hygiene: passed
git diff check: passed
```

Stage 3 最初并行运行四个本地分片时，三个分片因共享 Windows 环境资源争用超过 60 秒，
不能记为通过。随后改为四个互斥 basetemp 的串行分片，明确得到
`6 + 6 + 6 + 5 = 23 passed`；该失败尝试保留为调度事实，不包装成测试失败或通过。

### clean-head artifact

在工作区干净时执行：

```powershell
python scripts/run_assurance_stage3_dml_backfill_experiment.py `
  --output-dir .local-validation/assurance-stage3-dml-backfill-20260724-review-fix
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
snapshot.head: 25a7efc0058de62d2fc665c99b501f890ff5d3e9
verified declared artifact bindings: 10
```

SHA-256：

```text
result.json:
883C133F0790AD7F3793F6F774A796D95CF4077CF5A463F5FAFE9A0E733D0EDA

report.md:
67BABCFE2836F3E3B900E4E480F899852ABA90047C0AA56C1149642DEA55B630
```

该目录被 `.gitignore` 忽略，不会推送。artifact 的文本文件通过本机路径与凭据扫描，六条
evidence 共十个声明 artifact 均按记录哈希重新核对通过。

### PR CI

PR `#13` 的实现修复 head `25a7efc` 对应 workflow `30099248716`：

```text
10/10 jobs success
Python 3.11 full suite: success
Python 3.12 five shards: success
Windows + wheel smoke: success
POSIX temp-dir checks: success
wheel/sdist build and package smoke: success
static checks and 691-node collection contract: success
```

## 四、未通过或尚未完成的验证

以下事实仍不能扩大解释：

1. 单进程全量 pytest 运行超过 30 分钟外层预算，只到约 `31%`，进程已明确终止；
2. Windows 本地运行完整 semantics shard 时，
   `test_loop_eval_rejects_superseded_terminal_without_state_binding`
   触发 `58s` timeout；
3. 同一节点随后独立重跑为 `1 passed in 48.65s`，说明本地失败更像共享 Windows 环境的
   时序/性能问题，但不能据此把完整 shard 记为通过；
4. 本地没有重新声明单进程全量测试通过；完整跨平台门禁来自同一实现修复提交的隔离 PR CI；
5. 合并前必须读取 PR 最新 head 的实时 10 项 CI，不能沿用 `25a7efc` 的状态；CI 通过后只在
   PR 描述或评论中记录最终 head，不再为了追逐新 SHA 追加仓库文档提交。

因此当前裁决是：

```text
review-findings-fixed
implementation-head-pr-ci-passed
latest-head-ci-must-be-green-at-merge
full-local-suite-not-established
continue-experiment
requires_staged_rollout
do-not-integrate
manual-merge-candidate-only
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

1. 推送本 docs-only 接力提交，并确认 Draft PR 最新 head 的 10 项 CI；任何失败都保留为事实；
2. CI 全绿后做最终只读复核，重点核对：
   - Stage 3 未进入 `src/vega/`、CLI、Runtime、Finish、Goal 或成功语义；
   - `eval/` 相对主线只有追加；
   - artifact 和控制台不含绝对工作区路径；
   - 子进程退出、transaction rollback、checkpoint mismatch 和 oracle tampering 测试真实运行；
3. 最新 head 10/10 全绿且上述边界成立后，只更新 PR 描述或评论记录最终 SHA，不再追加
   “CI 已通过”文档提交；PR 才能从 Draft 转为人工合并候选；
4. 不自动合并，不删除实验分支，不启动 Stage 4；
5. `scripts/run_assurance_stage3_dml_backfill_experiment.py` 继续保持冻结实验脚本。只有未来要接
   Runtime 或支持通用 SQL/backfill 时，才需要先拆分并替换字符串级 SQL detector。
