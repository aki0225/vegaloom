# 主线可信执行加固交接

> 日期：2026-07-27
> 分支：`codex/mainline-trust-hardening`
> 基线：`origin/main@7805bba`
> 已验证远端 head：`7a35474`
> Draft PR：`#20`
> 状态：`7a35474` 的 CI run `30256075361` 已 10/10 success；当前增量推送后必须等待
> 新 head CI，不直接合并

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

## 当前验证证据

### 已通过

```text
远端 head 7a35474: CI 10/10 success
本机 .venv Python 3.12.10 collect-only: 812 tests collected
compileall -f: passed
Ruff: passed
git diff --check: passed
repository hygiene against origin/main: passed
architecture growth: passed
  C901 46 -> 40
  Python modules 55 -> 69
architecture against current branch HEAD: passed
  C901 40 -> 40
  Python modules 69 -> 69
```

当前增量的定向验证：

```text
新增 safe.directory 回归: 1 passed
Git 历史与 staged candidate 邻接回归: 2 passed
finish 决策表: 11 passed
FinishRuntime 与 Goal consumer 行为回归: 2 passed
```

### 本机未形成全量通过结论

Windows 本机运行整个 `tests/test_repository_hygiene.py` 两次均超过 60 秒；一次使用全局
Python，一次使用项目 `.venv`。相关进程已按本轮命令范围清理，这两次运行均记为
**未通过**。关闭第三方 pytest 插件和 cacheprovider 后，受影响的单节点与邻接节点均
快速通过；该模式会因为项目仍声明 `cache_dir` 产生一个预期的 pytest 配置 warning。

因此最新提交的合并证据仍必须等待 Draft PR 的新 CI：

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

1. 先核对 Draft PR `#20` 最新 head 的全部 CI job，不要新建分支。
2. CI 失败时保留日志，只修与本 PR 直接相关的失败，不再扩展新信任模型。
3. CI 全绿后做一次独立 PR diff 与公开仓库卫生复核，再标记 Ready for Review。
4. 合并后停止横向基础设施扩张，转入真实代码任务的成功率、耗时、token 成本、人工接管
   和恢复体验验证。
5. MA 与 Assurance 实验继续留在独立实验分支，不带入本 PR。
