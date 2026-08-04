# Core Real-World Pilot v1 接力说明

> 日期：2026-08-04
>
> 分支：`main`
>
> 远端续做入口：`origin/main`

## 2026-08-04 接力

### 当前结论

CRWP-V1 已取得预注册合同允许的全部终态，不再运行或修改 Runtime：

| Case | 终态 | 说明 |
|---|---|---|
| `CRWP-V1-01` | `needs_human / workspace_check_failed` | Worker 产生未登记缓存且候选超过 diff 预算，保留现场，不重跑。 |
| `CRWP-V1-02` | `needs_human / timed_out` | Worker 达到冻结的 `900s` timeout，未修改文件，后续验证与 Reviewer 未启动。 |
| `CRWP-V1-03` | `eligibility-changed-before-run` | 冻结后出现关联 PR，按资格合同停止，不启动 Worker。 |

这些结果不合并计算成功率，也不把 timeout 解释成模型修复失败或成功。后续禁止为得到更好
结果而选择性重跑、延长 timeout、更换模型或改写历史记录。新的公开运行记录已经只追加到
[`../eval/real-world-runs.md`](../eval/real-world-runs.md)。

`v0.1.4` 的 annotated Tag 与 GitHub Release 已发布。精确 Tag
`v0.1.4@289a1ad0431e0aaa2e74768c517058e62a33fdbf` 的 fresh JSONL smoke 已完成，
因此发布阶段与 CRWP-V1 阶段都可以标记为完成。当前唯一产品动作改为调查现有宿主会话入口，
固定 Plan-first 与人工确认协议；暂不修改 Finish、Runtime 或成功条件。

### `v0.1.4` 精确 Tag smoke

- Run：`20260804-093946-135999-feature-loop`。
- Worker JSONL `20` 行、Reviewer JSONL `4` 行，全部可解析，两个角色各有一条最终
  `agent_message`；两个 stderr 文件为空。
- 终端共 `25` 条固定安全进度，不包含目标路径或 Codex 命令。
- 目标只修改 `README.md`，新增 `1` 行；`python -m pytest -q` 报告 `1 passed`。
- Reviewer 返回 `approve`、findings 为 `0`；Finish 为 `ready_to_commit`。
- artifact integrity 为 valid，evidence freshness 为 fresh；`54` 个 run 文件的高置信
  凭据模式扫描为 `0`，登记的 `4` 个进程均已退出。
- 本机审计摘要位于 Tag worktree 的 `.tmp/v014-smoke-audit.json`，继续保持 ignored，
  不进入 Git。

### Case 02 正式运行

正式控制登记由以下两个主线提交固定：

- `77192aa`：重新冻结 Case 02 控制合同；
- `f748a2f`：登记 Case 02 运行时和控制 manifest。

控制 manifest SHA-256：

```text
9f24c2f352f12c64f5920131a3ba1ba67686209f2cb38dd74df77f75b44a7902
```

正式运行前：

- 资格证据只允许 `active_case_ids == ["CRWP-V1-02"]`；
- fresh baseline 为 `accepted=true`；
- 两次 oracle 输出一致，SHA-256 为
  `0ee06abd2c7451e416e7514f49ada0f7ff1017a14c6a94dde27d6db35464626b`；
- `945` 个 tracked 文件、`9,657,058` bytes 的调用前扫描命中 `0` 个冻结负向词；
- 最终 Worker prompt 再次扫描后命中仍为 `0`。

正式 Run 为 `20260804-130626-039900-bug-loop`：

- Worker 使用 `gpt-5.4 / medium`，达到冻结的 `900s` timeout；
- termination 已确认，未达到 `1800s` 总墙钟停止线；
- 已记录 stdout 为 `49` 行可解析 JSONL，没有最终 `agent_message`；最后可见事件仍在调查和
  设计 SQLite `CREATE TABLE` 解析方案；
- execution 记录同时注明输出读取线程关闭超时，因此不能把现有文件描述为外部进程全部输出；
- Worker 未修改任何文件，目标 Git 保持 clean、remote 为空；
- Workspace Gate、Verification、Reflect、Risk Gate 和 Reviewer 均未启动；
- Finish 为 `needs_human`，artifact integrity 为 valid，evidence freshness 为 false，
  原因是 `trusted_review_missing`；
