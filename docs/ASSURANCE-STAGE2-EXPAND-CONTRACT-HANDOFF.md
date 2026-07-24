# Assurance Stage 2 expand/backfill/contract 接力说明

> 日期：2026-07-24
>
> 分支：`experiment/assurance-stage2-backfill`
>
> 基线：`main@0280b9f6df0205261a489e1fd67c6b574684cb64`
>
> 当前裁决：`review-findings-fixed-targeted / pr-ci-required / do-not-integrate`

## 一、先看结论

`AV-STAGE2-002` 已完成本地候选实现。2026-07-24 的独立审查又复现了三个会降低证据可信度
的问题：共享行读取 helper 可同时欺骗应用层和 oracle、contract 完整性判定会忽略既有约束
丢失、重复值的专用 detector 分类不可达。三项红灯现已由 16 个定向测试关闭。

当前 head 的静态门禁和 667 节点收集合同通过，但本机完整执行没有形成合格的最终汇总；
2026-07-23 的 `663 passed, 1 skipped` 只适用于修正前旧 head，不能复用为当前 head 证据。
下一步必须创建 Draft PR，使用同一最新 head 的跨平台 CI 关闭完整测试：

- 危险顺序 `expand -> contract -> backfill` 会被 detector 标记，并被 SQLite 实际拒绝；
- 安全顺序 `expand -> bounded fixture backfill -> contract` 在固定两行 fixture 上通过；
- rollback、幂等、兼容矩阵、独立 SQL oracle 和负向敏感性在本实验范围内有效；
- 实验没有接入 Vega Runtime、默认 CLI 或成功语义。

它不能证明生产 migration 安全，也不能直接合并或集成。等待最新 head 的跨平台 CI、
做 post-CI 只读复核并追加最终裁决前，不得扩大到 Stage 3。

## 二、本轮实现

代码与测试：

- `scripts/run_assurance_stage2_expand_contract_experiment.py`
  - 执行危险/安全 SQLite 双生；
  - 数据准备只允许更新冻结的 `id=1/2`；
  - detector 失败时安全路径不执行 contract；
  - 独立 oracle 不复用应用层、detector 或 contract 的读取 helper；
  - 独立 oracle 校验完整列约束、非 partial `UNIQUE`、精确行映射和零临时表；
  - contract 完整性判定保留 `id PRIMARY KEY` 与 `display_name NOT NULL`；
  - 重复 `external_id` 使用专用 detector issue；
  - 输出只能写到仓库 `.local-validation/`，拒绝 symlink、junction 和 reparse point；
  - artifact 固定保持 `inconclusive / insufficient`，外部门禁未关闭时返回退出码 `1`。
- `tests/test_assurance_stage2_expand_contract_experiment.py`
  - 覆盖危险/安全顺序、部分数据准备、错误映射、掩盖读取层、错误约束、临时表残留、
    frozen ID scope、输出路径逃逸和链接路径；
  - 新增共享读取 helper 掩盖、既有约束丢失和重复值分类三个负向回归；
  - 直接覆盖 `main()` 的非零退出码、相对 artifact 路径和控制台路径脱敏；
  - 当前结果：`16 passed`。
- `.github/workflows/ci.yml`
  - 编译和 Ruff 纳入新脚本；
  - Linux 收集合同更新为 `667`；
  - Python 3.12 和 Windows 分片纳入新测试文件。

证据记录：

- 预注册合同：
  `docs/ASSURANCE-STAGE2-EXPAND-CONTRACT-EXPERIMENT.md`
- 追加式验证：
  `eval/assurance-validation.md` 的 `AV-STAGE2-002 · local candidate result`
- 本文只负责跨机器接力，不替代机器事实或 eval 裁决。

## 三、2026-07-23 旧 head 本地证据

以下证据对应修正前的 `44ab56f`，只保留为历史记录。2026-07-24 的审查修正改变了脚本和
测试，因此这些数字和哈希不能作为当前 head 的完整通过证据。

完整自适应分片：

```text
collected: 664
passed: 663
skipped: 1
failed: 0
timed_out: 0
```

机器可读汇总位于：

```text
.tmp/validation/stage2-adaptive-v2/summary.json
```

SHA-256：

```text
50EAFEFCEB3CB38DEA17440C434B8D21E983AC33C7180FC2729150A0DB3D00E3
```

唯一 skip 是 Windows 本地无法执行的 POSIX shell 变量展开专项，Linux CI 必须实际运行。

最终静态与结构门禁：

```text
compileall src scripts: passed
Ruff src tests scripts: passed
architecture growth: passed, C901 46->46, Python modules 54->54
repository hygiene: passed
final collection: 664 tests collected
git diff check: passed
```

最终本地实验：

```text
.local-validation/assurance-stage2-expand-contract-20260723-final/
```

```text
overall_decision: inconclusive
candidate_decision: continue-experiment
evidence_adequacy: insufficient
runtime_integration: disabled
external_quality_gates.status: not_evaluated
dangerous_twin.decision: reject
safe_twin.decision: candidate-passed-local
script exit code: 1
```

哈希：

```text
result.json:
6AA739107A360035AD34DB412D9FEC0B3CEFFFF842F0AE96063296F660DBB35E

report.md:
42843CF1CD39A58A28C82157BC888F750621FA405EDC4CCDC3992A94795F2DDD
```

`.tmp/`、`.local-validation/` 和 SQLite 文件都不提交。

## 四、无效证据

以下 artifact 早于最终裁决分层修正，不得引用哈希或作为通过证据：

