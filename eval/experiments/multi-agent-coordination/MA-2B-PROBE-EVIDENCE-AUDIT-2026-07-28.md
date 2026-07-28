# MA-2B 既有 Provider 探针证据审计

> 审计日期：2026-07-28
> 审计基线：`e4bacfc7c24020489db7bb2675aee4bab14c10d4`
> 结论：`historical_metrics_not_independently_replayable_from_remote_checkout`

## 一、审计目的

本记录只核对 2026-07-27 文档中 C07、C08、C05 Provider 探针数字能否由当前远端
checkout 独立复算。它不改写既有实验结论，也不把证据缺失解释为历史数字错误。

## 二、核对范围

只检查以下仓库内公开事实：

- 当前分支完整 Git 历史与受跟踪文件；
- `eval/experiments/multi-agent-coordination/` 下的冻结 task-pack、ground truth 和 workspace；
- `src/vega/experimental/ma2b/probe.py` 及其自动化测试；
- 当前机器仓库内被忽略的 `.tmp/` 实验目录。

没有检查其他电脑、私人目录、Provider 后台或未进入仓库的历史会话。

## 三、核对结果

| Case | 当前可找到的数值来源 | 可找到冻结输入 | 可找到结构化运行结果 | 可独立复算 Token / 时长 |
|---|---|---:|---:|---:|
| C07 | 研究计划、跨电脑交接 | 是 | 否 | 否 |
| C08 | 研究计划、跨电脑交接 | 是 | 否 | 否 |
| C05 | 研究计划、跨电脑交接 | 是 | 否 | 否 |

历史墙钟与 Token 数字只出现在：

```text
docs/experiments/multi-agent-coordination/RESEARCH-AND-EXPERIMENT-PLAN.md
docs/experiments/multi-agent-coordination/MA-2B-PILOT-NEXT-HANDOFF-2026-07-27.md
```

当前远端 checkout 没有与这些数字对应的以下原始产物：

- Provider JSONL 事件或原始 usage；
- 实际 Worker prompt 或其哈希；
- S/M 最终 diff、diff 哈希和 changed paths；
- 固定 verifier 的命令输出、返回码和日志哈希；
- 单次 Worker 状态、时长与调用标识的结构化结果。

当前 `.tmp/` 保留了更早的 C01/ATG 脚手架和 canary，但没有可绑定到上述 C05/C07/C08
数值的完整运行目录。

## 四、仍可独立验证的事实

以下内容可由当前 checkout 重新验证：

1. C05、C07、C08 的冻结 task-pack、ground truth 与候选 workspace 存在；
2. 当前最小 `probe.py` 能执行单 Worker 顺序模式与双 Worker 隔离并行模式；
3. probe 会检查互斥写范围、确定性集成并在最终 workspace 上运行统一 verifier；
4. probe 的 fake Worker 自动化测试可以重跑。

这些事实只能证明机制与输入仍在，不能反向补出历史 Provider 数字。

## 五、证据解释边界

因此，C05/C07/C08 的历史 Provider 数字应被视为：

```text
documented_observation_without_remote_replay_artifacts
```

它们没有被本次审计判定为错误，但在补齐原始产物前，不应作为可由第三方独立复算的唯一证据。
后续新增 Provider 对照必须从第一次调用开始保存 prompt/task/plan 哈希、Provider usage、
changed paths、diff 哈希和 verifier 结果，不能再依赖事后手工汇总。
