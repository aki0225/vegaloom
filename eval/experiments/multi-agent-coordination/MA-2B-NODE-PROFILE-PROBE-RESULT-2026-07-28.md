# MA-2B Node Project Profile 探针结果

> 运行日期：2026-07-28
> Candidate：`MA2B-NODE-PROFILE-V1`
> 冻结提交：`30cf4e89a8fb20f71054763fd3367f85f3036365`
> 结果：`inconclusive / current_harness_completion_signal_negative`

## 一、直接结论

本轮严格使用授权的 `3/3` 次 Worker Provider 调用：

```text
S：1 次
M：2 次并行
Planner：0
Reviewer：0
Retry：0
```

S 与 M 都没有在冻结的 480 秒上限内形成可统一验证的完成结果，因此不存在有效 S/M 质量或
经济性对照。该结果不能证明 Multi-Worker 无价值，但能证明当前“完整仓库 + 当前 prompt +
当前 Codex Worker 行为 + 480 秒上限”的组合还不能稳定完成这个中等自举任务。

## 二、输入资格

调用前门禁全部通过：

- 冻结初始 workspace：`11 failed`；
- 临时 reference workspace：`11 passed`；
- reference patch 未提交、未进入 prompt，Provider 调用前已删除；
- 两个 slice 新增行估算为 `45:22`，约 `2.05:1`；
- task、plan、ground truth、verifier 和候选 manifest 已先提交并推送。

因此本轮失败不是“verifier 本来就过不了”或“参考实现不存在”。

## 三、真实运行结果

| Treatment | Provider 状态 | 调用耗时 | Token | Worker workspace 改动 | 最终 verifier |
|---|---|---:|---:|---|---|
| S | `timed_out` | 489.532 秒 | 不可用 | 无 | 未运行 |
| M / Node 检测 | `timeout_termination_unconfirmed` | 497.636 秒 | 不可用 | 无 | 未运行 |
| M / 合同与上下文 | `timed_out` | 492.086 秒 | 不可用 | 仅 `models.py` 一行 | 未运行 |

S probe 总墙钟为 `498.417` 秒，M probe 总墙钟为 `502.328` 秒。三个会话都没有产生
`turn.completed`，因此 Provider 没有返回完整 Token usage；不能补算 Token、成本或经济性差值。

### S

S 正常启动并完成六次只读 shell 检查，最后进入三文件 `file_change`，但在首个写入完成前超时。
最终 workspace 无 diff。

### M / Node 检测

该 Worker 完成四次只读 shell 检查，最后进入 `project_profile.py` 的 `file_change`，但没有完成
任何写入。超时后 Windows 进程树终止未被 harness 确认，因此 execution 继续保留
`termination_unconfirmed`，不能改写为正常 timeout。

### M / 合同与上下文

该 Worker 完成三次只读 shell 检查，进入 `models.py` 与 `project_context.py` 的
`file_change`，超时前只落下一行：

```text
ProjectProfile.profile_issues: list[str] = Field(default_factory=list)
```

对这个局部 workspace 运行的事后诊断 verifier 为 `1 passed, 10 failed`。该诊断不属于正式
integrated verifier，也不改变 M 的 `worker_failed` 结果。

## 四、控制面事实

M 首次准备 workspace 时曾在任何 Provider 调用前触发 Windows 深路径复制失败。该尝试调用数
为零；保留失败现场后，只把 M workspace 根缩短为 `$repoRoot/.tmp/n28`，任务、模型、prompt、
时限和 verifier 均未改变，再执行原预注册的两次 M 调用。

Node 检测 Worker 超时后，终止诊断记录了 26 个 owned process tree PID。事后定向复核时这些
PID 均已不存在，但由于强制终止过程没有当场确认成功，公开结果继续保留
`termination_unconfirmed`。此外，一次性 driver 的 `run_id` 与 run root 名称不一致，标准
stop/recover 检查按安全语义拒绝接管；这是实验 driver 缺陷，不是 Vega 成功恢复。

## 五、为什么没有完成

从事件序列可见，三个 Worker 都把大部分时间花在读取完整生产文件、搜索既有测试和重新设计
接口，直到接近 480 秒才开始 `file_change`。M 虽然缩小了写范围，但没有同步缩小读取与探索
范围，因此两个 Worker 仍各自承担了大量重复上下文获取成本。

本轮支持以下判断：

1. 只拆写路径，不编译窄读上下文，不能自动获得 Multi-Worker 延迟收益；
2. 单纯继续放大 timeout 会掩盖上下文获取效率问题，不能作为首选修复；
3. 完整仓库自举任务比既有小 fixture 更接近真实使用，也暴露了当前探针的真实瓶颈；
4. Windows 临时根和 owned process 恢复身份必须在下一次调用前先离线修好。

## 六、阶段结论

本轮结果固定为：

```text
node_profile_probe_inconclusive
current_provider_harness_completion_signal_negative
multi_worker_economic_comparison_unavailable
formal_ma2b_pilot_readiness_blocked
```

它不撤销 C05/C07 的历史文档观察，也不把那些数字升级为可独立复算证据。结合本轮审计，当前
最诚实的说法是：最小 probe 机制能够用 fake Worker 重跑；历史 Provider 正向数字缺少远端原始
产物；新的真实中等任务在当前上下文与时限下三路均未完成。

## 七、下一步边界

本 candidate 到此结束，不在原输入上补跑或追加调用。若 Owner 后续批准 V2，只允许先做离线
准备：

1. 把 workspace/run 根固定为短路径；
2. 让 execution `run_id` 与可恢复 run root 身份一致，并先通过 Windows timeout/cleanup 测试；
3. 编译窄读 context packet，避免每个 Worker 重复读取完整大文件和无关测试；
4. 继续使用行为 verifier，不引入 AST、参考补丁匹配或 Reviewer 补救；
5. 重新预注册 timeout 与调用数，再单独获得新的 Provider 授权。

结构化脱敏结果位于：

```text
eval/experiments/multi-agent-coordination/results/MA2B-NODE-PROFILE-V1-2026-07-28.json
```

原始 Provider JSONL、execution 与本机 workspace 继续只保留在忽略的 `.tmp/`，不进入公开
Git 历史；结构化结果保存其关键内容哈希，但远端 checkout 仍不能重放原始 Provider 会话。
