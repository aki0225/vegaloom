# DV-B05 重跑 V3 预注册合同

> 预注册日期：2026-07-30
>
> Owner 再次确认 Provider 已恢复稳定，并授权第三个独立实验版本。

## 1. 历史结果边界

- `dv-b05-native-20260730`：Provider 中途 `502/503`，部分错误 patch，判定为无效基础设施样本。
- `dv-b05-native-r2-20260730`：Provider stream 断开后超时，无代码修改，判定为无效基础设施样本。
- 两条结果和原始证据继续保留，不删除、不改写。
- V3 新运行 ID 固定为 `dv-b05-native-r3-20260730`。
- V3 属于 `DV-B05` revision `5`、实验版本 `direct-rerun-v3`。

## 2. 隔离与冻结输入

- 使用全新的 baseline-only workspace、Codex 会话、venv、临时目录和证据目录。
- Worker 与 Reviewer 不得读取前两次运行的 patch、事件流、验证结果或结论。
- baseline：`6ec99f89261b32f8a50848786eca055e1967659f`
- 模型：`gpt-5.6-sol`
- reasoning effort：`medium`
- Worker timeout：`1200` 秒
- 允许修改：
  - `src/click/termui.py`
  - `tests/test_termui.py`
- 固定验证：
  - `python ../verifier.py .`
  - `python -m pytest -q tests/test_termui.py --basetemp ../pytest-temp`
- verifier SHA-256：
  `2d5e44e6432bb34c436c24a3a8254f51da916efe5676adc1574cc4b2161e2584`

当前 Provider profile 冻结为：

```text
profile_fingerprint: f04e21fe35621dba4cddd010abdb9a8251b5200224df9f41b373fcdaef70ae17
provider_origin_sha256: 6d04c9e03d12fcd92ccf96ef5b973056f0c28d35d37d8668851b4d8390266909
wire_api: responses
```

## 3. 唯一一次执行规则

1. V3 Native Worker 只允许一次正式调用。
2. 不增加额外模型健康请求；Owner 的当前稳定性确认作为启动依据。
3. 只有 Worker 产生 `turn.completed` 且两项固定验证全绿，才启动独立只读 Reviewer。
4. Reviewer 使用同一 profile、模型与 reasoning，只接收任务、diff、项目规则和验证证据。
5. 任一 Provider 故障、timeout、越界修改或验证失败都立即停止，不再次重跑 V3。
6. Native 与 Reviewer 封存后，再单独决定是否授权 Vega treatment。

## 4. 正式运行结果

V3 Native Worker 与独立只读 Reviewer 均已各正式运行一次：

- Worker 正常产生 `turn.completed`，退出码 `0`，未超时；
- tracked diff 仅修改两个允许文件；
- 封存 verifier 8 个场景全部通过；
- 目标 pytest 为 `237 passed, 11 skipped`；
- Reviewer 正常产生 `turn.completed`，workspace 保持不变；
- Reviewer 没有发现代码缺陷，但因输入中缺少 verifier 验证前后实际哈希记录而返回
  `needs_human`。

Reviewer 完成后已补充一次带前后哈希的固定验证：verifier hash 前后均匹配冻结值，两项
验证再次全绿。该补充证据没有追溯改写 Reviewer verdict，也没有再次调用 Reviewer。

本版本登记为：

```text
run_status=completed
final_disposition=needs_human
comparison_eligibility=pending_human_adjudication
```

Vega treatment 尚未授权。完整公开记录见
`eval/experiments/daily-value-validation/runs/DV-B05-native-r3-20260730.md`。
