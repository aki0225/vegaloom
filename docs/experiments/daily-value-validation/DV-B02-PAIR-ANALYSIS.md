# DV-B02 配对结果分析

- 日期：2026-07-29
- 状态：两个 treatment 已各正式运行一次并封存
- 结论边界：`insufficient_evidence`

## 1. 实际结果

| Treatment | 正式终态 | Wall clock | 超时现场 verifier | Reviewer |
|---|---|---:|---|---|
| Vega | `timed_out / not_completed` | `627.369s` | failed | 未启动 |
| Native | `timed_out / not_completed` | `608.106s` | passed | 未启动 |

两组使用相同 baseline、任务合同、模型、reasoning、600 秒 Worker timeout、Provider
profile、允许路径和固定 verifier。两组 Provider 都正常返回工具调用，不属于认证或网络
基础设施失败。

Vega 超时时只留下 `src/attr/converters.py` 的部分 patch，缺少 `Converter` 导入且没有测试；
固定 verifier 四项均因 `NameError` 失败。Native 超时时已修改两个允许文件并补充测试，固定
verifier 四项通过，但 Worker 没有返回最终消息，独立 Reviewer 也没有启动，因此不能记为
成功。

## 2. 这轮证明了什么

1. **fail-closed 成立。** Vega 没有因部分 patch 或后续人工 verifier 结果把 timeout 改写为
   success，也没有继续启动 Reviewer。
2. **证据保留有用。** state、trace、原始 Worker 输出、timeout 状态和完整 diff 足以还原
   Worker 在截止点前做到了哪里。
3. **Native 的截止点更靠后。** 它完成了导入、生产修改和回归测试；Vega 在相近实现的
   production patch 写入后立即超时。封存后才允许查看 oracle；Native 的修复方向与 oracle
   一致，并额外通过了四种 `Converter` 上下文参数组合的只读诊断。

这些事实只能描述本次截止点，不能证明 Native 普遍优于 Vega。两组都没有完成一次可交付
闭环，也没有产生 Reviewer 证据。

## 3. 暴露出的实验问题

### 3.1 正式依赖环境没有真正冻结

资格阶段的 `dependencies=passed` 只证明源码可以安装且 `pip check` 通过，没有证明正式
workspace 已安装测试 extra。Native 运行 `tests/test_converters.py` 时因缺少 `hypothesis`
失败；资格阶段 venv 的后验检查也确认没有该依赖。

因此当前资格字段命名过宽。它不能被解释为“正式测试环境 ready”。

### 3.2 600 秒主要消耗在慢工具往返

Vega 原始输出显示，若干本地只读命令单次耗时约 32 到 86 秒；Native 的失败 pytest 也耗时
约 52 秒。两组都在正常分析和自检阶段耗尽预算，未进入最终汇报。

当前证据不能区分以下因素：

- Codex/Provider 工具调用链路变慢；
- 同机其他 Codex 任务造成资源竞争；
- Worker 自检策略过重；
- Vega 额外上下文和编排带来的真实时间成本。

因此不能把 19 秒 wall-clock 差值解释为 Harness 开销。

### 3.3 Reviewer 价值仍然没有被测试

本实验的关键问题包含“隔离 Reviewer 是否减少遗漏”，但两个 treatment 都没有启动 Reviewer。
当前 pair 对 Reviewer 独立发现、误通过和上下文隔离收益提供零证据。

### 3.4 指标口径仍需收紧

- `verification_status` 当前记录封存时对最终 workspace 执行 verifier 的结果，但 Runtime
  内部 verification 实际都未运行；后续版本应显式记录 `verification_phase`。
- `manual_actions=0` 沿用了现有口径，但正式运行后的证据封存由操作脚本完成。V2 必须明确
  自动实验操作与 Owner 人工搬运的计数边界。
- Token 事件在 timeout 前没有完整落盘，不能形成成本结论。

## 4. 下一版只做的最小调整

不修改 Vega Runtime，不增加 SDK、队列、Web UI、Memory、Multi-Worker 或 A2A。若继续
实验，只预注册 `V2` 并做四项调整：

1. 为每个 case 建立 Native 与 Vega 共用的只读依赖环境，冻结 Python、包清单和 hash；
   正式启动前必须证明目标测试切片可收集。
2. 在 Provider 调用前记录同机实验并发条件；存在无法解释的高资源竞争时 fail-closed，
   不消耗正式 treatment。
3. 为 Worker 事件增加接收时间戳，区分模型思考、工具等待和 Harness 编排时间。
4. 结果 schema 区分 Runtime verification 与 post-seal verifier，并冻结人工操作计数口径。

至少获得一个“两个 Worker 都正常返回、固定 verifier 均执行、两个 Reviewer 均完成”的
干净 pair 前，不继续扩建 Harness，也不宣称 Vega 已证明日用价值。

## 5. 当前决策

DV-B02 V1 不重跑。它是一次有效的失败记录，但不是产品方向裁决。当前最合理的动作是先修正
实验环境和指标口径，再决定是否值得运行 V2；不是继续给 Vega 增加功能。
