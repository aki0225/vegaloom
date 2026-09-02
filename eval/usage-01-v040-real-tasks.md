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
