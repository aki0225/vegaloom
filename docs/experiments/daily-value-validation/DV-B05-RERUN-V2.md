# DV-B05 重跑 V2 预注册合同

> 预注册日期：2026-07-30
>
> Owner 裁决：上一轮 `dv-b05-native-20260730` 因 Provider 中途 `502/503`、没有
> `turn.completed` 且固定验证为红，作为不可比较的无效样本保留；允许以新实验版本重跑。

## 1. 结果边界

- 上一轮记录不得删除、改写或替换。
- 新运行 ID 固定为 `dv-b05-native-r2-20260730`。
- 新运行最终冻结为 `DV-B05` revision `4`、实验版本 `direct-rerun-v2`，不是 V1 隐藏重试；
  revision `4` 记录正式调用前的 Provider profile 变化。
- 使用全新的 baseline-only workspace、Codex 会话、临时目录和原始证据目录。
- Worker 和 Reviewer 不得读取上一轮 patch、事件流、验证结果或结论。

## 2. 继续沿用的冻结输入

```text
baseline: 6ec99f89261b32f8a50848786eca055e1967659f
model: gpt-5.6-sol
reasoning: medium
worker timeout: 1200 seconds
order: Native -> Vega
```

允许修改：

```text
src/click/termui.py
tests/test_termui.py
```

固定验证：

```powershell
python ../verifier.py .
python -m pytest -q tests/test_termui.py --basetemp ../pytest-temp
```

verifier SHA-256：

```text
2d5e44e6432bb34c436c24a3a8254f51da916efe5676adc1574cc4b2161e2584
```

## 3. 本版本唯一一次执行规则

1. 调用前重新证明 baseline verifier 为红、目标 pytest 为绿、workspace 无泄漏且 Provider
   profile fingerprint 与冻结值一致。
2. 不增加额外模型健康请求；Owner 已确认当前 Provider 恢复稳定。
3. Native Worker 在本版本只允许一次正式调用。
4. 只有 Worker 产生 `turn.completed`，且两个固定验证均为绿，才启动独立只读 Reviewer。
5. Reviewer 使用相同模型、reasoning 和 profile，只接收任务、当前 diff、项目规则和验证
   证据，不接收 Worker 对话或上一轮产物。
6. 任一 Provider 故障、timeout、越界修改或验证失败都立即停止，不再次重跑本版本。
7. Native 与 Reviewer 封存后，再单独决定是否授权 Vega treatment。

## 4. 正式运行结果

V2 Native Worker 已于 2026-07-30 正式运行一次：

- Worker 在初始源码检索后出现 Provider stream transport decode error；
- 直到 `1200` 秒 timeout 均未恢复；
- 进程树终止已确认；
- workspace 没有任何修改；
- 固定 verifier 仍为红；
- 目标 pytest 保持 `229 passed, 11 skipped`；
- Worker 没有 `turn.completed`，Reviewer 未启动。

本版本登记为 `timed_out / not_completed / invalid_infrastructure`，不再次重跑，也不继续
Vega treatment。正式证据见
`eval/experiments/daily-value-validation/runs/DV-B05-native-r2-20260730.md`。
