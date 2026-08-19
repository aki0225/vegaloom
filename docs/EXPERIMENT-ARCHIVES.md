# 历史实验归档

本文记录已经结束、不会作为当前产品入口的公开实验。归档 Tag 保留实验分支的完整
提交树和验证材料；它们不是版本发布 Tag，也不代表这些能力已经进入 `main`。

## 当前归档

| 实验 | 归档 Tag | 原分支 | 冻结提交 | 当前结论 |
|---|---|---|---|---|
| Goal Handoff 公开集成候选 | `archive/goal-handoff-integration-20260723` | `experiment/goal-handoff-integration` | `d3b6ed7` | 能力候选已被当前 Supervisor Agent 的 Task Card、Resume Capsule 和 Handoff 门禁取代；保留实验实现与公开 CI 记录供复核 |
| MA-2B 与日用价值实验 | `archive/ma2b-pilot-next-20260731` | `experiment/ma2b-pilot-next` | `9f5026c` | 保留多代理、日用价值和失败结果的冻结输入与证据；不接入当前默认 Runtime |
| LangGraph 编排对照实验 | `archive/langgraph-comparison-20260723` | `experiment/langgraph-comparison` | `448dedd` | 保留多阶段编排、恢复和拓扑实验材料；不等同于当前主线控制面 |
| Selective Memory Phase 1–2 | `archive/selective-memory-phase1-2-20260722` | `experiment/selective-memory-archive` | `d48f92e` | 保留数据集、指标、评估器和安全边界结论；不作为当前长期 Memory 实现 |
| SAG3B-03 machine A Handoff WIP | `archive/sag3b-03-wip-20260816` | `codex/sag3b-03-wip` | `065a423` | 保留未完成的 Git Task Card、允许范围 WIP 和 historical `not_run` Gate；Windows 短时文件锁修复已由主线提交 `012700b` 重新实现并验证，不从旧 WIP 继续开发 |

Tag 的完整提交号可用以下命令核对：

```powershell
git fetch --tags origin
git rev-parse archive/goal-handoff-integration-20260723^{}
git rev-parse archive/ma2b-pilot-next-20260731^{}
git rev-parse archive/langgraph-comparison-20260723^{}
git rev-parse archive/selective-memory-phase1-2-20260722^{}
git rev-parse archive/sag3b-03-wip-20260816^{}
```

输出应分别对应表中的冻结提交。Tag 一旦发布不得移动；如果需要修订实验材料，应创建
新的带后缀 Tag（例如 `-r2`），不能覆盖原始归档。

```text
archive/goal-handoff-integration-20260723 -> d3b6ed74a185a45f1d7b1d30aa7e023631b505f3
archive/ma2b-pilot-next-20260731          -> 9f5026c12dbbc32c3a569828f95c25d0c372cd97
archive/langgraph-comparison-20260723     -> 448dedd687b166bffa65b9926be8565367935df8
archive/selective-memory-phase1-2-20260722 -> d48f92ed3baebda946a691abc91fb7764c58fe34
archive/sag3b-03-wip-20260816             -> 065a42338da5956d410b632dc0c89f9cbdd05a07
```

## 如何复核

归档只读复核：

```powershell
git fetch --tags origin
git switch --detach archive/goal-handoff-integration-20260723
```

完成复核后返回主线：

```powershell
git switch main
```

归档分支不作为新功能开发基线。若实验结论需要重新验证，应从当前 `main` 重新制定计划，
并把归档 Tag 作为历史输入，而不是继续在旧实验树上追加提交。

## 分支维护规则

1. 正在进行的实现使用短生命周期分支，并通过 PR 合入 `main`。
2. 已完成实验使用不可移动的 `archive/*` Tag 保存，不长期占用远端分支名。
3. 临时跨机器接力分支在最终证据进入主线后删除；必要的 Task Card 或结论先转入
   主线文档或正式实验记录。
4. `v*` Tag 专用于版本发布，不能把实验归档 Tag 当作发布版本。
5. 归档材料只能解释历史结果，不能覆盖当前产品契约、验证命令、风险门禁或成功语义。
