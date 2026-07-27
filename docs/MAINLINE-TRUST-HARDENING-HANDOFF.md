# 主线可信执行加固交接

> 日期：2026-07-27
> 分支：`codex/mainline-trust-hardening`
> 基线：`origin/main@7805bba`
> 远端起点：`2ba6ab8`
> Draft PR：`#20`
> 状态：本轮实现与裁剪完成；当前分支是唯一接力入口，等待 Draft PR CI，不合并

## 本轮结果

1. 仓库卫生检查同时读取 worktree 与 staged/index candidate。暂存违规内容不能再被
   同名安全工作区文件遮挡，`eval/` 的暂存改写或删除仍按 append-only 规则拒绝。
   所有 Git 读取会清除调用方的 `GIT_*` 重定向，避免替代 index、仓库或对象库伪造
   安全候选；现有 staged 遮挡测试已包含 alternate-index 回归场景。
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
   文件。当前收集节点数更新为 `812`。

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

## 当前验证证据

### 已通过

```text
Python 3.14.3 collect-only: 812 tests collected
compileall -f: passed
Ruff 0.15.20: passed
git diff --check: passed
repository hygiene against origin/main: passed
architecture growth: passed
  C901 46 -> 40
  Python modules 55 -> 69
architecture against current branch HEAD: passed
  C901 41 -> 40
  Python modules 68 -> 69
```

当前补丁的定向验证：

```text
workspace manifest containment: 11 passed
workspace snapshot budget: 16 passed
新增 freshness 节点: 3 passed
新增 Review evidence 节点: 10 passed
新增 workspace manifest 节点: 2 passed
repository hygiene: 22 passed
alternate-index staged 遮挡回归: 1 passed
```

### 本机未形成全量通过结论

2026-07-27 的 Windows 全量运行先完成 `152` 个节点，随后
`test_zero_selected_verification_commands_cannot_auto_succeed` 在 Git 子进程读取期间
触发 58 秒 pytest 上限。其他完整 Loop 节点也多次出现相同现象。
一次临时小仓库的普通 `git status --short` 还触发了 Vega 内置 30 秒 Git 超时；同一时段
测得 Git 进程启动存在明显抖动。相关失败堆栈停在 Git 子进程读取，不是测试断言失败，
但这些运行仍按 fail-closed 记录为**未通过**，不能写成全量绿色。

因此合并证据必须等待 Draft PR 的 CI：

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

1. 先核对 Draft PR `#20` 的全部 CI job，不要新建分支，也不要合并。
2. CI 失败时保留日志，在同一 `codex/mainline-trust-hardening` 分支修复并推送。
3. CI 全绿后再做一次 PR diff 与公开仓库卫生复核，再判断是否具备合并条件。
4. MA 与 Assurance 实验继续留在独立实验分支，不带入本 PR。
