# 发布检查

已发布版本：`v0.3.0`。

## 1. 版本和工作区

- `pyproject.toml`、`vega.__version__`、README badge 和 CI package smoke 都是 `0.3.0`；
- `git status --short` 只包含本次发布候选变更；
- 没有本机绝对路径、凭据、`.env`、数据库、Office 文件或运行缓存；
- `eval/` 历史记录没有被改写。

## 2. 公共 CLI

基础检查：

```powershell
vega --version
vega --help
vega capabilities
vega config check --repo .
```

帮助中只出现当前顶层 ChangeRun 命令。以下旧入口不得注册：

```text
vega do
vega loop
vega agent
vega goal
vega run engineering-change
```

## 3. 本地验证

```powershell
python -m compileall src scripts/check_repository_hygiene.py
python scripts/check_repository_hygiene.py --base-ref origin/main
python scripts/plan_state.py check --base-ref origin/main
python scripts/check_architecture_growth.py --base-ref origin/main
python -m pytest
ruff check src tests scripts/check_repository_hygiene.py
git diff --check
```

报告精确通过数、跳过数、退出码和提交。超时不是通过。

## 4. Package smoke

```powershell
python -m build
```

在全新虚拟环境分别安装 wheel 与 sdist，检查：

```powershell
vega --version
vega --help
vega capabilities
```

还要确认：

- 包含 `.agents` Skill 生成资源；
- 不依赖源码目录；
- `vega capabilities` 显示 Codex App Server、持久 Worker、Reviewer isolation 和显式
  fresh-session；
- 基础安装不需要 LangGraph。

## 5. 真实 ChangeRun

使用干净目标仓库和真实 Codex 登录态。任务必须足够触发至少一次连续 Turn，优先覆盖 Repair
或两个 Work Item。

检查：

- Worker Thread ID 在允许复用的 Turn 间保持一致；
- Reviewer Thread 与 Worker 不同；
- Reviewer 为只读；
- `watch` 能看到安全事件；
- 至少发送一次 `steer`；
- Verification、Risk、Reviewer 和 Finish 绑定当前 Candidate；
- `agent-final-report.md` 来自确定性 Artifact；
- 过程中不需要人工转贴 Worker / Reviewer 正文；
- 失败或中断原样保留，不补写成功。

真实结果只以追加条目写入 `eval/real-world-runs.md`。

## 6. 文档

核对：

- README；
- PRODUCT-CONTRACT；
- ARCHITECTURE；
- USAGE-WALKTHROUGH；
- PLAN-FIRST-PROTOCOL；
- MVP-SCOPE；
- ROADMAP；
- RELEASE-NOTES / RELEASE-SUMMARY；
- CURRENT。

当前文档不得推荐已删除命令。历史 Release、Gate、预注册和 `eval/` 文档保持当时事实。

## 7. 计划事件

实现 PR 同时追加：

```text
SESSION-01 completed
SESSION-02 completed
SESSION-03 completed
SESSION-04 completed
```

每个事件只能在对应检查完成后写入。运行：

```powershell
python scripts/plan_state.py render
python scripts/plan_state.py check --base-ref origin/main
```

`docs/CURRENT.md` 由脚本生成，不手工修改。

`pr-ci` 由包含这些事件的同一个 PR 追认：CI 完成前，事件只是候选分支上的完成声明，
不能作为合并依据；required checks 全绿后才成立。

## 8. PR CI

- 当前分支推送到远端；
- PR 只包含 v0.3.0 Agent 变更；
- 所有 required checks 完成，不把 skipped 或 pending 当成通过；
- 失败时读取具体 job 日志；
- 修复提交重新跑同一套门禁；
- 合并前确认远端 HEAD 与本地验收提交一致。

## 9. 发布

只有 PR 合入 `main` 且发布提交通过完整门禁后：

1. 从精确 `main` 提交创建 annotated Tag `v0.3.0`；
2. 推送 Tag；
3. 创建 GitHub Release；
4. 使用 `RELEASE-NOTES-0.3.0.md`；
5. 核对 Release 绑定 commit、Tag、制品和 CI；
6. 再把文档中的“发布候选”更新为“已发布”。

Vega 本身不执行 commit、push、merge、Tag 或 Release；这些动作由人或当前仓库维护流程完成。

## 10. 本次结果

- 发布提交：`167567982cb9e72cf2e1ed01eee1d0f09d6e03d3`；
- `main` 的 10 项 GitHub Actions 全部通过；
- 注解 Tag `v0.3.0` 已推送；
- GitHub Release `Vega v0.3.0` 已发布；
- GitHub 自动生成的源码 ZIP 与 tar.gz 可用。