- 目标相关进程均已退出。

正式现场继续保存在
`.local-validation/crwp-v1/formal-runs/20260804-130323-crwp-v1-02/` 和
`runs/20260804-130626-039900-bug-loop/`，不得删除、修改或提交其中的本机产物。

### 验证边界

- 新增控制测试：`44 passed`。
- compileall、Ruff、仓库卫生、架构增长和 `git diff --check` 均通过。
- 远端 CI `#202`（workflow run `30878642623`）全部成功，覆盖 Python 3.11、Python 3.12
  四个分片、Windows、POSIX 和 wheel。
- 本机不能声称完整 `python -m pytest` 通过：直接运行超过 `60` 分钟后被停止；四分片并行
  因资源竞争触发 `58s` timeout；后续串行 Git-heavy 测试仍触发同一单测上限。残留进程已
  精确停止，这些不完整运行不作为通过证据。

### 下一步

1. 先只读核对 Codex Skill、Claude Code assist 说明和现有 Plan 入口。
2. 形成固定的调查、事实/假设分离、Plan 与人工确认协议。
3. 先把协议和最小模板交给人工审查；本阶段不新增 Planner Agent、命令、状态或 schema。
4. 协议确认后再使用短生命周期分支实现，不与 Finish 报告改动混在同一个 PR。

下方 2026-07-30 及更早章节只作为历史接力记录保留，其中“下一步”已经被本节替代。

## 2026-07-30 接力

### 本轮完成

本轮只整理主线既有可信性补丁，没有开始 Plan-first、Important Diff、Claude Code Runner，
也没有启动 CRWP-V1 Worker。

- Brief 与 Reflect 在同一次读取事务中复用固定 Git revision 和 ProjectKnowledge，避免
  多次 `rev-parse HEAD` 之间发生上下文漂移。
- 已解析 revision 绑定仓库根和进程内 proof，拒绝跨仓库复用及手工伪造。
- tracked project context 只接受由受信 loader 生成、且绑定相同 revision 的预加载知识；
  来源证明使用 Pydantic private attributes，不进入公开 JSON 或 Markdown。
- Gate 复用经过 freshness 校验的 Reflect `changed_files`，不再回退到新的
  `git diff --name-only` 事实源。
- Gate 与 Reflect 仍分别执行 staged/unstaged `git diff --check`，避免 `MM` 文件的净差异
  抵消 index 中问题。
- staged rename 的源路径继续参与 `risk.required_reviews`，不能通过移出高风险目录绕过披露。
- Git 读取使用空 `core.fsmonitor=`，兼容会把字符串 `false` 解释成 hook 路径的旧版 Git。
- 删除 Reflect 重复的 status/stat/name-only 读取；完整 diff、变更文件和指纹继续以 review
  workspace snapshot 为事实源。

架构增长门禁已恢复：

```text
架构增长门禁通过：C901 38->38，Python 模块 76->76
```

### 可信 pytest 终态（2026-07-30 后续补充）

当前 `main` 的 `a8a58cb` 已取得完整、可正常退出的本机测试终态。此前把 60 秒外层
观察窗口内尚未输出最终汇总的情况误判为 pytest 退出异常；本次按 CI 的 Python 3.12
分片边界执行，并为每个测试使用 `58` 秒 timeout。

```json
{
  "collected": 908,
  "passed": 900,
  "failed": 0,
  "errors": 0,
  "skipped": 8
}
```

- 六个分片均以退出码 `0` 正常结束，JUnit 结果只保存在被忽略的
  `.tmp/pytest/diagnostics/`。
- 最慢单项为 `25.22` 秒，仍低于 `58` 秒上限。
- 本机共享 pytest cache 目录存在 ACL 警告；本次每个分片使用独立 cache，因此该本机
  卫生问题没有污染测试结论，也不应通过修改产品代码掩盖。
- 这只解除主仓库验证前置条件；正式 CRWP-V1 Worker、Reviewer 和 Finish 仍然都没有启动。

### Case 01/02 预检终态（2026-07-30）

