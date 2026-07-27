# ATG Approval Worker 拓扑试跑交接

> 交接日期：2026-07-27
> 工作分支：`experiment/ma2b-pilot-next`
> 当前结论：正式 MA-2B 输入已迁移；ATG 拓扑试跑被人工中断，不构成实验结果

## 一、本次已提交的 MA-2B 状态

提交 `399e746` 已将 `MA2B-C01`～`MA2B-C12` 的 task-pack 与 ground truth 从冻结候选根
迁入正式默认根：

```text
eval/experiments/multi-agent-coordination/task-pack/
eval/experiments/multi-agent-coordination/ground-truth/
```

迁移没有改变 12 个 case 的身份和内容：

- 72 个正式 artifact 与冻结候选逐字节一致；
- case-set SHA-256 仍为
  `33b2caa335b417b47ee45bb5de7051aef20682bbf938eddf5d2e4ad5d3d4f137`；
- readiness 默认根可以加载全部 12 个 case；
- readiness 仍然 fail-closed，只剩
  `execution_binding_path_invalid` 与
  `execution_authorization_path_invalid`；
- 没有生成 pricing、execution binding 或 owner authorization，也没有因此授权正式 Pilot。

本次验证：

```text
python -m pytest tests/test_ma2b_task_pack.py tests/test_ma2b_readiness.py -q
26 passed

python -m compileall src scripts/check_repository_hygiene.py scripts/check_architecture_growth.py
ruff check src tests scripts/check_repository_hygiene.py scripts/check_architecture_growth.py
python scripts/check_repository_hygiene.py --base-ref origin/main
```

上述静态检查和仓库卫生检查均通过。

## 二、ATG 拓扑试跑的冻结设计

为回到“Multi-Worker 是否有净收益”的原始问题，本地选择了 AgentToolGate 的历史真实任务
“Approval 产品化硬化”，并冻结同一份计划比较三种 Worker 拓扑：

```text
M：两个独立 codex exec Worker，backend/frontend 并行
S：单一强 Worker，顺序完成全部切片
N：Codex 原生 Multi-Agent，主 Worker 派发恰好两个子 Agent
```

固定顺序为 `M → S → N`，固定模型为：

```text
Planner / Worker：gpt-5.6-sol，high
Reviewer：gpt-5.6-terra，high
```

Hooks、Memory、Goals、MCP、Trellis 均禁用。Reviewer 使用独立只读会话，只接收任务、
冻结计划、diff、变更路径和确定性验证结果，不接收 Worker 对话与拓扑身份。

历史基线：

```text
修复前提交：d52ee9070ce783143e762b4291f30e7b08720763
参考完成提交：370f4dab5d8dfc19f2aeafe2bf22bf45936e0152
冻结 verifier：backend/internal/app/approval_hardening_test.go
```

本地有效预检结果：

- 修复前后端测试按预期失败，失败命中冻结 Approval 行为；
- frontend check 通过；
- frontend build 通过；
- Planner 第二次调用成功并生成 backend/frontend 两个互斥写入切片；
- Planner 的集成命令只做了确定性格式规范化，未改变切片、风险、步骤或验收事实。

这些内容只证明案例和计划可用于试跑，不证明任何 Worker 拓扑有效。

## 三、中断事实

2026-07-27 17:31（Asia/Shanghai）启动 `M`。两个外部 Worker 已分别产生部分 backend 和
frontend 修改，但在 Worker 完成前，主会话被人工中断。随后只终止了本次实验 Python
进程的后代进程树。

中断时：

```text
result.json：不存在
集成 workspace：不存在
统一确定性验证：未执行
Reviewer：未启动
S / N：未启动
```

因此本次运行必须记录为：

```text
interrupted_before_worker_completion
quality_claim_allowed = false
```

不得把部分 diff、运行时长或 Worker 已写文件解释为成功率、质量或 Multi-Worker 收益。

本地中断材料保留在被忽略的 `.tmp/ma4-atg-approval-20260727-02/`，包括部分 patch 和
`control/interruption.json`。它们仅用于诊断为什么中断，不应进入正式聚合结果，也不会随
Git 推送。

## 四、晚间恢复方式

### 在当前机器继续

1. 拉取本分支最新提交并确认工作区干净。
2. 保留旧 `.tmp/ma4-atg-approval-20260727-02/` 作为中断证据，不覆盖、不续跑。
3. 从同一 ATG 提交创建新的运行目录。
4. 复用同一冻结任务、计划、模型和 `M → S → N` 顺序，从 `M` 完整重跑。
5. 只有生成三份 `result.json` 后才允许执行汇总。

### 在另一台机器继续

1. 拉取 `experiment/ma2b-pilot-next`。
2. 将 AgentToolGate 的干净副本放在 `$repoRoot/.tmp/` 下，并检出上述修复前提交。
3. 在 `$repoRoot/.tmp/` 下重新建立独立运行目录；不得访问或修改其他项目工作区。
4. 先复现修复前后端预期失败、frontend check/build 通过，再启动 Provider。
5. 若无法取得同一冻结计划原文，应停止并重新冻结一次完整试跑，不得让三个 Treatment
   使用不同计划。

本地一次性 driver 当前没有进入 Git。原因是它仍含机器工具路径和单案例常量，直接提交会把
探索脚手架伪装成产品能力。若后续证明此案例值得复放，再把“路径参数化、运行目录约束、
中断恢复和结果 schema”作为一个独立的小改动审查；本次交接不趁机扩张 Runtime。

## 五、下一步停止条件

- 不从中断的 Treatment 目录续计结果；
- 不用部分 patch 代替统一验证和 Reviewer；
- 不因单案例成功就默认启用 Multi-Worker；
- 不在完成 `M/S/N` 同计划比较前继续增加 receipt、ledger、协调协议或新抽象；
- MA-2B 正式 Pilot 在 execution binding 与 owner authorization 完成前继续保持 blocked。
