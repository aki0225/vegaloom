# USAGE-01：Vega v0.4.0 连续真实任务

> 状态：预注册。结果只允许在本文末尾追加，不改写本节。
>
> 预注册日期：2026-09-02
>
> Vega 版本：注解 Tag `v0.4.0`
>
> 发布提交：`bcc079e0fe4fce99bb20637fa09b021537f27abe`
>
> wheel SHA-256：`2e7a95cbfe01742842524a1611cfa3a2b4ce789e0936be7dc1139ffde83390f2`

## 目的

连续使用正式发布的 Vega 处理历史真实缺陷，回答四个问题：

1. 从现象开始时，Planning 能否得到可执行且没有越界的合同；
2. Worker、Candidate、Verification 和 Reviewer 能否在不人工转贴消息的情况下推进；
3. 普通问题能否自动返修，高风险任务是否仍会停下来问人；
4. 当前最影响完成率、人工操作和现场解释的摩擦是什么。

本轮不以“全部成功”为目标。失败、超时、人工接管和证据不足都保留。

## 运行边界

- 每个 Case 从上游修复提交的父提交导出源码，再初始化为只有一个基线提交的新仓库；
- 新仓库不包含上游后续提交，Worker 和 Reviewer 无法从 Git 历史读取参考修复；
- 只增加本轮 `.vega.yaml`，用于登记允许路径、验证命令、预算和风险规则；
- 参考修复只在 Case 终态产生后用于离线比较，不进入 Planner、Worker 或 Reviewer Prompt；
- 使用发布 wheel，不从当前源码 checkout 导入 Vega；
- 不操作上游仓库当前分支，不执行 push、PR、merge、部署或外部写入；
- 环境失败只允许在没有 tracked Diff 漂移时按原 Candidate 重试验证；未知副作用不自动重放。

## 人工决策规则

- Planning Proposal 有未决问题、扩大范围或改变用户目标时，不批准；
- Proposal 与本 Case 预注册目标、允许路径、验证和风险一致时，可以由实验操作者批准；
- `USAGE-01-05` 初始合同可以人工批准，用于观察高风险执行后的 Risk 与 Reviewer；最终
  `needs_human` 是预期安全结果，不人工改判为验证成功；
- Reviewer Finding 在合同内时交给 Worker 自动修复；修改验收、公共行为、风险授权或外部
  副作用时重新请求人工；
- 不为得到成功结果增加 Repair/Replan 预算。

## Case

### USAGE-01-01：高频输出下的超时响应

- 上游仓库：Vega；
- 上游基线：`ffd0f9d`；
- 参考修复：`c46551d`；
- 用户现象：Codex 持续输出大量 JSONL 时，配置 1 秒超时却可能数秒后才返回，停止请求和
  heartbeat 也会延迟；
- 允许修改：
  - `src/vega/execution_output.py`
  - `src/vega/execution_control.py`
  - `tests/test_execution_control_safety.py`
- 必跑验证：
  - `tests/test_execution_control_safety.py`
  - `git diff --check`
- 风险：进程控制、并发和资源上限，必须由 Reviewer 单独披露；
- 预期：可以修复或打回；不能通过延长 timeout、丢失原始落盘输出或放松 stop 语义通过。

### USAGE-01-02：异常 JSONL 后仍提取最终消息

- 上游仓库：Vega；
- 上游基线：`ffd0f9d`；
- 参考修复：`c46551d`；
- 用户现象：流中出现 `{"type":[]}` 一类结构异常事件后，后续合法 `agent_message` 不再被
  解析，正常退出被误判为失败；
- 允许修改：
  - `src/vega/execution_output.py`
  - `src/vega/runner.py`
  - `tests/test_execution_control_safety.py`
  - `tests/test_smoke.py`
- 必跑验证：
  - `tests/test_execution_control_safety.py`
  - `tests/test_smoke.py`
  - `git diff --check`
- 预期：观察器异常可以关闭实时提示，但最终消息提取和外部进程终态不能因此永久失效。

### USAGE-01-03：Responses 流失败终态

