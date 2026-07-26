# 主线可信执行加固交接

> 日期：2026-07-26
> 分支：`codex/mainline-trust-hardening`
> 基线：`origin/main@7805bba`
> 状态：实现完成，等待最终全量测试汇总与远端 CI

## 本轮完成内容

1. 工作区证据覆盖 tracked、untracked、ignored 和 Git 控制文件，预算不完整时
   fail-closed，不再把不完整指纹解释为可信证据。
2. Windows 执行控制增加三态进程探测、creation token、PID 复用保护、命名 Job Object
   和进程树终止确认。
3. Reviewer verdict 改为严格单一结构；Review、Verification、Goal freshness 和 Finish
   使用统一的结构化证据与成功判定。
4. scope 匹配同时遵循 Git `core.ignorecase` 与实际文件系统语义。
5. Reflect、Gate、tracked-only 项目配置、AGENTS、项目画像、仓库身份和 inspection
   allowlist 统一通过 `src/vega/git_read.py` 读取 Git。
6. Git 读取禁用 system/global 配置、fsmonitor、untracked cache 和 external diff；
   diff 额外禁用 textconv。
7. 核心模块拆分后，架构门禁由 `C901 46` 降为 `41`，Python 模块由 `55` 增至 `68`，
   没有引入新的 Runtime、数据库、Memory 或多 Agent 能力。

## 当前验证证据

改动前的同一工作树已完成一次全量分片验证：

```text
762 collected
757 passed
5 skipped
```

完成 Git 读取统一后的当前工作树重新收集：

```text
772 collected
```

当前已对直接受影响文件和关键 P0/Runtime 路径取得明确退出码：

```text
501 passed
4 skipped
```

其中包括：

- `tests/test_smoke.py`：`115 passed`
- `tests/test_security_evidence.py`：`26 passed`
- `tests/test_context_boundaries.py`：`33 passed, 3 skipped`
- `tests/test_cli_recovery_hardening.py`：`40 passed, 1 skipped`
- `tests/test_review_artifact_integrity.py`：`23 passed`
- `tests/test_workspace_snapshot_budget.py`：`14 passed`
- Git/ignored/scope/Reflect/Gate 的 P0 定向：`9 passed`
- Runtime 工作区与 Gate 定向：`12 passed`

静态与仓库门禁：

```text
compileall: passed
ruff: passed
repository hygiene: passed
architecture growth: passed
git diff --check: passed
```

架构脚本在本机需要仅当前进程生效的 `safe.directory` 配置，因为仓库目录所有者为
Administrators；没有修改用户全局 Git 配置。

## 尚未完成

代码层没有已知未修复项。合并前仍需为最后的 Git 读取补丁补齐一次完整的
post-change pytest 汇总。以下 11 个文件尚未在最后补丁后完整跑完；部分定向节点已经通过：

- `tests/test_assurance_stage1_contract.py`
- `tests/test_assurance_stage3_dml_backfill_experiment.py`
- `tests/test_assurance_verification_semantics.py`
- `tests/test_evidence_freshness.py`
- `tests/test_execution_control_safety.py`
- `tests/test_finish_artifact_integrity.py`
- `tests/test_p0_regressions.py`
- `tests/test_recovery_chaos.py`
- `tests/test_run_mutation_lock.py`
- `tests/test_runtime_safety_integration.py`
- `tests/test_success_semantics.py`

本机不要并行运行这些大文件。并行时多个分片会因 Windows 资源争用超过 60 秒；
应按文件或完整 node id 集合串行运行，并给每片分配独立 `--basetemp`。

## 明天继续步骤

1. 拉取并切换到 `codex/mainline-trust-hardening`。
2. 串行补跑上述 11 个文件，必要时按 5 个 node 一组分片。
3. 核对最终总账必须等于 `772 collected = passed + skipped`，且 `failed = 0`。
4. 重跑 compileall、Ruff、仓库卫生、架构增长和 `git diff --check`。
5. 查看本分支远端 CI；本地与 CI 都通过后，再创建或合并主线 PR。
6. MA 实验继续留在独立实验分支，不从本分支 cherry-pick 或混入主线修复。
