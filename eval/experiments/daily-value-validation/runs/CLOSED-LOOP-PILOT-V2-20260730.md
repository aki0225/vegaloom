# Vega 真实写审闭环实验 V2 阶段记录

> 本文件属于 append-only 实验证据。后续 repair 结果只能在末尾追加，不能修改或重写本阶段。

- 日期：2026-07-30
- 实验合同：`docs/experiments/daily-value-validation/CLOSED-LOOP-PILOT-V2.md`
- Run ID：`20260730-175833-282706-bug-loop`
- 当前状态：`in_progress`
- `reviewer_detection`：`passed`
- `repair_recovery`：`not_started`

## 阶段 1：Reviewer Detection

assist baseline 封存后，实验操作者应用 SHA-256 已冻结的 controlled negative patch。目标
workspace 保持初始 HEAD，没有 remote 或 commit，只修改：

```text
backend/app/quota.py
backend/tests/test_quota_limits.py
```

Vega 的 pre-verification、post-verification 和 pre-review scope gate 均通过。两条正式
verification 在 Windows `cmd.exe` 下正常执行并全部通过：

```text
..\venv\Scripts\python.exe ..\verifier.py .
..\venv\Scripts\python.exe ..\run_tests.py . {{vega_verification_temp}}
```

`verification-result.json` 记录 `command_count=2`、`failed_count=0`，不存在 timeout 或
interruption。

新的 `gpt-5.6-sol / medium / ephemeral` Reviewer 使用 `read-only` sandbox，约 107 秒后
正常结束，`returncode=0`、`termination_unconfirmed=false`。`review-context.json` 记录：

```text
contains_worker_chat=false
evidence_consistent=true
workspace_changed_during_review=false
```

Reviewer 返回 `request_changes`，明确指出：

1. 生产实现只读取 daily 状态，忽略 rolling bucket；
2. daily 与 rolling 同时存在时，没有按更严格剩余额度计算预占；
3. 新增测试没有覆盖仅 rolling 和双额度组合语义。

这些 finding 命中预注册的受控遗漏根因。因此按 V2 合同，本阶段记为：

```text
reviewer_detection=passed
```

该结果只证明当前只读 Reviewer 在一次有限验证全绿的受控负向样本中发现了明确项目规则
遗漏，不证明普遍检出率、正常任务成功率、经济性、Planner、Memory 或 Multi-Worker 价值。

## 当前停止点

Vega run 已安全停在 `needs_human`，iteration 1 生命周期为 `completed`，并生成
`fix-prompt.md`。交接前没有关联的存活 Vega、Codex 或 Node 进程。

尚未启动新的 repair Worker，也未执行 iteration 2。当前不能记录
`repair_recovery=passed`，也不能把 V2 写成完整闭环成功。

## 阶段 1 原始证据哈希

原始文件保存在 ignored `runs/` 与 `.local-validation/`。公开记录不保存本机绝对路径、
Provider endpoint、代理端口、凭据或完整事件流。

| Artifact | SHA-256 |
|---|---|
| `state.json` | `507e2afba33421da0c2eac9c7ff554e39578c7de3e4786d1629d7108f5dade47` |
| `trace.jsonl` | `90606d36a7841a71c561387d0e1b6e638cb0025d3c154c87ad6be9926687cab7` |
| iteration 1 `verification-result.json` | `1517c51784c2c65523895937ad08fe183dd6c8fd4b14d799b0c133182f058843` |
| iteration 1 `review-verdict.json` | `095912d42053da6f64e2d9d90e99df121205eaf0034ef99a101b8b9b4eabe87f` |
| iteration 1 `review-findings.md` | `2623cd176850416503918b272fc422c5306692a079506a5894a223d8224bed33` |
| iteration 1 `fix-prompt.md` | `c1b131fc9fc3c378b05d512ffee3a2bba07f05470713d11eb413943c9c9c8641` |
| iteration 1 `review-context.json` | `4e3c0a5c14813801d61ff25efdd98366184b54b17ce263f5e64e79261127333d` |
| Reviewer `execution.json` | `c71711aed782e1b8ada4070a692716c9116afe521f36b83720aeb18eb05fd6e2` |
| Reviewer `process-output.txt` | `4767f27424d2a3d377937ee0e557e3d124c60eae06a537ae0dff48a314e1c8b4` |
| controlled `backend/app/quota.py` | `fafed5db2612768d337fdf3618f70612c4fd66c7d608197c7b200b01e5ddc5c8` |
| controlled `backend/tests/test_quota_limits.py` | `9b0a89320d582bd93bb114ee4df27c29aea741257280be8dae0cb8caf991e9cc` |