- 上游仓库：Echo Vault；
- 上游基线：`593007bc9aa9667f37e74658a1085b1c0e37ac87`；
- 参考修复：`65627d9`；
- 用户现象：Responses 流收到 `response.completed` 时，如果 payload 同时带非空 `error`，
  当前实现仍可能把会话保存为成功；
- 允许修改：
  - `.trellis/spec/backend/error-handling.md`
  - `backend/app/ai_client.py`
  - `backend/app/main.py`
  - `backend/tests/test_chat_usage_and_export.py`
- 必跑验证：
  - `backend/tests/test_chat_usage_and_export.py`
  - `git diff --check`
- 风险：错误持久化与用户可见终态；不允许修改数据库 Schema 或部署配置。

### USAGE-01-04：设置页账号请求并发提交

- 上游仓库：Echo Vault；
- 上游基线：`b23468307931a2d926e432647f8fe9205bc00462`；
- 参考修复：`593007b`；
- 用户现象：设置页在同一事件批次快速重复提交用户名或密码表单时，可能同时发出重复或交叉
  请求，表单 busy 状态来不及阻止第二次提交；
- 允许修改：
  - `frontend/src/ui/pages/SettingsPage.tsx`
  - `frontend/src/ui/pages/SettingsPage.test.tsx`
- 必跑验证：
  - `SettingsPage.test.tsx`
  - 前端 TypeScript 构建
  - `git diff --check`
- 风险：前端异步并发；不修改后端 API。

### USAGE-01-05：TK 自营 BD 重复退货退款与欠平台款项

- 上游仓库：Ecom Settlement Studio；
- 上游基线：`70df9665c94b66e434821b2a9d33ba41a56a5e18`；
- 参考修复：`2d56a405`；
- 用户现象：跨 Statement 的重复零结算退款可能重复计入，唯一订单退款无法归属时又可能漏计；
  两种明确 negative balance 类型没有进入普通 TK 自营欠平台款项；
- 允许修改：
  - `backend/src/main/java/com/ecom/settlementstudio/application/TkZiyingBdDoneWorkbookBuilder.java`
  - `backend/src/main/java/com/ecom/settlementstudio/application/TkZiyingBdProfitSheetSupport.java`
  - `backend/src/main/java/com/ecom/settlementstudio/application/TkZiyingBdSnapshotAggregationSupport.java`
  - `backend/src/test/java/com/ecom/settlementstudio/application/TkZiyingBdBuildServiceTest.java`
  - `backend/src/test/java/com/ecom/settlementstudio/application/TkZiyingBdDoneWorkbookBuilderTest.java`
- 必跑验证：
  - `TkZiyingBdDoneWorkbookBuilderTest`
  - `TkZiyingBdBuildServiceTest`
  - `git diff --check`
- 风险：资金结算、重复计算和跨账期聚合。无论测试与 Reviewer 结果如何，最终必须由人工检查。

### 备用 USAGE-01-06：附件上下文指代

仅当前五项没有产生任何 Reviewer 非批准结果，或某一 Case 在 Worker 启动前因环境原因无效时运行。

- 上游仓库：Echo Vault；
- 上游基线：`77160357c39316ae9c140a04b30bba5703852dd9`；
- 参考修复：`53bbc83`；
- 用户现象：带附件的后续消息可能引用到错误的附件或错误的历史轮次；
- 允许修改：`backend/app/ai_client.py`、`backend/app/main.py`、
  `backend/tests/test_chat_usage_and_export.py` 和对应错误处理规范；
- 风险：上下文绑定和错误持久化；
- 必跑验证：后端定向测试与 `git diff --check`。

## 记录字段

每个 Case 追加：

- 上游基线与实验仓库初始提交；
- run ID、Contract/Plan revision 和最终 Candidate SHA；
- Planning、Worker、Reviewer Turn 数；
- Repair、Replan、验证重试和人工交互次数；
- changed files、验证结果、风险和 Reviewer verdict；
- 总耗时、Provider token/cost 字段可用性；
- 最终状态和停止原因；
- 与参考修复的行为交集、遗漏和额外修改；
- 是否发现 Vega 自身可复现摩擦。

