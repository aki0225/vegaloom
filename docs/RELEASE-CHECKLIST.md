# 发布检查

发布版本：`v0.5.0` 候选。

> 状态：候选清单，日期：2026-09-04。真实 Codex bounded 与 Claude Code human smoke
> 已完成；完整 CI、package smoke、Tag 和 GitHub Release 仍待验证。本清单不把本地文档
> 检查或计划状态写成发布通过。

## 1. 候选范围

- `pyproject.toml`、`vega.__version__`、Capabilities、CI 版本断言与发布材料统一为 `0.5.0`：**待验证**；
- README、站点、架构、产品契约、使用说明和路线文档切换到 `v0.5.0` 候选：**待复核**；
- 日常入口 `vega change`、`vega status`、`vega explain` 与高级 `start` / `approve` / `run`
  的边界在文档中保持一致：**待复核**；
- `eval/` 历史记录保持不变：**待复核**；
- 工作区不包含绝对本机路径、凭据、`.env`、数据库、Office 文件或运行缓存：**待验证**。

## 2. 公共入口

```powershell
vega --version
vega --help
vega capabilities
vega config check --repo .
vega change --help
vega status --help
vega explain --help
```

版本必须输出 `0.5.0`；日常入口应显示 `change`、`status`、`explain`，旧的 `do`、`loop`、
`agent`、`goal`、`inspection` 不得重新注册：**待验证**。

## 3. 本地最小检查

以下仅覆盖候选文档、仓库卫生和静态检查，不替代完整测试或跨平台 CI：

```powershell
python -m compileall -q src scripts
python scripts/check_repository_hygiene.py --base-ref origin/main
ruff check src tests scripts
git diff --check
```

每条命令的退出码、工作区状态和候选提交均需在发布记录中保存：**待验证**。

## 4. 完整基线

```powershell
python scripts/plan_state.py check --base-ref origin/main
python scripts/check_architecture_growth.py --base-ref origin/main
python -m pytest
```

完整 pytest、计划检查和架构检查必须报告计数、跳过数、退出码和候选提交。超时、pending、
skipped job 或环境阻塞不能写成通过：**待验证**。

## 5. Package smoke

```powershell
python -m build
python -m twine check dist/*
```

从干净构建目录分别安装 wheel 和 sdist，检查 distribution metadata、`vega.__version__`、
`vega --version`、`vega --help`、`vega capabilities`、`pip check`、Skill 资源和源码目录外
启动：**待验证**。

## 6. 真实 Provider 与恢复证据

候选发布前需要固定仓库、固定预算和脱敏记录证明：

- `vega change` 的 Codex bounded 路径：**已验证**；
- `vega change --provider claude` 的 Claude Code human、Worker / Reviewer 路径：**已验证**；
- Provider 请求在当前终端可见；缺少完整原始上下文时停止 attempt 并关闭 pending，未发生
  同终端自动批准或停止后的假响应：**自动化契约测试已验证，真实 smoke 未主动制造审批请求**；
- Core Work Item Reviewer 明确 `timed_out` 时最多自动恢复一次，第二次和不符合前提的情况
  保持 `needs_human`：**确定性故障注入已验证，未冒充真实 Provider timeout**；
- `vega status` / `vega explain` 的唯一 Run 选择、损坏记录拒绝和只读投影。

脱敏记录见
[`v0.5.0-daily-ux-smoke.md`](../examples/evidence/v0.5.0-daily-ux-smoke.md)。
真实 Provider 主路径：**已验证**；状态、交互与 timeout 恢复合同：**自动化验证已完成**。
不得用 timeout 夹具冒充真实 Provider timeout，也不得用历史 `eval/` 结果替代本候选验收。

## 7. PR 与 CI

1. 候选分支只包含批准范围内的版本、文档、站点和必要发布材料：**待验证**；
2. PR required checks 全部结束且通过：**待验证**；
3. 合并前核对 PR HEAD、本地候选提交和实际 Diff：**待验证**；
4. 使用 Squash Merge，提交信息使用简体中文：**待验证**；
5. 合并后删除候选分支：**待验证**；
6. `main` 合并提交的 CI 再次全部通过：**待验证**。

## 8. Tag 与 GitHub Release

确认 `main` CI 通过后，由人工执行：

1. 从精确 `main` 合并提交创建 annotated Tag `v0.5.0`：**待验证**；
2. 推送 Tag：**待验证**；
3. 使用 `RELEASE-SUMMARY-0.5.0.md` 创建 GitHub Release：**待验证**；
4. 核对 Release 的 Tag、提交、wheel 和 sdist：**待验证**；
5. 追加发布状态提交并生成新的 `CURRENT.md`：**待验证**。

Vega Runtime 不执行这些 Git 交付动作；本清单只记录仓库维护流程。

## 9. 完成条件

以下条件全部取得可复核事实后，才能把候选称为已发布：

- [ ] 版本文件与公共入口输出 `0.5.0`；
- [ ] 本地最小检查和完整基线均有退出码与计数；
- [ ] wheel、sdist、`twine check` 和干净环境安装 smoke 通过；
- [x] Codex、Claude Code 真实主路径与 Reviewer timeout 确定性恢复证据完整；
- [ ] PR required checks 与合并后 `main` CI 通过；
- [ ] annotated Tag `v0.5.0` 指向通过验证的 `main` 提交；
- [ ] GitHub Release 已创建且制品来自同一提交；
- [ ] 发布状态事件已追加并生成当前计划视图。

在上述事实出现前，`v0.5.0` 只能称为候选，不得写成“已发布”或“CI 已通过”。
历史 `v0.4.0` 材料保持原样。