本轮只完成预注册已有前置检查，没有启动正式 Worker、Reviewer 或 Finish，也没有新增
Runtime、测试框架或证据格式。

- 两个活跃目标副本均从冻结 SHA 重建，关闭 `core.autocrlf`，移除 remote，并只提交
  `.vega.yaml`：
  - Case 01 prepared HEAD：`3cde5c71416275f573bb7d6b8823464014f9d3df`
  - Case 02 prepared HEAD：`fde6ef505a84aef2a5377ac6f27f253b3a453d0b`
- 两个副本的 workspace snapshot 均为 `capture_complete=true`、
  `ignored_manifest_complete=true`、`git_control_complete=true`，且 tracked changes 和
  untracked files 都为 `0`。`ignored_content_complete=false` 仍是既有有界内容覆盖语义。
- Case 01 的定向 build、`59 passed`、typecheck、Biome、`git diff --check` 均通过；
  oracle 两次均以 `1` 退出，stdout SHA-256 都是
  `4308870971d831b3f42883a42ea06057da9267782574b278a527f614172095e1`，stderr 为空。
- Case 02 的 workspace build、`41 passing`、ESLint、`git diff --check` 均通过；
  oracle 两次均以 `1` 退出，stdout SHA-256 都是
  `0ee06abd2c7451e416e7514f49ada0f7ff1017a14c6a94dde27d6db35464626b`，stderr 为空。
- 资格复核没有变化：Dormice Issue `#33` 仍为 open、无 assignee、无评论，未发现关联
  修复 PR；Sequelize Issue `#18265` 仍为 open、无 assignee、无评论，受控公开 PR
  `#18274` 仍保持登记的 base、head 和 `6934` 字节 diff 哈希。
- Sequelize 冻结目标的 tracked 输入面未命中第十节四个负向词条。正式 Worker 和 Reviewer
  启动前仍必须扫描当次最终编译输入；命中即停止，不得调用外部 runner。
- `codex-cli 0.145.0` 使用 read-only、ephemeral 的 `gpt-5.4` 短探测返回 `READY`，
  退出码为 `0`。两个目标都没有遗留相关进程。

当前可以按预注册顺序进入 Case 01 的正式 Worker。不要再次重跑已经通过的 setup 或
baseline；Case 01 取得终态后，再决定是否继续 Case 02。

### Case 01 正式运行结果（2026-07-30）

Case 01 已形成终态，正式 run 为 `20260730-223403-019133-bug-loop`。

- `gpt-5.4 / medium` Worker 正常退出，耗时约 `464` 秒，runner 报告
  `58,637` tokens。
- Worker 只修改两个允许文件，但新增未跟踪缓存
  `.pnpm-store/v11/index.db`。Workspace Gate 在 verification 和 reviewer 前停止，
  最终状态为 `needs_human / workspace_check_failed`。
- 候选 diff 为 `344` 行新增、`210` 行删除，共 `554` 行，已经超过预注册的
  `max_diff_lines=350`。其中 `main.ts` 被整体重排为可导入的 `createProgram()`，
  不属于本题期望的小范围参数解析修复。
- 因为清理缓存后仍会被 diff 预算拒绝，本轮不删除现场、不继续同一 run，也不为得到更好
  结果而重跑 Case 01。
- Worker 最终输入在 `worker_started` 前已写入两份相同 artifact，SHA-256 为
  `bc045e73d25a921258dc952aec8fa85ca0e68d06ab567575e77cf030cb1586b9`；但控制端在外部
  runner 启动后才读取并登记该哈希，因此本次存在输入哈希登记时序偏差，不能描述为完全无
  协议偏差的样本。
- 没有启动 verification、Reflect、Reviewer 或 Finish，没有自动 commit、push、release
  或写入长期 Memory，目标相关残留进程为 `0`。

该结果证明 Vega 会在 Worker 产生未登记文件时按设计 fail-closed，但不能证明候选修复正确，
也不能算完成 Coding Loop。Case 02 尚未启动；下一步只评估如何在不增加 Runtime 机制的前提下，
在调用前完成其既定负向输入扫描。

### 已取得终态的静态验证