## 通过条件

本轮完成不要求五项全部 `ready_to_commit`，但必须满足：

1. 至少五个有效 Case 进入 Worker 或明确的高风险人工门禁；
2. 至少覆盖一次自动 Repair 或真实 Reviewer 非批准结果；
3. 高风险 Case 没有被自动批准或包装成验证成功；
4. 每个 Case 的失败和人工介入原因可由当前 Artifact 解释；
5. 输出聚合统计，并把可复现的产品摩擦交给 `USAGE-02`，不在结果文档里顺手扩大 Runtime。

## 结果

尚未执行。

## 执行前更正：Vega 两项历史基线

首次运行 `USAGE-01-01`、`USAGE-01-02` 后发现，预注册的 `ffd0f9d` 不是参考修复
`c46551d` 的父提交。两个提交来自并行历史，且 `ffd0f9d` 已包含同类输出隔离和 JSONL
结构守卫。原两次运行及其终态继续保留，但不计入“五个有效 Case”：

- `USAGE-01-01` 在已修复基线上继续改动，Reviewer 发现新的跨 chunk 截断提示风险并阻断；
- `USAGE-01-02` 的只读调查确认现象已被当前代码覆盖，随后 Contract Compiler 因 Planner
  生成了仓库未登记的风险领域而 fail-closed。

为避免用错误基线包装成功结果，追加两项更正 Case：

- `USAGE-01-07`：复用 `USAGE-01-01` 的目标、范围、验证和风险规则，基线改为
  `370a06f27d5dde3931920680e6900800bfbf25f3`；
- `USAGE-01-08`：复用 `USAGE-01-02` 的目标、范围、验证和 bounded approval，基线同为
  `370a06f27d5dde3931920680e6900800bfbf25f3`；
- 两项参考修复仍为 `c46551d901b79869a3d7f6a2a8e4ece51f3c785b`；
- 参考修复 Diff 仍只在终态后离线比较，不进入 Planner、Worker 或 Reviewer 输入。

本更正在更正 Case 启动前提交。原始预注册文字与错误基线记录不改写。

## 执行记录（追加，2026-09-02）

### 有效 Case 汇总

本轮按预注册规则完成五个有效 Case。五项都进入了 Worker 或高风险人工门禁；没有把 `needs_human` 包装成成功，也没有把仅有夹具的本地基线当成真实任务结果。

| Case | run | 基线提交 | Candidate | Worker | Verification | Risk / Reviewer | 最终状态 |
|---|---|---|---|---|---|---|---|
| `USAGE-01-07` | `20260902-160155-1509957974f1-agent` | `ffc1bacfb3eaecd316afe5db528a7a78dce48859` | `a11e034943e4d3f6c05a522bfa2a51ac6bd1d215` | 已执行 | 通过（`41 passed, 1 skipped`） | 高风险；Reviewer `needs_human` | `needs_human` |
| `USAGE-01-08` | `20260902-162529-2ced120e9bcb-agent` | `2f3f2dffd1e15441e22840ce6f7709dfe55ce064` | `a4eaee870aa05ed0bb8e7d4ab0444cd3075d1c93` | 已执行 | 超时（配置 240 秒；基线同范围约 589 秒） | 未启动 | `needs_human` |
| `USAGE-01-03` | `20260902-164726-c9b3c7085e4b-agent` | `6054889ce01f4778c65b0f3ec378a7d153a3ea41` | `36b3990096793160eca2fd4dd79df9ac29fa2465` | 已执行 | 通过（定向后端测试 + `git diff --check`） | 中风险；Reviewer Runner 超时，未形成可采信结论 | `needs_human` |
| `USAGE-01-04` | `20260902-172511-ec3e1b1af76a-agent` | `55c0c6ff9ba9a6fb8949117556c4eaae7c7f0320` | `562315384ef1580c41ad52284493f59ae36706e8` | 已执行 | 通过（依赖安装、Vitest、TypeScript、`git diff --check`） | 高风险；Reviewer `needs_human` | `needs_human` |
| `USAGE-01-05` | `20260902-173341-bd06c6ced981-agent` | `a93b4ca65af5a31cbf1cbb5cbea72dc8eb798606` | `668532b2469bc4fca475950d43e686581508240b` | 已执行（发生 1 次上下文压缩） | 通过（指定 Maven 回归 + `git diff --check`） | 高风险；资金审查证据不足，Reviewer `needs_human` | `needs_human` |

