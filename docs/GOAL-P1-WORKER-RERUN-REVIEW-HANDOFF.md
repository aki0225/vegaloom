# Goal P1 Worker 重跑审阅交接

> 日期：2026-08-09
> 分支：`codex/goal-p1-real-dogfood`
> 审阅基线：`origin/main@28c2ddb`
> 审阅提交：`0bf5baf`
> 状态：`blocked-before-pr`

## 结论

当前分支不能直接合并。

`vega loop continue --rerun-worker` 的产品方向仍然成立：它保持显式触发，不改变默认
`vega do`，也没有引入 daemon、数据库、多 checkpoint 或自动重试。但审阅确认，当前实现
仍可能在证据不完整或工作区变化未被识别时启动可写 Worker，违反 Vega 的 fail-closed
承诺。

本分支应继续作为实验分支修复，不创建新的替代分支。修复完成、最新 head 的本地门禁与
GitHub Actions 均通过后，再创建面向 `main` 的 PR。

## 已确认正常

1. 分支与远端一致，审阅时相对 `origin/main` 为 `ahead 4 / behind 0`。
2. Worker baseline 使用固定 iteration 路径，并以 SHA-256 绑定 state。
3. 旧 run 缺少新 baseline 时会拒绝 `--rerun-worker`，不会静默降级。
4. tracked、untracked、根部 ignored partial work 的现有正向和负向回归通过。
5. 自动 Worker 已达到最大 iteration 时不会继续提示重跑。
6. 新增 state 字段有默认值，旧主线 state 仍可解析。
7. `eval/long-task-controller-experiment.md` 仅追加历史证据，没有改写旧记录。

## 合并阻断项

### P1-1：Worker 重跑授权存在不可恢复的崩溃窗口

`initialize_auto_worker_rerun()` 先把授权写入 state，并把 run 改成 `running`，随后才写
`auto_worker_rerun_requested` trace；新的 iteration 又要更晚才登记。

如果进程在这些步骤之间退出，会留下：

```text
status=running
current_step=recovered
latest iteration=interrupted
authorization 已写入
rerun trace 或新 iteration 尚不存在
```

普通 continue 无法重入，RecoveryRuntime 也会因为当前 iteration 已是 `interrupted`
而拒绝恢复。

最小修复：

1. 复用现有 recovery transaction 思路，增加一个很小的、可幂等重放的重跑授权事务。
2. pending 阶段保持 `needs_human/recovered`，不要先暴露不可恢复的 `running` 中间状态。
3. trace、授权和下一 iteration claim 可以重复确认；同一授权重进时不得报“重复写入”。
4. 增加 state-save、trace-write 和 iteration-claim 两侧的故障注入测试。

### P1-2：来源 baseline 的 trace 缺失时仍可能先启动 Worker

恢复规划目前只校验 state 中的 baseline 元数据和 `worker-baseline.json` 内容哈希。匹配的
`worker_baseline_captured` trace 要到终态 eval 才检查。

因此删除来源 baseline trace 后，Worker 仍可能先运行和修改仓库，最终才把 run 标为
failed。这不符合“证据不足时先停止自动执行”。

最小修复：

1. 在恢复规划阶段要求恰好一个匹配的 `worker_baseline_captured` 事件。
2. iteration、artifact path、artifact version 和 SHA-256 必须同时匹配。
3. 缺失、重复或顺序无效时，在 runner 构造和 Worker 启动前拒绝。
4. 测试必须断言 runner 未调用、目标仓库未变化、state 与 trace 未产生新的执行记录。

### P1-3：同时删除授权 state 与授权 trace 可以绕过完整性检查

`worker_rerun_binding_issues()` 只比较现存授权和现存请求事件的数量。两者同时为空时会直接
返回成功，即使 state 中已经存在“中断 Worker 后又执行了下一轮 Worker”的 iteration
序列。

本轮最小复现得到：

```text
worker_rerun_binding_issues(...) == []
```

最小修复：

1. 从 iteration 生命周期推导是否必须存在显式重跑授权，不能只相信现存授权列表。
2. 中断 Worker 后出现下一轮非 `skipped` Worker 时，必须存在唯一 state 授权与唯一 trace
   请求事件。
3. 同时校验来源 interruption、recovery、rerun request 和新 `worker_started` 的因果顺序。
4. 增加“只删 trace”“只删 state”“两者同时删除”“重复事件”“事件重排”回归。

### P1-4：Git index 标记可隐藏 tracked partial work

`capture_tracked_scope_snapshot()` 已读取 `index_flags_sha256` 和 `unsafe_index_paths`，但
恢复使用的 `WorkspaceSnapshot` 与 `worker-baseline.json` 没有保存这两个字段。

在干净临时仓库中实测：

1. Worker baseline 在文件未修改时捕获。
2. 后续设置 `assume-unchanged` 并修改同一 tracked 文件。
3. `git status` 与 `git diff` 均为空。
4. 修改前后 `WorkspaceSnapshot` 相等。

这会把未知 partial work 误判为可安全重跑。

最小修复：

1. 将 `index_flags_sha256` 和 `unsafe_index_paths` 纳入 Worker workspace snapshot 与 baseline
   绑定。
2. baseline 或当前快照出现 `assume-unchanged`、`skip-worktree` 时直接拒绝显式重跑。
3. 增加两类 index flag 的真实 Git 回归，不使用纯模型夹具代替。

