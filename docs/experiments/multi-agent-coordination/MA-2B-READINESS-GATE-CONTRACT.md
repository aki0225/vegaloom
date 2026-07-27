# MA-2B readiness gate 合同

> 日期：2026-07-25<br>
> 分支：`experiment/ma2b-planner-worker-pilot`<br>
> 当前状态：`readiness_gate_fake_verified / real_execution_blocked`
>
> 2026-07-27 状态更新：正式默认根已具备完整 12-case，并可生成固定
> `case_set_sha256`。readiness 合同与 12-case gate 未放宽；当前阻断与 case 缺失无关，
> 仍因正式 execution binding 和 authorization 缺失而 `blocked`。详见
> `MA-2B-PILOT-INPUT-QUALIFICATION-V1.md`。

## 一、边界

本 Slice 只把已冻结的本地合同串成真实 Pilot 启动前的 readiness gate；在该 Slice 时点不创建
真实 `MA2B-Cxx` task-pack，也不创建真实 execution binding，不读取 Provider 凭据，不调用
Planner、Worker、Reviewer 或 Provider。后续候选 task-pack 不改变这些执行边界。

`check_ma2b_pilot_readiness()` 的默认行为是 fail-closed：只要缺任一真实前置 artifact，结果就是
`blocked`。

## 二、默认检查项

readiness gate 固定检查：

1. `MA2B-C01`～`MA2B-C12` 全部能由 `load_ma2b_case_package()` 加载；
2. 每个 case 必须是 `pilot_case`，不能把 `MA2B-Fxx` fake fixture 计入真实样本；
3. 12 个 case 的 task-pack hash、ground truth、case class、预期结果与 verification 命令可形成
   `case_set_sha256`；
4. `eval/experiments/multi-agent-coordination/MA-2B-execution-binding.md` 能通过 execution binding
   loader；
5. execution binding 引用的 pricing manifest 已通过 pricing schema、hash 与模型映射校验；
6. `eval/experiments/multi-agent-coordination/MA-2B-execution-authorization.json` 存在并通过授权
   schema；
7. authorization 绑定的 execution binding hash、pricing manifest hash 与 `case_set_sha256` 必须与
   当前读取结果一致；
8. authorization 的 UTC 时间不得早于 execution binding 的 Provider 可用性观测时间，也不得晚于
   execution window start。

## 三、授权 artifact

授权 artifact 是公开 JSON，不包含 endpoint、token、API key、本机路径或 Provider 凭据：

```json
{
  "schema_version": 1,
  "scope": "ma2b_pilot_execution",
  "decision": "authorized",
  "authorized_by": "<owner 或 independent-review 标识>",
  "authorized_at_utc": "<UTC 时间>",
  "execution_binding_sha256": "<sha256>",
  "pricing_manifest_sha256": "<sha256>",
  "case_set_sha256": "<sha256>",
  "notes": "<可选公开备注>"
}
```

## 四、拒绝条件

以下情况必须 `blocked`：

- 任一 `MA2B-Cxx` 缺失、schema 无效、hash 不匹配或 case identity 不一致；
- 任一 case 被 fake fixture role 冒充；
- execution binding 缺失、schema 无效、pricing manifest 无效或 hash 不匹配；
- authorization 缺失、JSON 无效、字段未知、包含敏感信息或本机路径；
- authorization 绑定的 execution binding、pricing manifest 或 case set hash 与当前读取结果不一致。
- authorization 时间早于 execution binding 观测时间，或晚于执行窗口开始。

## 五、停止线

readiness gate 只说明“是否允许进入真实 Pilot 的前置条件检查”，不负责执行 Pilot。即使 gate
返回 `ready`，真实调用仍需要单独的执行入口遵守预注册的三路隔离、随机化、Reviewer 盲化、
故障注入和最终人工/独立复审要求。