```text
python -m compileall -q src scripts/check_repository_hygiene.py scripts/check_architecture_growth.py
python scripts/check_repository_hygiene.py --base-ref origin/main
python scripts/check_architecture_growth.py --base-ref origin/main
ruff check src tests scripts/check_repository_hygiene.py scripts/check_architecture_growth.py
git diff --check
```

以上命令均通过。

此前受影响测试曾单独取得 `171 passed` 的明确终态：

```text
tests/test_security_evidence.py + tests/test_context_boundaries.py：80 passed
tests/test_evidence_freshness.py：27 passed
tests/test_required_risk_reviews.py：10 passed
tests/test_p0_regressions.py：54 passed
```

其中 `tests/test_evidence_freshness.py` 用时约 12 分钟，
`tests/test_p0_regressions.py` 用时约 17 分钟。测试较慢来自既有 Goal、Scope、Finish
重算链路，不是本轮命令失联。

### 已解除的历史验证阻断

完整测试此前在最终汇总前被外层会话截断，不能作为通过证据。上方 `908` 节点的六分片
结果替代该阻断，后续不得再把旧的部分 JUnit 或历史 `896` 节点统计当作当前 `a8a58cb`
的测试结论。

### 下一步固定顺序

```powershell
git fetch origin
git switch main
git pull --ff-only origin main
git status -sb
Get-Content docs/CORE-REAL-WORLD-PILOT-V1-HANDOFF.md -TotalCount 120
```

1. 从冻结 SHA 重建 CRWP-V1 Case 01/02 目标副本，只写入对应 `.vega.yaml`。
2. 重新登记 prepared HEAD、tree、配置摘要、非 oracle baseline、双次 oracle、Issue/PR
   资格和 Provider 探测。
3. Case 03 继续固定为 `eligibility-changed-before-run`，不得启动 Worker。
4. 只有控制哈希、资格、workspace baseline、Provider 和本节的可信 pytest 终态同时满足，
   才能按预注册顺序启动 Case 01/02 的正式 Worker。
5. 不为 Pilot 新增 Runtime、状态或 Artifact；在至少一次合同允许的真实终态前，不实现
   Loop 内部 Plan-first 与 Important Diff。

## 2026-07-29 接力

### 当前状态

- 本轮 oracle 修订基于主线可信执行提交：
  `8f91844`。
- `52a8b3d test: finalize CRWP-V1 oracle contracts` 已在本地提交三份 oracle、Control
  Amendment 与证据口径修正。本次接力提交推送后，远端 `main` 将包含该提交和本文件的更新。
- PR `#22` 已于 2026-07-28 合并，标题为
  `fix: bound ignored inventory and fail closed on timeout`。
- 主线随后新增可配置的 `risk.required_reviews` 必审高风险披露，并完成 Goal/Gate 防篡改、
  人工接管新鲜度和关键行号修复。当前快照已取得 `896 collected / 888 passed /
  8 skipped` 的全量 pytest 终态证据。
- 正式 CRWP-V1 Worker、Reviewer 和 Finish 仍然都没有启动。
- 三份 oracle 合同已经补齐，并由本文件所在提交共同冻结：
  - `scripts/pilot/crwp-v1/crwp-v1-01-timeout-oracle.py`
  - `scripts/pilot/crwp-v1/crwp-v1-02-sqlite-autoincrement-oracle.cjs`
  - `scripts/pilot/crwp-v1/crwp-v1-03-stub-filename-oracle.py`
- 三个项目内目标副本位于 `.tmp/crwp-v1/targets/`，都停在预注册的 upstream SHA，
  工作区干净且当前没有 `.vega.yaml`。
- `.local-validation/crwp-v1/control-evidence/` 保留本机基线和 oracle 原始输出，继续保持
  ignored，不进入 Git。
- 三份 oracle 已取得双次稳定 `exit=1` 证据；OpenStates 因 PR `#125` 固定停止为
  `eligibility-changed-before-run`。
- 本轮重新收集到 `896` 个 pytest node，但完整 pytest 在 60 秒外层上限内没有正常退出，
  因此不能记为新的全量通过。临时安全目录配置后，焦点集 JUnit 记录 `67 passed / 0 failed`，
  但 runner 同样未退出；该结果只说明测试主体已执行，不构成通过证据。