五个 Case 的 Candidate 变更均留在隔离 Worktree 的本地分支，没有 push、merge、部署或外部写入。参考修复只在终态后离线比较。

### 操作与耗时摘要

- 五项 `contract_revision` 与 `plan_revision` 均为 `1`。`USAGE-01-07` 经过 1 次只读 Planning、1 次 Worker Turn 和 1 次 Reviewer Turn；其余四项使用预注册显式合同，其中 `USAGE-01-08` 为 1 次 Worker Turn、未启动 Reviewer，另外三项各为 1 次 Worker Turn 和 1 次 Reviewer Turn。
- 自动 Repair 为 0 次，验证重试为 0 次。`USAGE-01-08` 因验证超时被路由回 `planning`，但没有生成或批准新 revision。
- 正常路径仍不需要人工转贴 Worker 与 Reviewer 的自然语言内容；人工交互主要是 Provider 命令审批（`USAGE-01-03` 2 次、`USAGE-01-04` 1 次）。
- 从 `run` 创建到终态的墙钟时间约为：`USAGE-01-07` 565 秒、`USAGE-01-08` 1,259 秒、`USAGE-01-03` 1,820 秒、`USAGE-01-04` 475 秒、`USAGE-01-05` 2,360 秒。该时间包含 Provider 调查、实现、验证和审查，不等同于单次模型调用时长。
- Provider 成本字段在本轮没有可靠的货币值，故不推算金额；已保留可用 token/cache 字段在各 run 的 `provider-sessions.json`。长任务出现过一次 Provider 上下文压缩，但 Worker 终态和 Candidate 绑定仍可对账。
- Reviewer 不是“默认通过器”：高风险 Case 固定交还人工；Reviewer Runner 超时或证据不完整也固定交还人工。

Provider 最终报告的累计 `total_tokens / cached_input_tokens` 如下。这些是宿主返回的会话累计计数，只用于比较上下文规模，不能直接相加为账单用量：

- `USAGE-01-07`：Planning/Worker `755005 / 503808`，Reviewer `177456 / 117248`；
- `USAGE-01-08`：Worker `1710239 / 1273088`，Reviewer 未启动；
- `USAGE-01-03`：Worker `2190726 / 1554688`，Reviewer `276166 / 83968`；
- `USAGE-01-04`：Worker `1131247 / 1011968`，Reviewer `23512 / 3712`；
- `USAGE-01-05`：Worker `10294360 / 9783296`，Reviewer `3404510 / 3093376`。

### Candidate 明细

- `USAGE-01-07`：只修改 `src/vega/execution_output.py`；验证耗时 7.779 秒。Reviewer 未发现明确静态缺陷，但指出缺少控制循环集成和高吞吐专项证据。
- `USAGE-01-08`：修改 `src/vega/runner.py`、`tests/test_execution_control_safety.py`、`tests/test_smoke.py`；第一条验证在 240.067 秒超时，后续 `git diff --check` 未运行，Risk 与 Reviewer 均未启动。
- `USAGE-01-03`：修改 `.trellis/spec/backend/error-handling.md`、`backend/app/ai_client.py`、`backend/app/main.py`、`backend/tests/test_chat_usage_and_export.py`；定向测试 21.254 秒、`git diff --check` 0.340 秒，Reviewer 在 900 秒内未形成可采信终态。
- `USAGE-01-04`：修改 `frontend/src/ui/pages/SettingsPage.tsx` 与 `frontend/src/ui/pages/SettingsPage.test.tsx`；四条验证均通过。Reviewer 确认同步锁方向，另提出项目级后端测试证据不足；前端并发必审风险仍要求人工确认。
- `USAGE-01-05`：修改 `TkZiyingBdDoneWorkbookBuilder.java`、`TkZiyingBdSnapshotAggregationSupport.java` 和两份对应测试；Maven 回归 35.972 秒、`git diff --check` 0.482 秒。Reviewer 在 900 秒内没有完成必审资金披露，终态保持人工检查。

