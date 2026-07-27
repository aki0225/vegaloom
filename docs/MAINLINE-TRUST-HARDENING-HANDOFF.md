# 主线可信执行加固交接

> 日期：2026-07-27
> 分支：`codex/mainline-trust-hardening`
> 基线：`origin/main@7805bba`
> PR：`#20`
> 状态：PR 已进入 Ready for Review。本文不固定易过期的分支 head；每次推送后必须以
> PR `#20` 最新 head 的 CI 为准，不能复用旧提交的成功结果

## 本轮结果

1. 仓库卫生检查同时读取 worktree 与 staged/index candidate。暂存违规内容不能再被
   同名安全工作区文件遮挡，`eval/` 的暂存改写或删除仍按 append-only 规则拒绝。
   本次加固覆盖的 Runtime 与仓库卫生 Git 读取会清除调用方的 `GIT_*` 重定向，避免
   替代 index、仓库或对象库伪造安全候选；不把它表述为仓库内所有脚本的全局保证。
   现有 staged 遮挡测试已包含 alternate-index 回归场景。
2. ignored 证据采用最小两层模型：
   - `ignored_manifest_complete`：路径枚举和稳定元数据是否完整；不完整时 fail-closed。
   - `ignored_content_complete`：内容是否全部读取；敏感内容或预算外内容省略时记录为
     `metadata_bounded`，只说明证据强度，不作为放行字段。
3. content manifest 拒绝 `..`、绝对路径、父目录 symlink 和 Windows junction 逃逸；
   最终 symlink 只读取链接本身，不读取仓库外目标。
4. Review evidence 升级为严格整数 schema v5。Review 与 freshness 共用同一份 schema
   校验，旧 schema 只允许查看，不能继续自动执行。
5. success Loop eval 使用一次联合证据快照同时取得 artifact integrity 与 freshness，
   不再在同一成功判定中重复执行相同验证；非 success 路径仍保留当前工作区重算。
6. CI 的 29 个测试文件全部进入 Linux 分片且无重复；Windows 专项保留完整 containment
   文件。CI 要求 pytest 成功收集并打印实际节点数，不再把会随正常增删测试变化的
   `812` 写成固定授权条件。

## 最后一轮行为合同修复

1. Scope gate 同时检查 staged、unstaged 和 untracked 文件；未跟踪文件不能绕过
   `allowed_paths` 或 `forbidden_paths`。
2. worker 启动前已有 tracked diff 时，仍会独立核对启动前未跟踪文件的完整性；允许
   继续上一轮 tracked diff 不等于允许改写本地未跟踪文件。
3. scope 路径大小写由宿主路径语义决定，仓库本地 `core.ignorecase` 不能扩大或缩小
   allowlist/denylist。
4. Review evidence 的 `changed_files` 必须是严格字符串列表，哈希、Reflect state 和
   当前工作区快照必须一致；重新计算自洽哈希不能伪造文件集合。
5. POSIX 根进程退出后仍监控同一进程组中的后台后代，后代未退出时不能提前宣布 runner
   成功；根进程会先被 `poll()` 回收，避免非 Linux POSIX 因 zombie 根进程误等到超时。
6. 带命名 Windows Job 的 `termination_unconfirmed` 会在 recovery 时重新探测；只有
   Job、owner PID 和 child PID 都确认消失后才允许恢复。owner 已退出但 Job 仍活跃时，
   不再建议写入无人消费的 stop request，而是明确交还人工核对进程树。

## 二次独立审阅后的有界补强

最新 head 全绿后又执行了一轮独立 diff 审阅，只修复能够稳定复现且属于 `M-004` 既定
边界的问题，没有新增 Stage 或实验能力：

1. `verification-result.json` 绑定验证命令结束时的完整工作区 fingerprint；最终
   `ready_to_commit` 要求该 fingerprint 与 reviewer 实际审查的工作区完全一致。
   verification 后即使只修改 allowlist 内文件，也不能复用旧验证结果。
2. `termination_unconfirmed` 从 owned process 结果贯通到 runner、worker 和 reviewer。
   未确认终止时不读取 runner 输出，不继续工作区检查、verification 或采用 reviewer
   verdict。
3. POSIX recovery 使用 child PID 作为 process group ID 探测后台后代；owner、根进程和
   进程组全部退出后才允许重新确认。终态 execution 只检查残留 child/Job/进程组，不会
   因同一 CLI owner 仍存活而阻止正常 continue。
4. `approve` 必须包含非空摘要和至少一个 `checked_item`，且不能同时携带
   `blocker`/`major` finding。
5. 受控 Git 读取禁用 replace objects，文本读取失败时 fail-closed；scope 大小写语义
   改为只读探测目标文件系统，而不是仅依据宿主操作系统默认规则。
6. verification 命令结束后的工作区指纹采集失败时，写出
   `workspace_capture_failed` 结构化结果；auto 与 continue 都保留完整 artifact 和终态
   trace，并暂停为 `needs_human`，不再停留在 `running/verify` 或进入 Reflect/reviewer。

## 首轮新 head CI 反馈

workflow `30272233352` 首轮运行时，静态检查、构建、Windows、POSIX、review evidence、
remaining 和 runtime security 均通过，但 smoke 与 p0 分片暴露两个跨平台问题：

1. Review evidence 把当前 snapshot 的 untracked 文件误算进 tracked `changed_files`
   绑定，导致带本地配置或其他未跟踪文件的 Gate 运行被错误降级。修复后只比较 tracked
   文件集合，untracked 仍由已有独立证据规则 fail-closed。