- 继续 CRWP 前必须先定位 pytest runner 未正常终止的原因，并取得带明确
  `passed / failed / skipped` 计数的正常退出终态。该问题不通过放宽超时、强制成功退出或忽略
  残留进程解决。

### Runtime 阻断已经解除

PR `#22` 已解决此前的两个 P0：

1. 大型 ignored 目录改为有界、可解释的 inventory，不再逐文件无界枚举依赖目录。
2. Git 或工作区读取 timeout 会进入结构化 fail-closed 终态，不再留下
   `running / current_step=worker` 的半完成现场。

合并后使用当前 `main` 对三个现有目标副本重新执行 `snapshot_workspace()`：

| Case | `capture_complete` | `ignored_manifest_complete` | `ignored_content_complete` | 秒 |
|---|---:|---:|---:|---:|
| Dormice | `true` | `true` | `false` | `0.265` |
| Sequelize | `true` | `true` | `false` | `0.242` |
| OpenStates | `true` | `true` | `false` | `0.209` |

`ignored_content_complete=false` 是当前 `metadata_bounded` 语义的一部分：依赖目录根和高价值
ignored 文件仍受控，但不能把结果描述成“完整扫描了依赖目录内的每个文件”。

### Oracle 修订进度

#### `CRWP-V1-01` Dormice

默认 timeout 分支已经补充以下断言：

- 请求路径为 `/execCommand`；
- Authorization 为 `Bearer oracle-token`；
- body 的 `name=oracle-timeout`；
- body 的 `command=echo oracle`；
- `timeoutSeconds=300`。

连续两次基线均稳定以 `1` 退出，输出 SHA-256 均为：

```text
4308870971d831b3f42883a42ea06057da9267782574b278a527f614172095e1
```

Git 暂存区中的 LF 字节 SHA-256（提交后的权威控制哈希）：

```text
a1a9152a9d96f0ac935f6c26baccba7ce4632453b388ba570ceb62731ae65b5f
```

当前 Windows 工作树因 CRLF checkout 得到
`5af44f7ec73328d373c791b4b042c5465ceeb754e2d2bf4f1aef45d17328cf76`；该值只用于
本机行尾诊断，不替代 Git index 控制哈希。

#### `CRWP-V1-02` Sequelize

行注释负对照已经只在 `describeTable('line_comment_pk')` 期间临时替换
`queryInterface.showConstraints()` 为空结果，并在 `finally` 恢复。真实
`PRAGMA TABLE_INFO`、AUTOINCREMENT metadata 路径和块注释正常路径均未替换。

两次独立基线均以 `1` 退出，输出 SHA-256 均为：

```text
0ee06abd2c7451e416e7514f49ada0f7ff1017a14c6a94dde27d6db35464626b
```

两次均确认行注释与块注释真实保留，且没有把普通主键误标为 auto-increment。当前失败只剩
目标 Issue 的正向语义。

Git 暂存区中的 LF 字节 SHA-256（提交后的权威控制哈希）：

```text
f784abc3518e12991f3f0b93628773adda1d68c9add4fe2a75d9e93b318e93d0
```

当前 Windows 工作树因 CRLF checkout 得到
`61c85e423715ad748b3adabef6b9cd9718b51fb1a4aa31b416b981885479c294`；该值只用于
本机行尾诊断。

#### `CRWP-V1-03` OpenStates

幂等用例已经补充：

- 首次运行真实生成一份 Division YAML 和一份 Jurisdiction YAML；
- 两份文件都位于预期输出目录并进入 manifest；
- Division `ocdid=ocd-division/country:us/state:wa`；
- Jurisdiction `ocdid=ocd-jurisdiction/country:us/state:wa/government`；
- 第二次运行全部 skip、writer 调用为 `0`、manifest 不变。

连续两次基线均稳定以 `1` 退出，输出 SHA-256 均为：

```text
e3cf488350744510cbe084c452e4dc4ce4a314724ea0d02910837084c6675f21
```

