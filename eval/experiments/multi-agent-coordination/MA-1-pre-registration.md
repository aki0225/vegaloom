# MA-1 委派合同与就绪性预注册

> 冻结日期：2026-07-23<br>
> Gate：`MA-1`<br>
> 状态：`pre-registered`<br>
> 默认产品行为：不变

## 1. 冻结输入

- `baseline_commit`：`521f9b924241ec258c75b2ecc893bdaa3be91abd`
- `origin/main`：`521f9b924241ec258c75b2ecc893bdaa3be91abd`
- 研究合同：`docs/experiments/multi-agent-coordination/RESEARCH-AND-EXPERIMENT-PLAN.md`
- 研究合同 SHA-256：`b9aa7d0e577b468aebc3b69e1eb3f5da70f8d6472d87d092bec61997aa6ed92a`
- task-pack：不适用；本 Gate 不调用真实 Provider，也不执行真实 Worker

本 Gate 开始后不随意 rebase。若主线修复改变下列冻结合同或验证变量，应关闭本 Gate，并以
新的预注册文件建立新 Gate。

## 2. 唯一研究问题

严格、版本化并绑定当前 task、policy、workspace snapshot 的 `PlanContract`，能否在启动
Worker 前通过确定性 `DelegationReadiness` 校验，拒绝不可委派计划，并产生可审计的 route
evidence artifact？

本 Gate 不证明 Planner 质量，不证明 budget Worker 可以完成真实任务，也不产生多 Worker、
A2A 或主线合并结论。

## 3. 实现范围

只允许新增：

1. 严格 `PlanContract` 数据合同；
2. 独立的 `schema_version` 与 `plan_revision` 语义；
3. task、policy、workspace snapshot、路径、artifact 和 verification command 绑定；
4. DAG、acceptance coverage 与未决决策校验；
5. `budget_eligible`、`premium_required`、`human_required` 三种确定性结果；
6. 可落盘、可哈希复核的 `delegation-readiness.json`；
7. 对应的自动化测试与本实验文档。

明确不实现：

- Planner 或任何模型调用；
- Worker、原生子 Agent、多 Worker、mailbox 或 A2A；
- 自动 replan、retry、模型升级或长期 Memory；
- CLI 默认路径、Loop / Finish / Goal 成功语义；
- `DelegationAttempt` Runtime 或第二套 `execution.json`。

## 4. 预注册验收条件

以下条件必须全部满足，Gate 才能形成正向的实现结论：

1. 未知字段、错误类型、非法相对路径、无效 revision、未知依赖和 DAG 环被严格拒绝。
2. `PlanContract` 不能自行声明 route；route 只能由校验结果产生。
3. task artifact、workspace snapshot、policy hash、write scope、input artifact 或 verification
   command 任一错绑时，结果必须为 `human_required`。
4. 未决决策或显式人工风险存在时，结果必须为 `human_required`。
5. 合同绑定有效但超过预注册 budget 阈值或显式要求强 Worker 时，结果必须为
   `premium_required`。
6. 只有全部绑定有效、无阻塞项且满足 budget 阈值时，结果才能为 `budget_eligible`。
7. 相同输入与 validation context 必须产生相同的状态、issue code 与内容哈希。
8. route evidence artifact 必须使用 UTF-8、仓库相对语义且不包含凭据或本机绝对路径。
9. 不修改现有 CLI、默认 `linear + single reviewer` 行为或 Assurance 成功语义。
10. 新增逻辑通过受影响测试、全量回归、Ruff、compileall、仓库卫生检查和
    `git diff --check`。

## 5. Fail-closed 与停止线

出现以下任一情况时，不扩大范围：

- 需要信任 Planner 自报才能判断路径、oracle、snapshot 或风险是否有效；
- 需要放宽现有 scope、verification、artifact integrity 或 evidence freshness；
- 为了产生 `budget_eligible` 而引入隐式默认值或吞掉未知字段；
- 需要修改主线 Assurance 合同才能让实验成立；
- 需要启动真实 Worker 才能验证本 Gate 的数据合同；
- 无法区分执行前 Delegation Contract 与执行后 Assurance Contract。

## 6. 计划验证

```powershell
python -m compileall src scripts/check_repository_hygiene.py
python -m pytest tests/test_delegation_contract.py
python scripts/check_repository_hygiene.py --base-ref origin/main
python -m pytest
ruff check src tests scripts/check_repository_hygiene.py
git diff --check
```

只有明确的 `passed`、`skipped`、`failed` 数量可以作为测试证据；超时不视为通过。
