# MA-2B Worker 输入 v2 Canary Planner 适配修订

> 冻结日期：2026-07-26  
> 分支：`experiment/ma2b-planner-worker-pilot`  
> 前置协议：`MA-2B-WORKER-INPUT-V2-CANARY.md`  
> 前置协议提交：`137ef84`
> 状态：`adapter_repair_frozen / owner_authorized`

## 一、首次运行事实

首次 v2 A/B/C 运行保留在：

```text
runs/ma2b-canary/20260726-190841-v2-abc
```

首次结果不得覆盖：

- `A`：`attempt_recorded`，Attempt `valid`，控制面 verification 返回 `0`；
- `B`：premium Planner 在 Worker 前超时，Worker 未启动，workspace clean；
- `C`：premium Planner 在 Worker 前超时，Worker 未启动，workspace clean。

两次 Planner 请求均通过本机 CC Switch gateway 发出。上游在约 75 秒后返回 HTTP 502，
错误分别为 access forbidden 和 temporarily unavailable；Codex CLI 自动重连后达到冻结的
180 秒外层上限。该结果属于 Provider / Planner adapter 失败，不是 Worker 能力结果。

## 二、唯一修订

下一次全新 A/B/C 运行只修改 Planner 的结构化输出适配：

```text
删除 Codex CLI --output-schema 参数
-> 把完全相同的 PlanContract JSON Schema 放入 Planner prompt
-> 使用 --output-last-message 保存唯一 JSON
-> 返回后由本地 PlanContract 严格解析
-> 再由 DelegationReadiness 和运行时绑定校验
```

理由：

- 同一 Provider、模型和 `xhigh` 配置已在既有 canary 中通过 schema-in-prompt 路径成功产生
  严格 PlanContract；
- 当前失败只出现在经 CC Switch 传递 `--output-schema` 的请求路径；
- 本地严格解析和运行时确定性校验保持不变，不放宽 Plan、scope、verification 或 Attempt
  成功条件。

## 三、不变项

以下内容全部沿用前置协议：

- case、task-pack、任务事实、scope、预算和控制面 verification；
- treatment 顺序 `A -> B -> C`；
- A/B premium Worker 为 `gpt-5.6-sol`；
- B/C premium Planner 为 `gpt-5.6-sol`、`xhigh`；
- C budget Worker 为 `gpt-5.6-luna`；
- Worker 使用 `compiled-context-v2` 或 `plan-contract-v2`；
- Worker 不接收或运行验证命令；
- 每个 treatment 首次结果有效，不 retry、不 replan、不自动升级；
- 不调用 Reviewer，不进入正式 Pilot、MA-3、原生子 Agent 或 multi-worker。

修订运行必须创建新的三路 workspace、run directory 与 Provider ephemeral session。首次运行及
本次修订运行分别解释，不合并为同一次 treatment，也不使用首次 A 的结果替代修订运行 A。