Git 暂存区中的 LF 字节 SHA-256（提交后的权威控制哈希）：

```text
79fd7227f44e1cf1aaff40d9f02b9d19a96834b14aa858de6c507503208ace0f
```

当前 Windows 工作树因 CRLF checkout 得到
`596145bfa64a84ae98bf63f6b96401e027d145a50aafc89410f0be68954f7e02`；该值只用于
本机行尾诊断。Case 本身另有资格变化。

### 新发现的资格变化

`openstates/jurisdictions` PR `#125`：

- 创建时间：`2026-07-28T07:59:23Z`；
- 状态：open、非 draft；
- 标题：`Fix ancestor stub filenames`；
- 明确 `Closes #122`；
- base：预注册冻结 SHA
  `6fbe7d6aed32c3b781490c8e4c5a737bdd6e4705`；
- head：`89e39fe09d952e92708a3a10f898a79650fd6a22`。

它晚于资格快照截止时间 `2026-07-28T04:07:36Z`，因此
`CRWP-V1-03` 已触发 `eligibility-changed-before-run`。下一会话不得按原 Issue 引导样本
直接启动 Worker。可选动作只有：

1. 保留原预注册并把该 Case 记录为运行前资格变化后停止；
2. 另建新的预注册版本，把它明确改为 controlled public replay。

不能在同一登记中静默改分类或把新 PR 内容加入模型输入。

### 下一会话固定顺序

1. 先定位 pytest runner 在测试主体完成后未正常终止的原因；保持当前 60 秒上限，不把
   JUnit 单独生成当作测试通过。
2. 取得一次完整 pytest 的正常退出和明确计数后，复核本文件中的 `896 collected / 888 passed /
   8 skipped` 历史基线是否仍适用。
3. OpenStates 固定记录为 `eligibility-changed-before-run`，不运行 Case 03。
4. 从冻结 SHA 重新准备 Case 01/02 目标副本，只提交 `.vega.yaml`，重新登记 prepared HEAD、tree 和
   config hash。
5. 重跑 config check、非 oracle 基线、双次 oracle、Issue/PR 资格和 Provider 探测。
6. 只有控制哈希、资格、workspace baseline、Provider 和可信 pytest 终态全部满足合同，才按固定顺序启动
   Dormice 和 Sequelize。每项最多两轮、总墙钟 30 分钟，不挑结果重跑。
7. 将真实结果只追加到 `eval/real-world-runs.md`，最后再执行 Vega 主仓库验证。

### 续做命令

如果 Git 报告目录所有者与当前用户不一致，不要改全局 `safe.directory`。先设置当前仓库
变量，再只对本次命令传入安全目录：

```powershell
$repoRoot = (Get-Location).Path
$repoRootPosix = $repoRoot.Replace("\", "/")
git -c "safe.directory=$repoRootPosix" status --short --branch
```

进入现场：

```powershell
$repoRoot = "<vegaloom-repository>"
Set-Location $repoRoot
$repoRootPosix = (Get-Location).Path.Replace("\", "/")
git -c "safe.directory=$repoRootPosix" fetch origin --prune
git -c "safe.directory=$repoRootPosix" pull --ff-only origin main
git -c "safe.directory=$repoRootPosix" status --short --branch
Get-Content docs/CORE-REAL-WORLD-PILOT-V1-HANDOFF.md
```

先检查 pytest runner 是否有遗留进程，再从测试终态诊断继续。正式 Worker、Reviewer 和 Finish
仍未启动；在可信 pytest 终态与 Case 01/02 全量 preflight 同时满足前，不得启动它们。

## 上一阶段结论（保留作历史）

CRWP-V1 的预注册、两次 Amendment、四个控制脚本和执行登记草案已经整理并可从远端接续。
正式 Worker **没有启动**，三个 Case 也没有产生 Reviewer 或 Finish 结果。

当前有两个独立阻断，不能只修其中一个：

1. 控制 oracle 登记审查发现三个合同覆盖缺口；
2. Vega Runtime 无法为带完整依赖目录的目标仓库建立可信 workspace baseline。

Runtime 阻断：

