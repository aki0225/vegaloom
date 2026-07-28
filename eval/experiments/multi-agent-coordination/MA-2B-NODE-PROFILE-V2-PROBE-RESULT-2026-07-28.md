# MA-2B Node Project Profile 探针 V2 结果

> 运行日期：2026-07-28
> Candidate：`MA2B-NODE-PROFILE-V2`
> 冻结提交：`d55eeda3def863be50ab8654191c956be9b1764a`
> 结果：`inconclusive / parallel_treatment_not_executed`

## 一、直接结论

本轮获得单独 Owner 授权，预算上限为：

```text
S Worker：1 次
M Worker：2 次并行
Planner：0
Reviewer：0
Retry：0
```

实际只消耗 `1/3` 次 Worker Provider 调用。S Worker 超过冻结的 480 秒上限后，Windows
owned process tree 终止未被 harness 当场确认。Driver 按预注册停止规则阻断 M，因此两个
并行 Worker 均未启动，剩余两次预算也没有用于补跑。

这轮结果不能评价 Multi-Worker 的完成率、延迟、Token 或经济性，因为 M treatment 根本没有
执行。能够确认的只有两点：

1. V2 的 fail-closed 停止规则生效，没有在终止状态不可信时继续扩大执行；
2. 当前 Windows 进程树终止确认仍不足以支撑下一轮真实 S/M 对照。

## 二、调用前资格

调用前复核结果如下：

- 当前分支与冻结提交一致，Tracked 工作区 clean；
- 控制根、S run root、M run root 均不存在，没有覆盖历史运行；
- 冻结源码树为 `61efd1dc116be8101000f464739b817b0eb33f16`；
- 准备归档 SHA-256 为
  `55acdb8cd08803863b8b03878282d8d4dfcc12aa59529dc0012a8aa6265c1462`；
- 新鲜红基线为 `11 failed in 17.85s`；
- Driver `_preflight()` 通过；
- S 与两个 M prompt 哈希均与运行 manifest 一致；
- M 两个 prompt 继续保持上下文包互斥。

模型与客户端绑定为：

```text
Codex CLI：codex-cli 0.144.6
Model：gpt-5.6-sol
Reasoning effort：medium
Worker timeout：480 秒
```

## 三、真实运行结果

| Treatment | Provider 调用 | 状态 | 调用耗时 | Token | Workspace 改动 | 最终 verifier |
|---|---:|---|---:|---:|---|---|
| S | 1 | `termination_unconfirmed` | 499.442 秒 | 不可用 | 无 | 未运行 |
| M / Node 检测 | 0 | `not_run` | 不适用 | 不适用 | 不适用 | 未运行 |
| M / 合同与上下文 | 0 | `not_run` | 不适用 | 不适用 | 不适用 | 未运行 |

S probe 总墙钟为 `500.632` 秒。Provider 事件中没有 `turn.completed`，因此没有可信 Token
usage，不能补算成本。

S Worker 完成了一次针对三个目标文件的窄读取，随后进入以下文件的 `file_change`：

```text
src/vega/project_profile.py
src/vega/models.py
src/vega/project_context.py
```

但 `file_change` 没有完成，最终冻结 workspace 仍为 clean，tree 仍是
`61efd1dc116be8101000f464739b817b0eb33f16`，diff SHA-256 为标准空内容哈希
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。

## 四、终止与停止语义

超时后，非强制 `taskkill /T` 返回失败，诊断显示部分后代需要 `/F`；随后强制
`taskkill /T /F` 在当前 10 秒确认窗口内超时。execution 因此保留：

```text
status = running
finished_at = null
termination_unconfirmed = true
```

Driver 将本次调用记为 `worker_failed`，没有运行 scope 检查、确定性集成或统一 verifier，
也没有启动 M。

事后定向审计发现，终止诊断中作为目标出现的 26 个 PID 均已不存在，Provider 输出文件在连续
5 秒观察窗口内也没有继续增长。但这只能说明“事后没有观察到存活进程”，不能证明 taskkill
当场完整终止了所有后代，因此公开结果继续保留 `termination_unconfirmed`，不改写成普通
timeout。

## 五、V2 相对 V1 能说明什么

V2 的窄上下文确实让 S Worker 没有重复进行整仓搜索，而是一次读取目标文件后进入写入阶段；
这是方向性观察，不是完成率证据。由于写入没有完成、M 没有执行，本轮仍不能证明：

- 窄上下文能让任务在 480 秒内稳定完成；
- 两个 Worker 比一个 Worker 更快或更省 Token；
- 两个切片能够成功确定性集成；
- Multi-Worker 对这个真实任务有正向或负向收益。

因此阶段结论固定为：

```text
node_profile_v2_probe_inconclusive
sequential_completion_signal_negative
parallel_treatment_not_executed
multi_worker_comparison_unavailable
formal_ma2b_pilot_readiness_blocked
```

## 六、下一步边界

本 candidate 到此结束，不把剩余 `2/3` 预算拆出来补跑 M，也不在同一结果中混入修复后的
调用。下一次真实 Provider 实验前应先离线解决：

1. 用受控进程树复现并收紧 Windows 强制终止与确认窗口；
2. 评估是否绕开 `cmd.exe + codex.cmd` 包装层，减少 owned tree 层级；
3. 保留 V2 的短路径、run identity、prompt 隔离与窄上下文；
4. 重新冻结 Driver、调用数和停止规则，再单独获得新授权。

结构化脱敏结果位于：

```text
eval/experiments/multi-agent-coordination/results/MA2B-NODE-PROFILE-V2-2026-07-28.json
```

原始 Provider JSONL、execution、prompt 与本机 workspace 继续只保留在忽略的
`$repoRoot/.tmp/m2n/`，不进入公开 Git 历史。
