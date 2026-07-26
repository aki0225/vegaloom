# MA-2B Worker 输入 v2 能力 Canary 结果

> 运行日期：2026-07-26  
> 分支：`experiment/ma2b-planner-worker-pilot`  
> 协议提交：`137ef84`
> Planner 适配修订提交：`48e1518`
> 状态：`canary_completed / capability_signal_positive / economic_signal_not_observed`

## 一、结论

本轮证明：

- `compiled-context-v2` 与 `plan-contract-v2` 都能让 Worker 在不知道验证命令的情况下完成
  `MA2B-F01`；
- A/B/C 三路都只修改 `src/textops.py`，没有新增文件、范围污染、HEAD 变化或
  `__pycache__`；
- 三路产生完全相同的补丁，Scope Gate、控制面 verification 和 canonical
  DelegationAttempt 全部通过；
- `gpt-5.6-luna` budget Worker 在本 case 中能够遵守 v2 委派合同，旧 C 因自行运行测试而
  产生范围污染的问题没有复现。

本轮没有证明：

- `C` 比 `A` 的 treatment 总成本更低；
- 单个一行修复足以代表正式 12 case Pilot；
- Reviewer、正式 task-pack、真实项目复杂度、MA-3、原生子 Agent 或 multi-worker 已通过；
- `worker_token_limit=5000` 已被强制执行。

## 二、首次运行与适配修订

首次 v2 运行：

```text
runs/ma2b-canary/20260726-190841-v2-abc
```

- `A` 有效；
- `B/C` 的 premium Planner 经本机 CC Switch 调用时，上游约 75 秒后返回 HTTP 502；
- 两路 Planner 最终达到 180 秒外层超时，Worker 均未启动，workspace 保持 clean。

首次结果完整保留，没有重试或覆盖。后续按
`MA-2B-WORKER-INPUT-V2-CANARY-ADAPTER-REPAIR.md` 建立全新运行，只把 Planner 从
`--output-schema` 改为 schema-in-prompt，并继续使用本地严格解析和运行时绑定校验。

修订运行：

```text
runs/ma2b-canary/20260726-192039-v2-repair-abc
```

## 三、修订运行结果

| Treatment | Planner Token | Worker Token | 总 Token | Planner 秒 | Worker 秒 | 总墙钟秒 | 结果 |
|---|---:|---:|---:|---:|---:|---:|---|
| `A` | 0 | 21,198 | 21,198 | 0 | 44.412 | 47.610 | valid |
| `B` | 16,133 | 19,627 | 35,760 | 27.851 | 31.251 | 62.547 | valid |
| `C` | 16,081 | 17,101 | 33,182 | 31.265 | 21.368 | 55.953 | valid |

三路共同满足：

```text
runtime_status = attempt_recorded
readiness_status = budget_eligible
attempt_validation = valid
verification_returncode = 0
changed_files = ["src/textops.py"]
new_files = 0
pycache = 0
```

三路补丁 SHA-256 相同：

```text
c659cf92f9033773bac9668c489438b344287ce9b925768ae1073b5410d4d3b3
```

补丁均为：

```diff
 def normalize_label(value: str) -> str:
-    return value.strip()
+    return " ".join(value.split())
```

## 四、成本与耗时解释

`C` 相比 `A`：

- Worker Token 减少 `19.3%`；
- Worker 执行时间减少 `51.9%`；
- treatment 总 Token 增加 `56.5%`；
- treatment 总墙钟时间增加 `17.5%`。

`C` 相比 `B`：

- treatment 总 Token 减少 `7.2%`；
- treatment 总墙钟时间减少 `10.5%`。

因此，本 case 的正确解释是：

> budget Worker 的受控执行能力出现正向信号；但 premium Planner 的固定开销超过了
> budget Worker 节省的执行成本，尚未观察到相对无显式 PlanContract 的 A 基线的总经济收益。

不能只比较 Worker 子阶段后声称 `C` 已降低整体成本。

## 五、剩余边界

1. `MA2B-F01` 是 synthetic fake-driver fixture，不是预注册的正式代码 case。
2. 当前任务只有一个文件和一行核心修改，不能代表跨文件行为变更。
3. Worker 实际 Token 均超过 Plan 中的 `5000`；当前 CLI 只能观测 usage，不能按该字段强制
   终止。正式 Pilot 前必须明确该字段是硬门禁还是观测预算，不能继续把未执行限制写成事实。
4. 本轮没有调用 Reviewer，不能形成审查质量或盲化效果结论。
5. 当前 Provider 使用 ephemeral 会话，但没有达到正式 Pilot 的完整 Provider cache 隔离要求。

## 六、下一步门槛

本结果不授权进入 MA-3。若继续 MA-2B，下一步应先解决正式 Pilot 的两个输入问题：

1. 冻结非 synthetic 的实际 task-pack，至少覆盖小修复、跨文件行为变更、需要人工决策和
   verifier 故障阻断；
2. 对 `worker_token_limit` 的真实语义作出明确选择并在任何正式 Provider 运行前冻结。

在这两项完成前，不继续增加 receipt、ledger、manifest、Reviewer 或新的协调层。