```text
.local-validation/assurance-stage2-expand-contract-20260723-232008/
.local-validation/assurance-stage2-expand-contract-20260723-234037/
```

首轮完整分片使用过长 Windows `basetemp`，产生 `WinError 206` 和衍生锁错误；该轮全部作废。
有效汇总只来自数字短路径的 `stage2-adaptive-v2`。

## 五、当前继续：创建 Draft PR 并关闭 CI

在另一台机器恢复：

```powershell
git fetch origin
git switch experiment/assurance-stage2-backfill
git pull --ff-only
git status --short --branch
git log --oneline --decorate -5
```

先执行本地最小复核：

```powershell
python -m compileall src scripts
ruff check src tests scripts --no-cache
python scripts/check_architecture_growth.py --base-ref origin/main
python scripts/check_repository_hygiene.py --base-ref origin/main
python -m pytest --collect-only -q
git diff --check
```

然后创建 Draft PR，目标为 `main`。不要自动合并。等待同一最新 head 的全部任务完成，至少核对：

1. Python 3.11 全量测试。
2. Python 3.12 分片测试。
3. Windows 专项测试。
4. Linux POSIX 临时环境变量专项没有 skip。
5. wheel/sdist 构建、安装和 package smoke。
6. 仓库卫生、架构增量、编译、Ruff 和 `667` 节点收集合同。

CI 全绿后做一次只读复核，确认：

- workflow 对应 PR 最新 head，不是旧提交；
- 新脚本仍未注册默认 `vega` CLI；
- `runtime_integration` 仍为 `disabled`；
- artifact 自身仍保持 `inconclusive / insufficient`；
- PR 没有提交 `.tmp/`、`.local-validation/`、SQLite、`runs/`、`memory/`、`.env` 或本机路径；
- 唯一 POSIX 专项已在 Linux 真正执行。

最后只向 `eval/assurance-validation.md` 末尾追加 post-CI 记录。外部门禁全部关闭后，组合裁决
最多提升为：

```text
continue-experiment / requires_staged_rollout / do-not-integrate
```

这个提升属于 eval 的组合证据，不回写或伪造本地 `result.json`。

## 六、仍未证明

- PostgreSQL/MySQL、在线 DDL、锁时间和复制延迟。
- 并发写入、真实流量、生产数据规模和滚动部署。
- 通用 backfill runner、租户 scope、row budget、checkpoint、恢复和 reconciliation。
- 恶意并发进程在最终路径复检后替换目录的 TOCTOU 防护。
- Runtime 成功语义、自动 patch、自动 commit、自动部署或长期 Memory 写入。

这些属于后续独立实验或 Stage 3/4，不在本分支继续实现。

## 七、2026-07-24 独立审查修正

### 红灯

修正前的定向测试结果为：

```text
3 failed, 13 passed
```

三个失败分别证明：

1. 错误 backfill 与共享 `_stored_rows` 掩盖同时存在时，旧 oracle 会读取同一份伪装结果，
   实际数据库为 `wrong-0001/wrong-0002` 仍可能得到 `candidate-passed-local`；
2. contract 表删除 `id PRIMARY KEY` 与 `display_name NOT NULL` 后，旧完整性判定仍会返回
   `already_contracted`，旧 oracle 也会通过；
3. `external_id_not_unique` 检查位于精确映射比较之后，因此重复值只能得到宽泛的
   `external_id_mapping_mismatch`，专用分支不可达。

### 最小修复

- oracle 在新连接中独立执行 `PRAGMA`、行查询、`COUNT` 和索引查询，不复用被测 helper；
- contract 完整性与 oracle 都核对完整冻结列定义和既有约束；
- detector 先检查非空与唯一性，再检查精确映射；
- 新增三个负向回归，CI 收集合同由 `664` 更新为 `667`。

### 当前本地结果

```text
Stage 2 定向：16 passed
compileall src scripts：passed
Ruff src tests scripts：passed
architecture growth：passed, C901 46->46, Python modules 54->54
repository hygiene：passed
collection：667 tests collected
git diff check：passed
```

修正后 artifact 重放：

```text
.local-validation/assurance-stage2-expand-contract-20260724-review-final/

overall_decision: inconclusive
candidate_decision: continue-experiment
evidence_adequacy: insufficient
runtime_integration: disabled
external_quality_gates.status: not_evaluated
dangerous_twin.decision: reject
safe_twin.decision: candidate-passed-local
safe_twin.oracle.passed: true
safe_twin.oracle.schema_columns_passed: true
script exit code: 1
```

SHA-256：

```text
result.json:
29BC11A9E060ED547665050E0F6B3123134A0A25F96C88D3EE51F4AD1BA20442

report.md:
90D09243024EA7BE00AD86C4FFB640274838FED12E4F92C92B3B3E0FA16A8B05
```

完整本地执行没有形成可采信的最终汇总：

- 单进程全量运行在 20 分钟外层预算耗尽；
- 后续直接 smoke 与自适应分片均遇到同机并行 pytest 的资源竞争；
- 第一版自适应脚本还因未预建 `basetemp` 父目录产生环境性 setup error，整轮作废；
- 第二版只完成部分节点并记录多个 60 秒 timeout，已主动终止，不计为通过或产品失败。

因此当前裁决保持：

```text
review-findings-fixed-targeted / pr-ci-required / do-not-integrate
```

当前 head 的完整测试只能由最新 Draft PR 的隔离 CI 关闭；不得引用旧 head 的本地汇总补齐。
