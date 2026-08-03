# Assurance Stage 1 完成与接力说明

> 日期：2026-07-22
>
> 完成日期：2026-07-23
>
> 分支：`feat/assurance-stage1-contract`
>
> 当前裁决：`completed / merged-to-main / main-ci-passed`

## 一、先看结论

Assurance Stage 1 已完成预注册红灯、独立数据合同、两轮审阅修正和定向验证。它把
Claim、Threat、Evidence、可信上下文与确定性 AdequacyResult 固化为严格、版本化、
fail-closed 的数据合同。

本阶段没有接入 Runtime detector，没有修改 Finish、Goal、`ready_to_commit` 或现有成功语义。
`sufficient_for_merge` 目前只是独立合同层结果，不能直接让 Vega run 成功。

PR `#7` 已完成最终 head 和合并后主线的跨平台 CI，并以 `main@521f9b9` 合并。下文保留
候选阶段的红灯、审阅修正和本地分片历史，避免把合并前的不完整证据改写成事后全绿。

关键提交：

- 基线：`775e1b9`
- 红灯预注册：`75ddc50`
- Stage 1 候选实现：`9a67692`
- 最终 PR head：`428573e`
- 主线合并：`521f9b9`

## 二、实现内容

`src/vega/assurance.py` 新增：

1. 严格 `schema_version: 1` 模型：Claim、Threat、EvidenceRecord、AssuranceBundle、
   AssuranceContext 和 AdequacyResult。
2. 可信上下文独立冻结 accepted Claim、active Threat 和允许的 Evidence 合同 hash，
   防止候选 Bundle 自己声明可信来源。
3. Artifact 只允许当前 run/current iteration 的固定 verification-result 路径，并拒绝
   绝对路径、`..`、空路径段、盘符、URI scheme、NTFS ADS 和真实路径逃逸。
4. 解析 verification artifact v2，重新校验 run、iteration、shell、命令序号、声明命令、
   实际命令、verification 临时目录、结果、失败数、跳过命令和中断状态。
5. Assurance input、单 Evidence 文件和总读取量均有硬预算；读取使用有界流，成功和失败
   读取都缓存，同一真实路径不会被重复读取。
6. 结果独立写入 snapshot、来源集合 hash、Evidence 合同 hash 和输入 hash，并在持久化前
   经过统一脱敏。

## 三、测试与审阅

预注册阶段新增 26 个节点，旧代码结果为：

```text
26 failed
ModuleNotFoundError: No module named 'vega.assurance'
```

首版实现虽然得到 `26 passed`，但独立审阅发现跨 run/iteration 引用、Evidence 语义自报、
LLM 来源伪装、严格类型、读取预算和跨平台 CI 等缺口，因此没有提交首版。

第二轮审阅继续发现并修正：

- `stat` 后输入增长仍可能突破有界读取。
- verification artifact 没有绑定声明命令、实际命令、序号和临时目录。
- 失败的 oversized artifact 会被重复读取。
- 多命令中断的合法 skipped 结构会被误判损坏。
- `PurePosixPath` 会规范化双斜杠，导致空路径段检查失效。

最终 Stage 1 定向结果：

```text
59 passed in 1.24s
600 tests collected
```

静态门禁通过：

- `python -m compileall src scripts/check_repository_hygiene.py`
- `ruff check src tests scripts/check_repository_hygiene.py --no-cache`
- `python scripts/check_repository_hygiene.py --base-ref main`
- `git diff --check`

## 四、候选阶段的本地全量分片历史

审阅最后三项修正前，597 节点的四分片结果为：

```text
595 passed
1 skipped
2 failed
0 timed out
```

两条失败均是既有长路径/并发型测试：

- owner crash recovery 在长 basetemp 下最终进入 `needs_human`。
- dogfood eval 在四路并发下为 `7/8`。

两条随后在短路径、无并发条件下明确通过：

```text
owner crash recovery: 1 passed in 13.73s
dogfood eval: 1 passed in 37.14s
```

这能说明两条失败不是 Assurance 代码回归，但不能替代最新 600 节点全量结论。最新 head 的
完整裁决最终由 PR 和合并后主线 CI 给出，而不是由这次候选阶段分片外推。

唯一 Windows 本地 skip 预计仍是 POSIX shell 变量展开专项，Linux CI 必须实际通过。

## 五、下一步

1. 将 Stage 1 视为已完成的数据合同层，不在后续改动中重复扩大其 schema。
2. 下一阶段只选择数据库 Migration 这一类高价值 Threat 做独立纵向实验。
3. 任何 Stage 2 候选继续保持默认关闭，不注册默认 CLI，不修改现有成功语义。
4. 先关闭 Draft PR `#8` 已记录的审查 findings，再决定是否合入其轻量核心重构和 SQLite
   双生实验。
5. 在真实 detector、Evidence 生产链和跨平台危险/安全案例完成前，不把
   `AdequacyResult` 接入 Finish、Goal 或 `ready_to_commit`。

## 六、剩余边界

- Stage 1 只实现数据合同与离线确定性校验器，不生成真实 Claim/Threat。
- 尚未定义 Runtime detector 的生命周期、并发模型或缓存策略。
- 尚未把 AdequacyResult 接入 Finish/Goal；这必须是后续独立预注册阶段。
- 当前 artifact 可信度仍依赖 Vega run-local 证据链；不声称容器、操作系统或远程证明级隔离。
- 不新增数据库、Web UI、LangGraph、Memory、自动 commit、push 或 release 能力。

## 七、最终合并与 CI 结论

- PR：`#7`。
- 最终 head：`428573efc5e962757b146995fba1f0f47261f0c0`。
- 最终 PR workflow：`29973922619`，10 项检查全部成功。
- 合并提交：`521f9b924241ec258c75b2ecc893bdaa3be91abd`。
- 合并后主线 workflow：`29977016358`，10 项检查全部成功。
- 主线本地复核：Stage 1 `59 passed`，完整收集 `600 tests collected`，仓库卫生与
  `git diff --check` 通过。

最终裁决：

`completed / merged-to-main / main-ci-passed`

该结论只证明 Stage 1 数据合同满足当前仓库的测试和证据门槛，不证明真实模型、生产数据库、
容器隔离或远程证明级安全。
