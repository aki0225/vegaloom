# 发布检查

发布版本：`v0.4.0`。

> 结果：2026-09-02 已发布。PR `#102` 以 Squash Merge 合入
> `bcc079e0fe4fce99bb20637fa09b021537f27abe`；注解 Tag `v0.4.0` 和 GitHub Release
> 均绑定该提交。

## 1. 候选范围

- `pyproject.toml` 与 `vega.__version__` 均为 `0.4.0`；
- README、站点、架构和文档导航指向 `v0.4.0`；
- 发布说明只总结 `v0.3.1..HEAD` 已进入主线的能力；
- `eval/` 历史记录保持不变；
- 工作区不包含绝对本机路径、凭据、`.env`、数据库、Office 文件或运行缓存。

## 2. 公共入口

```powershell
vega --version
vega --help
vega capabilities
vega config check --repo .
```

版本必须输出 `0.4.0`。旧的 `do / loop / agent / goal / inspection` 入口不得重新注册。

## 3. 完整基线

```powershell
python -m compileall -q src scripts
python scripts/check_repository_hygiene.py --base-ref origin/main
python scripts/plan_state.py check --base-ref origin/main
python scripts/check_architecture_growth.py --base-ref origin/main
python -m pytest
ruff check src tests scripts
git diff --check
```

完整 pytest 必须报告计数、跳过数、退出码和候选提交。超时、pending、skipped job 或环境阻塞
不能写成通过。

## 4. Package smoke

```powershell
python -m build
python -m twine check dist/*
```

分别从干净虚拟环境安装 wheel 和 sdist，检查：

- distribution metadata、`vega.__version__` 和 `vega --version` 都是 `0.4.0`；
- `vega --help`、`vega capabilities` 和 `pip check` 通过；
- `vega-agent` Skill 资源存在；
- `PORTABLE_WORKSPACE_DIGEST_KIND == "git-blob-v1"`；
- 基础安装不依赖 LangGraph；
- `RunMutationLock` smoke 通过；
- 从源码目录外执行时不导入当前 checkout。

## 5. 真实 Agent 证据

`AUTONOMY-05` 已覆盖：

- bounded 低风险自动批准；
- Human 批准、真实 Reviewer 打回和同 Worker Thread 自动返修；
- 数据库迁移与权限风险保持人工门禁；
- Codex 原生上下文压缩；
- Worker 中断、partial diff 和单 Writer；
- Task Card 换目录恢复并重新校验当前证据。

发布候选只复核现有追加式记录及其绑定实现，不为了发布重复消耗真实模型调用：

- `eval/autonomy-05-real-agent.md`
- `eval/real-world-runs.md`
- `plans/events/20260901T125605Z-AUTONOMY-05-completed.json`

## 6. PR 与 CI

1. 候选分支只包含版本、发布文档、CI 版本断言和必要站点更新；
2. PR required checks 全部结束且通过；
3. 合并前核对 PR HEAD、本地候选提交和实际 Diff；
4. 使用 Squash Merge，提交信息使用简体中文；
5. 合并后删除候选分支；
6. `main` 合并提交的 CI 再次全部通过。

## 7. Tag 与 GitHub Release

确认 `main` CI 通过后：

1. 从精确 `main` 合并提交创建 annotated Tag `v0.4.0`；
2. 推送 Tag；
3. 使用 `RELEASE-SUMMARY-0.4.0.md` 创建 GitHub Release；
4. 核对 Release 的 Tag、提交和页面；
5. 追加一个发布状态提交，记录 Tag 与 Release 事实。

Vega Runtime 本身不执行这些 Git 交付动作；本清单属于仓库维护流程。

## 8. 完成条件

本轮已满足：

- 本地完整测试为 `1489 passed, 12 skipped, 1 warning`；
- wheel、sdist、`twine check` 和两个干净虚拟环境安装 smoke 通过；
- PR `#102` 与合并提交的 10 项 CI 均通过；
- 注解 Tag `v0.4.0` 指向通过验证的 main 提交；
- GitHub Release 已创建，并附带 wheel 与 sdist；
- README、文档导航和发布状态均使用 `v0.4.0`。

历史 `v0.3.1` 材料保持原样，发布事实由其 Tag 与 Release 负责。