### P1-5：ignored 目录后代内容变化可以逃逸

ignored 路径枚举使用 `git ls-files --directory`，完整目录会折叠成一个目录项。目录
fingerprint 只记录目录自身元数据，不读取后代，却把 `metadata_complete` 记为 `True`。

在临时仓库中修改 `ignored-dir/payload.txt` 的同长度内容后，修改前后
`WorkspaceSnapshot` 完全相等。

最小修复：

1. Worker 重跑基线不得把“目录内容未读取”标记为完整。
2. 对 ignored 目录生成有界后代 manifest；超过既有预算时 fail-closed，不新增第二套预算
   系统。
3. 增加已有 ignored 目录内文件内容变化、增删后代和预算耗尽回归。

### P1-6：Worker baseline 会明文保存敏感路径名

`worker-baseline.json` 直接序列化 `tracked_files`、`untracked_files` 和
`ignored_path_exclusions`，没有经过路径脱敏。使用假 key 路径名复现时，假 key 会原样出现
在 artifact 中。

最小修复：

1. 机器比较使用排序后路径集合的稳定 SHA-256 与数量，不在 baseline 中保存原始路径列表。
2. 需要给人工显示的路径单独使用现有 `safe_path_for_report()` / redaction 规则。
3. 增加假 key 路径测试，并扫描 state、trace、baseline、eval 与最终报告。

### P1-7：最终工作区检查与 Worker 启动之间仍有竞态

恢复入口第二次捕获快照后，会继续写授权、state、trace、prompt 和 metrics，随后才复用旧
快照保存 baseline 并启动 Worker。该窗口内的人工或外部修改可能被错误归入 Worker。

最小修复：

1. 在持久化本轮 Worker baseline 前重新捕获实时快照。
2. 将该快照与恢复授权时的 expected snapshot 比较，不一致立即停止。
3. 使用这份最后快照写 baseline，并紧邻 `worker.run()` 调用。
4. 文档明确这只是把竞态缩到进程启动边界，不声称提供操作系统级文件锁或隔离。

## 暂不扩大

以下内容不属于本轮修复：

1. daemon 或后台常驻调度器。
2. 多 checkpoint 自动串联。
3. 自动重试、自动恢复或无人值守跨天执行。
4. 数据库、事件总线、签名链或通用事务框架。
5. 新 Provider、Multi-Agent、LangGraph 或新的编排 Runtime。
6. 为减少测试而删除 tracked、untracked、ignored、index flag 等不同安全语义的覆盖。

## 建议实施顺序

1. 修复 snapshot 完整性：index flags、ignored 后代 manifest、敏感路径机读摘要。
2. 修复恢复授权事务和来源 baseline trace 的启动前校验。
3. 从 iteration 序列推导授权要求，补齐完整性与事件顺序校验。
4. 把最终工作区复查移动到 Worker baseline 持久化和启动边界。
5. 更新 `LONG-RUNNING-GOALS.md` 与 `ROADMAP.md`，将结论限定为 r6 的受限单文件样本，不写成
   已证明通用长任务能力。
6. 跑本地门禁，推送最新 head，创建 PR 并等待 GitHub Actions。

## 最小验证矩阵

必须新增并通过：

1. 授权 state 保存后崩溃可幂等恢复。
2. 授权 trace 写入后崩溃可幂等恢复。
3. 来源 baseline trace 缺失或重复时 Worker 未启动。
4. state 授权与 trace 同时删除时完整性校验失败。
5. `assume-unchanged` 与 `skip-worktree` partial work 均阻止重跑。
6. ignored 目录后代内容变化阻止重跑。
7. 最后一次复查后工作区变化阻止重跑。
8. 假 key 路径不出现在任何 run artifact。
9. 正常 clean-workspace 显式重跑仍能完成。
10. 旧主线 run 仍可查看，但缺少新协议证据时不可重跑。

完成定向测试后运行：

```powershell
python -m compileall src scripts/check_repository_hygiene.py
python scripts/check_repository_hygiene.py --base-ref origin/main
python -m pytest
ruff check src tests scripts/check_repository_hygiene.py
git diff --check
git status --short --branch
```

单次 pytest 超过仓库约定时限时，按 CI 的互斥文件集合分片，并为每片使用独立
`.tmp/pytest/runs/<name>` 与 cache 目录。必须核对 collected node 总数，不能把超时当作
通过。

## 本轮审阅证据

本轮没有修改 Runtime。实际执行结果：

```text
目标化 recovery 回归：6 passed in 23.07s
git diff --check origin/main...HEAD：通过
双删授权证据复现：校验错误地返回 []
assume-unchanged partial work 复现：修改前后 WorkspaceSnapshot 相等
ignored 目录后代内容变化复现：修改前后 WorkspaceSnapshot 相等
假 key 路径复现：明文出现在 worker-baseline.json
```

审阅时该 head 没有 PR，也没有 GitHub Actions workflow run 或 commit status。分支上先前
记录的更大测试结果可以作为历史参考，但不能替代修复后最新 head 的 CI。

## 明日继续位置

```powershell
git switch codex/goal-p1-real-dogfood
git pull --ff-only
```

从 P1-1 至 P1-3 的恢复事务与启动前证据校验开始，随后处理 snapshot 三项完整性缺口。
不要另开功能分支，也不要同时推进新的长任务能力。