- Dormice 的 ignored 枚举超过 Runtime 固定的 30 秒 Git 读取上限；
- Sequelize 与 OpenStates 的 ignored 文件数超过 4096 个元数据条目预算；
- Dormice 的 `subprocess.TimeoutExpired` 还会直接向上传播，可能留下
  `running / current_step=worker` 的半完成 run。

控制 oracle 阻断：

- Dormice 默认 timeout 场景还要校验 Authorization、name 和 command；
- Sequelize 还要加入真实 SQL 行注释与块注释负对照；
- OpenStates 还要证明首次幂等运行真实生成预期 division/jurisdiction YAML 和 `ocdid`。

因此不能靠删除依赖、提高条目上限、沿用当前 oracle 哈希或放宽 fail-closed 语义继续实验。

## 本次接力提交包含

- `docs/CORE-REAL-WORLD-PILOT-V1-PREREGISTRATION.md`
- `docs/CORE-REAL-WORLD-PILOT-V1-RUN-REGISTRATION.md`
- `eval/real-world-runs.md` 中 2026-07-28 的追加记录
- `scripts/pilot/crwp-v1/` 下四个控制文件

`.local-validation/` 和 `.tmp/` 仍是本机 ignored 证据，不会推送到公开仓库。它们包含目标
副本、原始命令输出和本机路径信息，不能直接加入 Git。后续正式运行本来就必须从冻结 SHA
重新创建三个目标副本，因此晚间续做不依赖复制这些本机目录。

## 验证现状

已通过：

```powershell
python -m compileall src scripts/check_repository_hygiene.py
python scripts/check_repository_hygiene.py --base-ref origin/main
ruff check src tests scripts/check_repository_hygiene.py
ruff check scripts/pilot/crwp-v1
node --check scripts/pilot/crwp-v1/crwp-v1-02-sqlite-autoincrement-oracle.cjs
node --check scripts/pilot/crwp-v1/ignore-native-drivers.cjs
git diff --check
```

三个当前版本 oracle 都连续复现两次，非 oracle 命令全部通过；但 oracle 代码审查发现上述
三个覆盖缺口，因此双次结果只属于待修订控制基线，不属于最终冻结证据。

`python -m pytest` 尚无可信全量终态。本机为 Python 3.14.3 + pytest 9.0.2；仓库 CI 使用
Python 3.11/3.12。定向诊断确认 `tests/test_assurance_verification_semantics.py` 是重型集成
测试集合，共 18 个 node，单文件预计需要 40 至 50 分钟；此前 900 秒停止不是
cacheprovider 挂死。不要把部分节点结果写成全量通过。

## 晚间续做

```powershell
git fetch origin
git switch main
git pull --ff-only origin main
git status -sb
Get-Content docs/CORE-REAL-WORLD-PILOT-V1-HANDOFF.md
```

先补控制合同，不启动正式 Worker：

1. 修复三个 oracle 覆盖缺口；
2. 对每个变更后的 oracle 运行语法/静态检查和双次缺陷基线；
3. 更新控制文件哈希、执行登记、本地机器证据和 Control Amendment。

然后做 Vega 主线修复：

1. 为 `node_modules`、`.venv` 等大型依赖目录定义有界、可解释的 ignored inventory。
2. 保留对高价值 ignored 文件新增、修改和删除的检测能力。
3. 将 ignored 枚举 timeout 转成结构化、可恢复的 `needs_human` 终态。
4. 补 Node/Yarn/Python 大依赖目录回归测试，证明 worker 前后使用同一套 workspace 语义。
5. 修复通过后追加 Runtime Amendment，再从三个冻结 SHA 重建目标副本。

OpenStates 的生产文件路径会命中 `migration` 高风险门禁，后续即使 workspace 阻断解除，也应
按当前 fail-closed 语义转人工，不能为了让三个样本都进入 Reviewer 而放宽门禁。

## 不要做

- 不复用当前办公室机器上的目标副本作为正式运行输入；
- 不把 `config check` 或控制基线写成 Pilot 已执行；
- 不提交 `.local-validation/`、`.tmp/`、`.env`、数据库、Office 文件或本机绝对路径；
- 不自动 commit、push、release 或启动正式 Worker。
