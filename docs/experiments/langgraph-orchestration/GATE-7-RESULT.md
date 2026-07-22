# Gate 7 v1 大任务协议实验结果

> 历史状态：`superseded`
>
> 本文件只记录 2026-07-19 的 Gate 7 v1 结果，不是 Gate 7 的最终收口结论。
> 2026-07-20 的最终真实执行结果见
> [`GATE-7-R6-RESULT.md`](GATE-7-R6-RESULT.md)，整体架构决策见
> [`DECISION.md`](DECISION.md)。

> 总状态：`failed-at-gate-7a`
>
> 日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> baseline commit：`private-gate-7-v1-baseline-redacted`

---

## 1. 最终判断

Gate 7 v1 没有完成真实大任务实验。

```text
Gate 7A = failed during CP01 provider session
Gate 7C = not-started because A success condition was not met
overall = failed-at-gate-7a
```

失败发生在真实 worker provider transport 阶段，不是 Flask 测试、scope、handoff、
LangGraph checkpoint 或 Machine F 恢复阶段。

因此本实验不能回答：

- linear + Goal/Handoff 能否真实完成该三 checkpoint 大任务；
- LangGraph 是否提供恢复增量；
- LangGraph 的真实成本和延迟是否值得；
- 默认引擎是否应该改变。

---

## 2. 实验前已经证明的内容

Gate 7 fake v3 两臂均成功：

```text
linear fake v3 = success
langgraph fake v3 = success
provider sessions = 0
prompt SHA parity = true
final tree = a5b249e710d1253bee4c099faf91e45f9ebfbddd
final diff SHA-256 =
d8e20d91ebe30ca5056be1b3e4d84d989dbba6fd2a16829baecf0620bdc4d33b
```

Fake v3 证明本地协议实现具备：

- 人工冻结 CP01 / CP02 / CP03；
- exact scope 与前序 diff 防回改；
- Linear checkpoint evidence cursor；
- 真实 LangGraph 节点、CP02 SQLite interrupt 与 CP03-only resume；
- sealed handoff；
- final tree / canonical diff identity；
- event hash、duplicate claim、DLP 和 canary 门禁。

这些都只是 harness readiness，不是 provider 实验结论。

---

## 3. 真实执行事实

Gate 7A：

```text
baseline tag = gate-7a-pre-run-v1
consumed tag = gate-7a-consumed-v1
provider sessions used = 1
tokens used = 11,965
checkpoint started = CP01
checkpoint completed = none
automatic retry events = 0
runner returncode = 1
termination confirmed = true
```

直接错误：

- nested Codex collab spawn 找不到当前 thread；
- Sandbox Proxy 无法连接 Sandbox Provider `/responses` 上游；
- local proxy 返回 `502 Bad Gateway`；
- worker repo 没有代码改动。

Gate 7C：

```text
consumed tag = absent
provider sessions used = 0
output / fixture = absent
```

---

## 4. 这轮真正证明了什么

### 4.1 证明了控制面 fail-closed

真实 provider 失败后：

- 没有自动 retry；
- 没有进入 CP02；
- 没有伪造 planned migration；
- 没有创建 handoff；
- 没有启动 Machine F；
- 没有启动 Gate 7C；
- 没有代码 scope 副作用；
- consumed baseline 被保留，避免通过删除失败现场重跑。

### 4.2 证明了 readiness 与 real result 必须分开

Fake v3 的两臂成功不能覆盖真实 provider 的失败。Gate 7 最终结论必须由 real arm 决定，
不能把本地 oracle 预演写成“大任务成功”。

### 4.3 暴露了两个前置能力缺口

1. TCP 可达只证明 loopback listener 存在，不能证明 Sandbox Proxy 的上游 `/responses` 路由健康。
2. nested Codex worker 尝试使用 collab，但真实 session 没有可用 thread identity。

这两个问题都发生在业务代码执行前，应成为下一轮的独立 readiness 门禁。

---

## 5. 未完成事项

- Gate 7A CP01 代码变更与验证；
- Gate 7A CP02；
- Gate 7A CP03；
- sealed handoff 与 Machine F；
- Gate 7A real success summary；
- Gate 7C 三节点真实执行；
- Linear / LangGraph 的真实 token、latency、state overhead 对照；
- `contract-equivalent` 或 `completed-with-overhead` 结论。

---

## 6. 下一步建议

下一步不是删除 `gate-7a-consumed-v1` 后重试。

应单独设计 Gate 7 R2：

1. 先修复或切换 Sandbox Proxy 上游路由；
2. 在不消耗模型 session 的前提下，增加比 TCP 更强的 provider route readiness；
3. 明确 nested Codex 的 collab 能力，或让 fixture worker 不依赖 unavailable collab thread；
4. 重新冻结新的 case hash、baseline commit、A/C tags、consumed tags 和 session；
5. 仍然先 A，A 成功后才触发 C；
6. 保留 v1 branch commit、baseline tags、consumed tag 与失败 artifacts。

当前产品决策保持不变：

```text
default engine remains linear
```

脱敏机器结果清单见 `eval/gate-7/result-v1.json`。
