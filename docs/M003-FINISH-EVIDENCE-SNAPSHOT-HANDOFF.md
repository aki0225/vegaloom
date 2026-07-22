# M-003 Finish 证据快照复用接力

> 日期：2026-07-22
>
> 分支：`perf/finish-evidence-snapshot`
>
> 当前裁决：`passed-local / requires-ci / do-not-merge`

## 一、先看结论

M-003 已完成预注册红灯、最小实现、独立审阅修正和完整本地验证。同一次 Finish 现在只执行
一次终态 artifact integrity 校验，并让 `finish-summary.json` 同时消费该结果与 evidence
freshness。完整性、新鲜度、scope、risk 和 project policy 的终态重算没有删除或放宽。

当前不能合并：候选实现还需要公开 PR 的 Python 3.11、Python 3.12 分片、Windows、POSIX
和 wheel 构建安装 CI。Roadmap 在跨平台 CI 通过前继续保持 `M-003=next`。

关键提交：

- 基线：`da1ac290addd0042f8782476cdb5ece4e53f2aa8`
- 红灯预注册：`da5dcd65eef0e827f627b951df986a05a8708e89`
- 候选实现：`15924027000f78fd61139ce8a952aa32ccb23188`

## 二、实现内容

`src/vega/goal_evidence.py` 新增 `LoopEvidenceValidationSnapshot` 和
`validate_loop_evidence_snapshot()`：

1. 有可信 review 的正常路径先完成 workspace/review freshness，再在末端执行一次 artifact
   integrity，并把两者作为同一个快照返回。
2. Finish 不再先独立调用一次 integrity，然后又通过 freshness 间接调用第二次。
3. 没有 review 或 review run 不存在时，公开 `validate_loop_evidence_freshness()` 继续按旧合同
   直接早退，不新增完整 integrity 扫描。
4. Finish 在这些早退路径仍会补齐一次 integrity，从而保证快照结构完整，但不会把 integrity
   issue 混入原 freshness issue 集合。
5. 结果只在单次 Finish 调用内复用，不跨调用缓存，不写入长期 memory。

`src/vega/finish_runtime.py` 只消费该快照并继续使用原有 `build_finish_summary()`。Stage 1 的
Threat/Evidence 数据模型、Goal 路径优化和 runner 配置均未进入本改动。

## 三、预注册结果

旧实现：

```text
1 failed in 18.75s
E assert 2 == 1
```

候选实现最终节点：

```text
1 passed in 14.81s
```

该节点同时确认：

- Finish 内 artifact integrity 调用次数为 `1`。
- `finish_status=ready_to_commit`。
- artifact integrity、evidence freshness 和结构化 verification 均有效。
- risk gate 结果只收集一次。
- `review_run` 为空时，公开 freshness API 的 integrity 调用次数为 `0`。
- review run 不存在时，公开 freshness API 的 integrity 调用次数为 `0`。

18.75 秒与 14.81 秒只作为同机观察，不是跨平台硬性能阈值。

## 四、独立审阅修正

独立审阅没有发现 fail-closed 被放宽，但指出初版实现让公开 freshness API 的两个早退路径
也执行完整 integrity。这会扩大 Goal 等调用方的耗时和异常面，并改变基线语义。

候选实现已改为：

```text
公开 freshness
  -> 正常 review 路径：freshness + 一次 integrity
  -> 早退路径：只返回 freshness

Finish snapshot
  -> 正常 review 路径：复用同一次 integrity
  -> 早退路径：补一次 integrity，形成完整快照
```

同一预注册节点已覆盖两种早退情况，审阅发现已关闭。

## 五、本地验证

完整收集和最终去重结果：

```text
541 collected
541 unique
540 passed, 1 skipped
0 failed, 0 errors
```

唯一跳过：

```text
tests/test_runtime_safety_integration.py::test_posix_verification_temp_env_does_not_re_evaluate_path
```

该节点只覆盖 POSIX shell 变量展开，在 Windows 本地按合同跳过；Linux POSIX CI 必须真实通过。

完整测试使用 71 个最终有效小分片。带 `pytest-timeout` 参数的首次命令因本机未安装该 CI
额外依赖而在收集前退出；部分大分片也在并发负载下超过 60 秒。这些尝试均未计入产品结论，
最终汇总只纳入具有明确 passed/skipped 计数且覆盖 541 个唯一 nodeid 的分片。

静态门禁通过：

- `python -m compileall -q src scripts/check_repository_hygiene.py`
- `ruff check src tests scripts/check_repository_hygiene.py --no-cache`
- `python scripts/check_repository_hygiene.py --base-ref main`
- `git diff --check`

结构化摘要：

- `examples/evidence/m003-finish-snapshot-local-summary.json`
- `eval/assurance-validation.md` 的 `AV-M003-001 local candidate result`

## 六、下一步

1. 推送 `perf/finish-evidence-snapshot`。
2. 创建 Draft PR，确认 base 为 `main`。
3. 等待全部 CI job 通过，尤其是 Python 3.11 全量、Python 3.12 分片、Windows、POSIX 和
   wheel 构建安装。
4. 对 PR head 做一次独立 diff/证据审阅；发现缺口时只补 M-003 合同，不开始 Stage 1。
5. 代码 head CI 与审阅通过后，再追加 post-CI 证据并更新 Roadmap：
   - `M-003=completed`
   - `Stage 0=completed`
   - `Stage 1=next`
6. 最终文档 head 再次通过 CI 后，才可转为 Ready 并合并。

## 七、在另一台机器继续

```powershell
git fetch origin
git switch --track origin/perf/finish-evidence-snapshot
git status -sb
git log --oneline -3
```

最小复核：

```powershell
python -m pytest -q `
  tests/test_finish_artifact_integrity.py::test_finish_reuses_single_terminal_artifact_integrity_validation `
  tests/test_evidence_freshness.py::test_finish_rejects_workspace_changes_after_approved_review `
  tests/test_finish_artifact_integrity.py::test_risk_gate_recomputation_rejects_fully_synchronized_semantic_downgrade `
  tests/test_assurance_verification_semantics.py::test_finish_recomputes_unverified_success_as_needs_human `
  tests/test_assurance_verification_semantics.py::test_latest_passed_verification_supersedes_previous_failure_for_finish_and_goal
```

## 八、剩余边界

- 当前证据只证明本机 Windows / Python 3.12.10；不能替代 PR CI。
- 不缓存跨 Finish 调用的验证结果。
- 不优化 Goal 当前的独立 integrity 路径。
- 不删除 workspace、scope、risk 或 project policy 的终态重算。
- 不新增 Threat/Evidence 数据合同。
- 不发布新版本、不打标签、不自动合并。