2. Windows Job recovery 回归漏设平台模拟，Linux 无法进入 Windows 重新确认分支；
   测试已显式固定平台前提。`core.ignorecase` 回归也改为只验证 scope gate 结果，不再
   把后续风险门禁的终态耦合进路径匹配合同。

对应 CI 失败节点在本机修复后重跑为 smoke `5 passed`、p0/review `4 passed`。这仍不能
替代后续最新 head 的完整跨平台 CI。

## 最新增量与裁剪

- README 恢复精确产品边界：独立只读 reviewer 只读取明确编译的任务、规则、tracked diff
  和验证证据，不继承 worker 完整对话，也不宣称操作系统级隔离。
- ROADMAP 把 `M-004` 记录为 2026-07-27 新增的独立维护决策，不再把它包装成原 Assurance
  Stage 的自然延续；M-004 合并后转入真实代码任务能力与成本验证。
- 仓库卫生脚本只为自身 Git 子进程传入目标仓库的 `safe.directory`，不修改用户全局配置。
- 删除锁定函数定义与 import 形状的 AST 测试，保留 finish 决策表，并继续由
  FinishRuntime artifact 与 Goal 篡改拒绝用例覆盖两个 consumer 的行为接线。
- 撤回尚未成熟的 orphan Windows Job 外部接管、ignored 目录折叠和
  `core.ignorecase=false` 覆盖真实文件系统语义的本地尝试；它们没有进入提交候选。

## 本轮明确删除或合并

- 删除 `ignored_paths_complete` 与 `ignored_metadata_complete` 两组重复状态和 issue，
  统一为 `ignored_manifest_complete`。
- `ignored_coverage_level` 只在展示时派生，不写入 Review evidence 作为授权输入。
- 合并 Review/freshness 两套重复 schema 校验。
- 删除仓库卫生中的零对象特殊分支、重复 base-ref 解析和额外 HEAD 读取，保留最外层
  HEAD/index 前后检查。
- 删除重复的 junction 端到端测试、独立 metadata budget 测试和无分支价值的 legacy
  参数组合；仓库卫生测试复用统一 Git 初始化 helper。
- 不新增 Runtime、数据库、Memory、Adapter、Goal、Assurance 或多 Agent 能力。
- `metadata_bounded` 仍只证明有限元数据与清单稳定，不证明能够抵抗恶意本地写者伪造
  同长度内容和时间戳；本 PR 不把它升级成操作系统级文件完整性保证。
- 本 PR 不承诺安全执行恶意仓库自定义的 Git filter、外部 include 或 hook。Vega 的
  reviewer sandbox 和证据边界不等于“打开任意恶意仓库也安全”的 OS 隔离。

## 当前验证证据

### 已通过

```text
本机 Python 3.12.10 collect-only: 832 tests collected
compileall -f: passed
Ruff: passed
git diff --check: passed
repository hygiene against origin/main: passed
architecture growth: passed
  C901 46 -> 39
  Python modules 55 -> 69
architecture against current branch HEAD: 42 passed
```

当前增量的定向验证：

```text
execution control safety: 31 passed, 1 skipped
runtime safety integration: 28 passed, 1 skipped
scope path matching: 52 passed
review fingerprint / approve contract 精确回归: 6 passed
verification 后工作区变化回归: 1 passed
recovery continue 回归: 1 passed
受控 Git replace/text 读取回归: 2 passed
verification 工作区指纹采集失败（auto/continue）: 2 passed
architecture growth 单元合同与 self-HEAD: 42 passed
```

代码提交：`d5091e0`（`fix: complete mainline trust hardening`）。

### 本机未形成全量通过结论

Windows 本机运行整个 `tests/test_repository_hygiene.py` 两次均超过 60 秒；一次使用全局
Python，一次使用项目 `.venv`。相关进程已按本轮命令范围清理，这两次运行均记为
**未通过**。关闭第三方 pytest 插件和 cacheprovider 后，受影响的单节点与邻接节点均
快速通过；该模式会因为项目仍声明 `cache_dir` 产生一个预期的 pytest 配置 warning。

本轮还确认 pytest 9 的 cacheprovider 会在本机 session finish 阶段卡在
`tempfile.mkdtemp`；测试节点已显示通过也不能替代最终汇总。关闭 cacheprovider 后，
execution control、runtime safety、scope 与精确回归均得到正常退出和明确计数。
`tests/test_assurance_verification_semantics.py` 与 `tests/test_review_artifact_integrity.py`
整文件运行仍超过 60 秒，因此只记录上面的精确节点结果，不把超时当成通过。

因此本机不把上述超时命令记为全量通过。PR `#20` 的每个新 head 仍必须重新完成：

- Python 3.11 全量测试。
- Python 3.12 collect 守卫与 29 文件分片。
- Windows 专项测试。
- 仓库卫生、Ruff、compileall、架构增长和构建检查。

## 另一台机器接力

```powershell
git fetch origin --prune
git switch codex/mainline-trust-hardening
git pull --ff-only origin codex/mainline-trust-hardening
git status -sb
```

1. 先核对 PR `#20` 最新 head 的全部 CI job，不要新建分支。
2. CI 失败时保留日志，只修与本 PR 直接相关的失败，不再扩展新信任模型。
3. CI 全绿后做一次独立 PR diff 与公开仓库卫生复核。
4. 最新 head 的 CI 和独立 diff 复核均通过后，由维护者决定是否合并；不要复用旧 head
   的成功结果。
5. 合并后停止横向基础设施扩张，转入真实代码任务的成功率、耗时、token 成本、人工接管
   和恢复体验验证。
6. MA 与 Assurance 实验继续留在独立实验分支，不带入本 PR。
