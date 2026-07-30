# Vega 真实写审闭环实验 V1 运行记录

- 日期：2026-07-30
- 实验合同：`docs/experiments/daily-value-validation/CLOSED-LOOP-PILOT-V1.md`
- Harness code baseline：`b5f7cd62916b3ff216571899a9e39e3739104386`
- 预注册提交：`dfeb479117d01cff75c069c24714404d5f1afca7`
- 总体状态：`completed`
- 总体结论：`inconclusive`

## 1. Normal Treatment

- Run ID：`20260730-173024-698996-bug-loop`
- 最终状态：`needs_human`
- 当前步骤：`stopped`
- Reviewer：未启动
- verification：未启动

Worker 使用冻结的 `gpt-5.6-sol / medium / ephemeral` 配置，于
`2026-07-30T09:30:54.410831+00:00` 启动。到 360 秒预注册观察上限时，操作者写入 stop
request；Vega 只停止本轮 owned child，没有扫描或终止其他进程。终止于
`2026-07-30T09:37:50.072600+00:00` 确认，`termination_unconfirmed=false`。

触发停止线时只观察到生产文件改动。安全停止的宽限阶段内，Worker 又写入了测试文件，最终
现场为两个允许文件、57 行新增、3 行删除：

```text
backend/app/quota.py
backend/tests/test_quota_limits.py
```

生产代码已加入固定 1 USD 预占上限，并继续按 daily/rolling 的更严格剩余额度取值；测试
现场也包含高额度并发、低额度阻断和滚动窗口组合用例。但 Worker 没有正常返回，Vega 没有
执行固定 verification，也没有启动 Reviewer，因此该现场不能记为成功或已验证修复。

本 treatment 证明：

- stop request 能绑定并停止正确的 owned Worker；
- 停止后保留 diff、execution、trace 和终止确认；
- `stopped` 不会被后续 partial patch 包装为成功。

它没有证明正常写审闭环能在本轮时间预算内完成。

## 2. Negative Treatment

- Run ID：`20260730-173846-467705-bug-loop`
- 最终状态：`needs_human`
- 当前步骤：`stopped`
- `reviewer_detection`：`not_scored`
- `repair_recovery`：`not_started`

assist baseline 成功封存后，操作者应用了 SHA-256 已冻结的 controlled negative patch。
scope gate 正确识别两个允许文件且无污染，但两条 verification 命令都在 Windows
`cmd.exe` 下失败：

```text
../venv/Scripts/python.exe ../verifier.py .
../venv/Scripts/python.exe ../run_tests.py . {{vega_verification_temp}}
```

失败根因是 POSIX 风格路径不能作为当前 `cmd.exe` 的可执行命令；两个命令均在 0.4 秒内以
退出码 `1` 结束，不是 verifier 断言或 pytest 失败。

当前 Runtime 在 verification 失败后仍继续编译 Reflect 与 review pack，并准备启动
Reviewer。V1 合同要求“绿验证后才计 Reviewer”，因此操作者在 reviewer owned execution
建立后立即写入 stop request。Reviewer 终止确认，`runner_status=stopped`；自动生成的
`needs_human` verdict 只是停止后的解析兜底，不计入 Reviewer 漏检或发现结果。

本 treatment 证明了 scope、verification 证据和 Reviewer stop 边界，但没有形成有效的负向
敏感性样本。

## 3. V1 结论

- Normal 是有效的时间预算失败样本，不能隐藏重跑或改写为成功。
- Negative 是验证命令可移植性造成的无效样本，不能评价 Reviewer。
- V1 没有证明真实写审闭环成功，也没有证明 Reviewer 能发现绿验证下的受控遗漏。
- 后续若继续，只能新建版本；不得修改本文或 V1 原始证据。

## 4. 原始证据哈希

原始文件保存在 ignored `runs/` 与 `.local-validation/`。公开记录不保存本机绝对路径、
Provider endpoint、代理端口、凭据或完整事件流。

| Artifact | SHA-256 |
|---|---|
| normal `state.json` | `d3d3063264a1ce8b9b09df2db1e6a25b3707890e5e2364540bd0e1381cfd5740` |
| normal `trace.jsonl` | `4860fc8b9c131af50d21292c80fd0d1bf8fdc148c107bd3bd3a3a3d5d67c8687` |
| normal worker `execution.json` | `da1121b9e3df87ec46fee4ed8b3890cb94b834d868c8f44fffeea4f7b938a812` |
| normal `stop-request.json` | `553039c31898b45ccac92793552c6b3c57cbc07c8791e1037e502ae74dca1972` |
| normal `process-output.txt` | `3a78db6442838720ad00e5caa445470469dec70c43ef8f30f50224146e01dd37` |
| negative `state.json` | `dc125bd18009c62796cb87b6bf724114db953c9d170dc48557df2994e855850b` |
| negative `trace.jsonl` | `e884375612ca1e71ee588a5d6a99fc671084f3fcdcdd0adb2f164af7ea03159c` |
| negative `verification-result.json` | `7f7367d1cf7e71440eda052fb7aac7fb39ff8880f100fb8c914a095178cb1e95` |
| negative reviewer `execution.json` | `2809e171b947e9474ea0fcdd334c25e82dc54c159ecfd23c500e3290bb08c5c4` |
| negative `review-verdict.json` | `acc2e773eecebb50082ea709f2f537512f19a3a104c79a2d16d1e5c4cc529e9c` |
