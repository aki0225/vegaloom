# 发布检查

发布版本：`v0.3.1`。

> 结果：2026-08-30 已发布。注解 Tag `v0.3.1` 和 GitHub Release 均绑定提交
> `5ee8328fa6c670c7feb788b5daa62cfe5615bb0f`。

本文件记录本轮候选的检查顺序和发布结果。

## 1. 版本和工作区

- `pyproject.toml` 与 `vega.__version__` 均为 `0.3.1`；
- README 顶部 badge、正文、发布说明和发布摘要均指向稳定版 `v0.3.1`；
- `git status --short` 只包含本次候选变更；
- 没有盘符/UNC/用户主目录绝对路径、凭据、`.env`、数据库、Office 文件或运行缓存；
- `eval/` 历史记录只追加，不改写；
- 新增的 `plans/events/` 事件与候选实现来自同一提交范围。

## 2. 公共 CLI

```powershell
vega --version
vega --help
vega capabilities
vega config check --repo .
```

版本必须输出 `0.3.1`。帮助中只出现当前顶层 ChangeRun 命令，以下旧入口不得重新注册：

```text
vega do
vega loop
vega agent
vega goal
vega run engineering-change
```

## 3. 本地门禁

文档、版本和 CI 修改至少运行：

```powershell
python -m compileall -q src scripts
python scripts/check_repository_hygiene.py --base-ref origin/main
python scripts/plan_state.py check --base-ref origin/main
python scripts/check_architecture_growth.py --base-ref origin/main
ruff check src tests scripts
git diff --check
```

本轮涉及 Core、Supervisor、恢复和安全边界时，按职责扩大到对应测试分片。完整 pytest
必须报告实际计数、跳过数、退出码和提交；不能把超时、被跳过或环境失败算作通过。

## 4. Package smoke

从干净输出目录构建：

```powershell
python -m build
python -m twine check dist/*
```

分别在全新虚拟环境安装 wheel 和 sdist，确认：

```text
版本元数据和 vega.__version__ = 0.3.1
vega --version = 0.3.1
vega --help
vega capabilities
pip check
```

两种制品都必须在 `python -I` 下执行：

```python
from vega.agent_handoff_digest import PORTABLE_WORKSPACE_DIGEST_KIND
assert PORTABLE_WORKSPACE_DIGEST_KIND == "git-blob-v1"
```

还要确认：

- wheel/sdist 包含 `vega-agent` Skill 资源；
- 从源码目录外运行，不意外导入当前 checkout；
- `vega capabilities` 仍声明 Codex App Server、持久 Worker、按 Work Item Reviewer
  隔离和显式 fresh-session；
- 基础安装不依赖 LangGraph；
- 已删除的旧 Skill 目录不会被安装；
- 已安装包的 `RunMutationLock` smoke 通过。

## 5. 真实 Agent smoke

使用干净目标仓库、正式安装的 `vega 0.3.1` 和真实 Codex 登录态。任务选择一个范围明确的
小型修改；不得读取目标仓库后续修复或本机原仓库。v0.3.0 Dogfood 已覆盖连续 Turn、
Steer 和 Reviewer 复用，本轮 smoke 只确认最终候选仍能走通真实
Worker → Candidate → Verification → Reviewer → Finish。

需要记录：

- Reviewer Thread 与 Worker 不同，且只读；
- `watch` 能看到低频安全事件，不泄露模型正文、推理、命令参数或凭据；
- Verification、Risk、Reviewer 和 Finish 均绑定同一 Candidate；
- 不需要人工在 Worker 和 Reviewer 之间复制完整消息；
- 失败、中断、超时或证据不足保持原现场并进入 `needs_human`；
- 目标仓库没有执行 push、PR、merge、部署或生产写入。

真实结果只以追加条目写入 `eval/real-world-runs.md`，不能回写历史条目。
Task Card 跨 checkout 恢复由 `VALID-02` 的 LF/CRLF、内容漂移、重复 Claim 和 Git mode
回归负责；只有本次 smoke 实际进入 Handoff 时，才额外执行新的 checkout 恢复。

## 6. 文档一致性

核对以下文档没有推荐已删除命令或声称未实现能力：

- `README.md`
- `docs/README.md`
- `PRODUCT-CONTRACT.md`
- `ARCHITECTURE.md`
- `USAGE-WALKTHROUGH.md`
- `PLAN-FIRST-PROTOCOL.md`
- `MVP-SCOPE.md`
- `ROADMAP.md`
- `RELEASE-NOTES-0.3.1.md`
- `RELEASE-SUMMARY-0.3.1.md`
- `CURRENT.md`

旧版本 Release、Gate、预注册和 `eval/` 文档保持当时事实，不为本次版本重写。

## 7. PR 与 CI

1. 将候选分支推送到远端并创建面向 `main` 的 PR；
2. PR 只包含 v0.3.1 候选、VALID-02 证据和必要的版本/文档/CI 更新；
3. 等待所有 required checks 完成，不把 skipped 或 pending 当作通过；
4. 失败时读取具体 job 日志，在同一候选分支修复并重新运行门禁；
5. 合并前确认 PR HEAD、本地验收提交和待合入内容一致；
6. 采用 Squash Merge，提交标题和说明使用简体中文；
7. 合并后删除已完成的远端候选分支。

`pr-ci` 在 required checks 全绿前只是候选事件；只有进入 `main` 后才成为主线事实。

## 8. Tag 与 Release

只有 PR 合入 `main` 且合并提交通过完整门禁后：

1. 从精确的 `main` 提交创建 annotated Tag `v0.3.1`；
2. 推送 Tag；
3. 创建 GitHub Release，正文使用 `RELEASE-SUMMARY-0.3.1.md`；
4. 核对 Release 绑定的 Tag、提交和自动生成制品；
5. 确认 GitHub Release 页面可访问；
6. 再提交一个简短文档更新，把候选状态改为“已发布”，并保留发布提交与 Tag 事实。

Vega 运行时不自动执行上述 Git 交付或发布动作；这些动作属于仓库维护流程。

本次 PR `#95` 以 Squash Merge 合入，PR CI `33296945545` 和 main CI `33297134548`
均为 10/10 jobs 通过；远端候选分支已删除。

## 9. 发布完成条件

本轮已满足：

- 本地门禁和必要测试有明确计数；
- wheel 与 sdist smoke 均通过，且 digest kind 断言通过；
- 真实 Agent smoke 已授权并记录结果；
- PR required checks 全绿；
- PR 已合入 `main`；
- `main` 合并提交的 CI 全绿；
- annotated Tag `v0.3.1` 已推送；
- GitHub Release 已创建并绑定正确提交。

历史版本 `v0.3.0` 的发布结果仍由其 Tag、Release 和
[`RELEASE-NOTES-0.3.0.md`](RELEASE-NOTES-0.3.0.md) 负责，不在本候选清单中重复改写。