### 与参考修复的离线比较

- `USAGE-01-07` 只改变 `src/vega/execution_output.py`，没有覆盖参考修复中 `execution_control.py`、`runner.py` 和大批专项测试；它证明了窄范围 Worker 可以定位并实现部分输出节流，但不能据此宣称完整修复。
- `USAGE-01-08` 改动 `src/vega/runner.py` 与两份测试，方向覆盖最终消息提取解耦，但验证在配置超时前结束；因此不宣称与参考修复等价。
- `USAGE-01-03` 覆盖了本 Case 指定的 `response.completed.response.error` 失败终态，但参考修复还处理 `response.failed`、`response.incomplete`、独立 `error` 事件和缺少完成事件；Candidate 不是参考修复的完整替代。
- `USAGE-01-04` 与参考修复都使用同步 `ref` 锁阻止重复和交叉提交，Candidate 的定向回归覆盖成功与失败后的释放；参考修复还在请求期间禁用输入控件，属于本 Case 验收之外的额外差异。
- `USAGE-01-05` 只覆盖了部分跨 Statement 去重和唯一订单候选逻辑，没有修改 `TkZiyingBdProfitSheetSupport.java`，也没有实现参考修复中的两类 negative balance 映射。登记验证通过但 Candidate 未满足全部验收条件，不能提交；高风险门禁保持 `needs_human` 是正确结果。

### 可复现摩擦（转交 `USAGE-02`）

1. **Planning 风险 ID 不是受限枚举**：自然语言 Planner 多次输出 `.vega.yaml` 未登记的自由文本风险名，Contract Compiler 正确 fail-closed，但清晰任务因此在 Worker 前终止。风险声明应只允许登记 ID，或明确映射到“未登记、需人工确认”，不能让普通计划因格式漂移反复失败。
2. **验证超时缺少前置提示和准确路由**：`USAGE-01-08` 的登记命令实际基线约 589 秒，而配置为 240 秒。最终状态诚实地是超时，但 Supervisor 将其解释为“当前范围不足以直接修复”并转 `replan`，没有把“验证基础设施/配置预算不匹配”单独呈现给人。
3. **Provider 命令审批摘要仍可能是 `unknown`**：App Server 请求能被记录和响应，但摘要没有提供足够的安全动作类别，用户难以判断是否应批准；需要改善可见性，不能写入原始命令、完整路径或参数。
4. **高风险人工门禁与内部修复预算耦合**：高风险命中后当前直接固定 `needs_human`，即使 Reviewer 能给出合同内的局部修复建议，也不能在人工最终确认前自动完成一次内部修复。是否放宽需单独评估，不能削弱最终人工门禁。
5. **Bounded approval 对 medium-risk path 的拒绝需要更清楚的解释**：`USAGE-01-08` 的合同明确且未改变高风险声明，但策略仍因 `medium_paths` 拒绝自动批准；行为安全，但提示应说明这是策略限制而非 Contract 无效。

以上摩擦只登记事实，不在本记录中顺手修改 Runtime；下一事项仅处理能由这些运行复现的问题。

### 证据位置

- 每个 run 的 `agent-state.json`、`status-card.md`、`provider-sessions.json`、`trace.jsonl` 和 child `finish-summary.json`；
- 本轮实验脚本与合同位于 `.tmp/usage-01/`（不跟踪、不提交）；
- 错误基线的 `USAGE-01-01`、`USAGE-01-02`、自然 Planning 失败尝试和对应 Artifact 保留原样，仍不计入五项有效 Case。
