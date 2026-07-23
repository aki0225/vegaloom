# MA-1 委派合同与就绪性接力说明

> 接力日期：2026-07-23<br>
> 分支：`experiment/multi-agent-coordination`<br>
> 冻结基线：`main@521f9b924241ec258c75b2ecc893bdaa3be91abd`<br>
> 当前结论：`implementation-candidate / full-regression-pending`

## 一、当前做到哪里

`MA-0` 研究合同已经收口并冻结，`MA-1` 的最小代码实现已经完成：

- `docs/experiments/multi-agent-coordination/RESEARCH-AND-EXPERIMENT-PLAN.md`
  - 主线只承担可信 Assurance 裁决，不加入 Planner、多 Worker 或 A2A；
  - 实验第一目标改为可验证委派合同；
  - `Plan Adequacy` 改为 `Delegation Readiness`；
  - 区分 `schema_version` 与 `plan_revision`；
  - 明确 `PlanContract` 与未来 `DelegationAttempt` 的输入输出边界；
  - 冻结 A/B/C、显式计划修订和分支同步规则。
- `eval/experiments/multi-agent-coordination/MA-1-pre-registration.md`
  - 冻结 baseline、研究合同 hash、实现范围、验收条件和停止线。
- `src/vega/delegation.py`
  - 严格、版本化的 `PlanContract`；
  - task、policy、workspace snapshot、write scope、artifact 与 verification command 绑定；
  - DAG、验收覆盖、计划 revision 与父计划引用校验；
  - `budget_eligible`、`premium_required`、`human_required` 三种确定性结果；
  - 有界 JSON 读取、非法输入 fail-closed；
  - `delegation-readiness.json` 写入与内容哈希。
- `tests/test_delegation_contract.py`
  - 覆盖 schema、路径、DAG、revision、snapshot、artifact、oracle、scope、预算和 evidence
    落盘边界。

没有实现：

- Planner 或模型调用；
- Worker、原生子 Agent、多 Worker、A2A 或 mailbox；
- 自动 replan、retry、模型升级或长期 Memory；
- CLI、Loop、Finish、Goal 或 Assurance 成功语义接入；
- `DelegationAttempt` Runtime。

## 二、已经得到的验证证据

已通过：

```text
tests/test_delegation_contract.py
43 passed

delegation.py targeted coverage
96%

python -m compileall src scripts/check_repository_hygiene.py
passed

ruff check src tests scripts/check_repository_hygiene.py
passed

python scripts/check_repository_hygiene.py --base-ref origin/main
passed
```

全量测试共收集 `643` 个 node id。第一次组合分片在既有
`tests/test_assurance_verification_semantics.py` 慢测试处超过时限；拆成完整 node id 后，
已单独确认前两个慢测试分别通过。随后启动了自适应全量分片，但在完成全部分片前因本次会话
被中断而主动终止。

因此当前不能宣称：

```text
643 passed
MA-1 Gate accepted
```

这不是已观察到的代码失败，而是**全量回归证据尚未闭合**。

## 三、另一台机器的恢复命令

```powershell
git fetch origin
git switch experiment/multi-agent-coordination
git pull --ff-only

python -m compileall src scripts/check_repository_hygiene.py
python -m pytest tests/test_delegation_contract.py -q `
  --basetemp .tmp/pytest/runs/ma1-targeted
ruff check src tests scripts/check_repository_hygiene.py
python scripts/check_repository_hygiene.py --base-ref origin/main
git diff --check
```

全量 pytest 不要用超时结果冒充通过。若单次执行超过 60 秒，应使用完整 node id 分片，并为
每个分片提供独立的：

```text
.tmp/pytest/runs/<shard-name>
```

优先从下面的慢文件恢复：

```powershell
python -m pytest --collect-only -q tests/test_assurance_verification_semantics.py
```

然后按输出的完整 node id 逐项或小组执行，再覆盖其余测试文件，最终核对通过的 node id
集合与收集到的 `643` 个节点完全一致。

## 四、下一步与 Gate 判定

下一位执行者只做：

1. 完成 `643` 个 node id 的全量回归证据；
2. 复核新增合同是否有第二事实源、隐式默认 route 或本机路径泄漏；
3. 若全绿，新增 `MA-1-decision.md`，记录 `accept / partial / reject`；
4. 先向 owner 汇报，不自动进入 `MA-2`。

若发现需要改变冻结 baseline、研究合同或 route 变量，应关闭当前 Gate 并重新预注册，不在
当前结果上直接 rebase 后继续累计。

## 五、Git 边界

- 继续使用 `experiment/multi-agent-coordination`。
- 不修改或推送公开 `main`。
- 本 Gate 基于冻结的 `main@521f9b9`，在形成 Gate 结论前不随意 rebase。
- 实验能力即使通过，也不能整分支合并主线；只能从最新 `main` 单独提取已证明通用的小能力。
