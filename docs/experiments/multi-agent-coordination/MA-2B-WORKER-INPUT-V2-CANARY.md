# MA-2B Worker 输入 v2 能力 Canary 协议

> 冻结日期：2026-07-26  
> 分支：`experiment/ma2b-planner-worker-pilot`  
> 代码基线：`49ecc19fe4cf7acd7fb9e0c4a736b3b8fb6312d3`  
> 状态：`protocol_frozen / owner_authorized`
> 后续 supersession：正式 Pilot 输入资格与 Worker Token 语义由
> `MA-2B-PILOT-INPUT-QUALIFICATION-V1.md` 控制；本文件继续作为历史 Canary 协议保留。

## 一、目的与结论边界

本 Canary 只回答：

> 在 Worker 不获得 verification command、oracle 或 shell 类型，且确定性验证完全由 Vega
> 控制面执行的 v2 输入协议下，同一小任务的 A/B/C 是否都能形成范围合法、可验证的首次结果？

本轮不是预注册的 12 case Pilot，不调用 Reviewer，不形成产品采用、成本节省、MA-3、
原生子 Agent 或 multi-worker 结论。

既有 v1 B/C 运行保留为问题发现样本，不与本轮 v2 结果合并计分，也不得被本轮结果覆盖。

## 二、冻结输入

- case：`MA2B-F01`
- package role：`fake_driver_fixture`
- task-pack SHA-256：
  `a12f4bcbf7be3a8fbc39963cd4143efba7f9cfbbe36fda57f988ffa4196daf85`
- 任务：仅修改 `src/textops.py`，让标签归一化同时移除首尾空白并折叠连续内部空白
- 允许写入：`src/textops.py`
- 最大变更文件数：`1`
- 最大新增文件数：`0`
- 最大 diff 行数：`40`
- Worker 时间上限：`180` 秒
- Worker token 观测预算：`5000`；当前 CLI 无法强制终止 token 超额，因此只记录实际 usage，
  不把该字段声明为已执行门禁
- 控制面 verification：`python -m pytest -q tests/test_textops.py`
- oracle：全部命令退出状态为 `0`
- treatment 顺序：`A -> B -> C`

三路必须从同一 fixture tree 建立独立、干净的 Git workspace，并使用互不重叠的 control
directory、run directory 与 Git index。Provider 调用使用 `--ephemeral` 新会话，不复用前一
treatment 的聊天或输出。本 Canary 不宣称已达到正式 Pilot 的完整 Provider cache 隔离要求。

## 三、冻结 treatment

| Treatment | Planner | Worker | Worker 编译器 |
|---|---|---|---|
| `A` | 不调用；控制面编译约束计划 | `gpt-5.6-sol` | `compiled-context-v2` |
| `B` | `gpt-5.6-sol`，`xhigh` | `gpt-5.6-sol`，`none` | `plan-contract-v2` |
| `C` | `gpt-5.6-sol`，`xhigh` | `gpt-5.6-luna`，`none` | `plan-contract-v2` |

Provider 执行绑定：

- Codex CLI：`0.145.0`
- Provider：本机 CC Switch gateway
- wire API：`responses`
- Worker sandbox：`workspace-write`
- Planner sandbox：`read-only`
- Planner 与 Worker 均使用 `--ephemeral`
- 不在仓库、运行协议或结果摘要中记录 API key、Authorization header 或原始凭据

## 四、v2 输入边界

三路 Worker 均不得收到：

- verification command；
- oracle；
- shell 类型；
- ground truth 或参考补丁；
- 其他 treatment 的输出；
- Planner / Worker 完整聊天；
- 控制面 artifact 路径或可写入口。

Worker prompt 必须明确：

- 只修改 `allowed_write_paths`；
- 不运行测试、静态检查、构建或其他验证命令；
- 返回后由控制面执行确定性验证；
- 不 commit、不 push、不改写控制面 artifact。

Scope Gate 必须先于 verification。只有 Scope Gate 通过后，控制面才执行冻结命令；验证进程
设置 `PYTHONDONTWRITEBYTECODE=1`、禁用 pytest 自动插件加载和 cache provider，避免验证本身
污染目标 workspace。

## 五、有效结果与停止线

每个 treatment 只执行一次，不 retry、不 replan、不自动升级模型。

一个结果只有同时满足以下条件才有效：

1. Worker 进程正常完成；
2. 目标 diff 满足冻结任务事实；
3. 没有范围外文件、额外新文件、HEAD 变化或预算越界；
4. Scope Gate 通过；
5. 控制面 verification 返回 `0`；
6. canonical DelegationAttempt 校验为 `valid`。

发生以下任一情况时，该 treatment 记为无效并继续保留现场，不覆盖、不补跑：

- Provider、认证、网络、超时或模型不可用；
- Worker 自行运行验证并产生范围污染；
- scope、snapshot、artifact 或 Attempt 绑定失败；
- verification 非零、缺失或未执行；
- 需要修改 task、模型档位、执行顺序、成功条件或停止线才能通过。

若 `C` 再次无法形成有效结果，下一步不得扩展 receipt、ledger、manifest、Reviewer 或其他
证据层，应直接评估当前 budget Worker 是否不适合该委派协议。若 `C` 有效，本轮也只允许
讨论是否进入正式 MA-2B task-pack，不能直接进入 MA-3。
