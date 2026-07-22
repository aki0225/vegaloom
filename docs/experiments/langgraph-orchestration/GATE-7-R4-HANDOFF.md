# Gate 7 R4 交接记录

> 状态：`superseded-by-2026-07-20-test-closure`
>
> 日期：`2026-07-19`
>
> 续接完成：`2026-07-20`
>
> 续接分支：`gate7-r4-publish`
>
> 目标远端：`origin/experiment/langgraph-comparison`

## 1. 当前结论

R4 的有界检查安全修复已经实现并通过 Gate 7 专项回归。fake linear 与 fake LangGraph
v2 双臂也已经完成，证明修复后的 checkpoint、handoff、范围控制和最终身份在两个引擎
下保持一致。

本交接中的 `761/838` 阻塞已在 2026-07-20 的短路径复验中关闭。当前全量结果为：

```text
838 passed
0 failed
0 skipped
0 timeout-unresolved
```

已验证实现提交为 `private-gate-7-r4-crash-marker-fix-redacted`。当前已经具备
baseline freeze 条件，但本轮没有创建 baseline/consumed tag，也没有调用真实 provider。
后续以 `GATE-7-R4-TEST-CLOSURE.md` 和更新后的 readiness 为准。

## 2. 已完成

- transcript audit 严格限制为 `pwsh` 或 `pwsh.exe` wrapper；
- `rg -e` pattern 必须使用 PowerShell 单引号字面量；
- 拒绝 `$` 可展开表达式和反引号转义；
- 每条命令绑定当前 fixture repo 的规范化 `expected_workdir`；
- audit、checkpoint payload、event ledger 和 Gate 7C 复验链绑定 output/audit hash；
- Gate 7 专项 `42` 个节点全部通过；
- execution control 完整 `17 passed`；
- fake v2 双臂均为 `success`，自动重试为 `0`，计划迁移为 `1`；
- case、plan、prompt、final tree 和 canonical diff 均保持双臂一致；
- 没有创建 R4 baseline/consumed tag，没有消费真实 provider 预算。

## 3. 冻结身份

```text
case SHA-256 = e14720051ff970e489176db8ef4165f90cc382f714e341c0734c90b8acf1e737
plan SHA-256 = f39ce91758867b4e5f7c5e338c85e4f4b8e5afa5a2374aee0dee44919fce7e2d
CP01 prompt SHA = 4df182033096692bba758ca824a1d54042380ccfa202c9d424138f3e673e9fb3
CP02 prompt SHA = 51ee8cd22813b048b04794ae37d02f7964fd5f9204a79281f774ea9da02d96a8
CP03 prompt SHA = b2ae159146e5298edfe4e02c23715758640b00ab5124a0f7541dd7d32b9dd705
final tree = a5b249e710d1253bee4c099faf91e45f9ebfbddd
canonical diff bytes = 19266
canonical diff SHA-256 = d8e20d91ebe30ca5056be1b3e4d84d989dbba6fd2a16829baecf0620bdc4d33b
```

## 4. 2026-07-20 续接结果

在短路径 checkout `<short-checkout>` 复验后：

| 证据来源 | 通过数 |
| --- | ---: |
| 完整文件分片 | 473 |
| 超时文件拆分后的完整 node id | 348 |
| 系统 Python execution control | 17 |
| 合计 | 838 |

原 77 个记录的多数现象由 Windows 深路径和分片时限解释。短路径仍暴露一个真实恢复
回归：`os._exit` 发生在 Step Result 与下一条 Graph checkpoint 之间时，上一条 manifest
仍然自洽，缺少“本次提交未完成”的持久化证据。

修复新增 `graph/checkpoint-pending.json`。marker 在 Step Result 后写入，只能在新的
checkpoint 行与 manifest seal 成功后清除；硬退出会保留 marker，恢复因此 fail-closed，
且不会重复启动 worker。

## 5. 本机未提交证据

原始验证日志位于：

```text
.local-validation/gate-7-r4-full-suite/run-235523/summary.json
.local-validation/gate-7-r4-full-suite/rerun-002733/summary.json
.local-validation/gate-7/gate7-r4-fake-linear-v2/summary.json
.local-validation/gate-7/gate7-r4-fake-langgraph-v2/summary.json
.local-validation/gate-7-r4-shortpath-fix-20260720/aggregate-summary.json
.local-validation/gate-7-r4-shortpath-fix-20260720/coverage-manifest.json
```

这些目录按项目规则保持忽略，不进入 Git。换机后以本文档中的冻结值作为入口，并重新
生成本机验证证据。

## 6. 下一步顺序

1. 等待项目 owner 明确授权 baseline freeze。
2. 授权后创建并推送两个 R4 annotated baseline tag。
3. 确认远端实验分支和两个 tag peel 到同一已验证提交。
4. 只执行一次真实 Gate 7A，不换 provider、模型、reasoning、CLI 或 retry 策略。
5. Gate 7A 成功且 transcript 全链复验通过后，才允许条件触发 Gate 7C。

## 7. 禁止事项

- 不删除或改写 R1、R2、R3 的失败证据与 tag；
- 不创建 R4 consumed tag；
- 未经 owner 再次授权，不创建 R4 baseline tag；
- 不换 provider、模型、reasoning 或重试策略；
- 不调用真实 provider；
- 不提交 `.tmp/`、`.local-validation/`、`runs/`、`memory/` 或凭证。
