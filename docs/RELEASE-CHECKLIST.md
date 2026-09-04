# 发布检查

发布版本：`v0.5.0`。

> 状态：已发布，日期：2026-09-04。注解 Tag、GitHub Release、主线 CI 和制品均绑定
> `1a2ad71929805485ac44d30fba322b15cd150519`。本文件记录实际结果，不以 PASS 文案替代
> 命令终态或远端状态。

## 1. 发布范围

- `pyproject.toml`、`vega.__version__`、Capabilities、CI 版本断言与发布材料统一为 `0.5.0`：**已验证**；
- README、站点、架构、产品契约、使用说明和路线文档已切换到 `v0.5.0`：**已复核**；
- 日常入口 `vega change`、`vega status`、`vega explain` 与高级 `start` / `approve` / `run`
  的边界在文档中保持一致：**已复核**；
- `eval/` 历史记录保持不变：**已复核**；
- 工作区不包含绝对本机路径、凭据、`.env`、数据库、Office 文件或运行缓存：**已验证**。

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

版本输出 `0.5.0`；日常入口显示 `change`、`status`、`explain`，旧的 `do`、`loop`、
`agent`、`goal`、`inspection` 未重新注册：**已验证**。

## 3. 本地最小检查

以下仅覆盖候选文档、仓库卫生和静态检查，不替代完整测试或跨平台 CI：

```powershell
python -m compileall -q src scripts
python scripts/check_repository_hygiene.py --base-ref origin/main
ruff check src tests scripts
git diff --check
```

发布提交和发布后文档均完成编译、仓库卫生、Ruff 与 diff check，命令正常结束：**已验证**。

## 4. 完整基线

```powershell
python scripts/plan_state.py check --base-ref origin/main
python scripts/check_architecture_growth.py --base-ref origin/main
python -m pytest
```

发布提交的 main CI 运行 `33904159484` 共 10 个 job，全部结束并通过：

- Python 3.12 Core：`312 passed`；
- Python 3.12 Core Heavy：`126 passed`；
- Python 3.12 Supervisor：`441 passed`；
- Python 3.12 Security：`434 passed, 7 skipped`；
- Python 3.12 Experimental：`279 passed`；
- Python 3.11 兼容：`1592 passed, 7 skipped`；
- Windows 专项、POSIX 专项、静态检查与 package job 同时通过。

计划检查在发布提交上为 `31/32`，当前事项为 `RELEASE-05`；本次发布后事件进入主线后变为
`32/32`。架构增长检查保持 C901 `31 -> 31`：**已验证**。

## 5. Package smoke

```powershell
python -m build
python -m twine check dist/*
```

CI 在 Python 3.12 干净环境分别安装 wheel 和 sdist，并检查 distribution metadata、
`vega.__version__`、`vega --version`、`vega --help`、`vega capabilities`、`pip check`、
Skill 资源和源码目录外启动。发布维护机另以 Python 3.14 重复 wheel 与 sdist 安装 smoke：
**已验证**。

上传制品：

| 文件 | 大小 | SHA-256 |
|---|---:|---|
| `vegaloom-0.5.0-py3-none-any.whl` | 673430 bytes | `c4af770bc245757038e8c98fc9781178c7038ba9270cb0dcfcc9f27405f332d5` |
| `vegaloom-0.5.0.tar.gz` | 540662 bytes | `34b48dda23a52ce2e128b5ed02921e92f1b9600382d3ce1a4a2f6f7a4231fb39` |

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

1. 候选分支只包含 UX-01/02/03 实现、必要测试、版本、文档、站点和发布材料：**已验证**；
2. PR #108 的 10 个 CI job 全部结束且通过：**已验证**；
3. 合并前核对 PR HEAD `783e1beabedfba34ea558ac0af21beb2f283d736`、本地候选和实际 Diff：**已验证**；
4. 使用 Squash Merge，主线提交为 `1a2ad71929805485ac44d30fba322b15cd150519`：**已验证**；
5. 候选分支 `feat/v0.5.0-daily-ux` 已删除：**已验证**；
6. `main` 合并提交的 10 个 CI job 全部结束且通过：**已验证**。

## 8. Tag 与 GitHub Release

`main` CI 通过后完成：

1. 从精确主线提交创建并推送 annotated Tag `v0.5.0`；Tag 对象为
   `a72f678bd4b7214f580b3a6c6452bd63f0c962b2`，peeled commit 为
   `1a2ad71929805485ac44d30fba322b15cd150519`：**已验证**；
2. 使用 `RELEASE-SUMMARY-0.5.0.md` 创建非 draft、非 prerelease 的 GitHub Release
   `382931461`：**已验证**；
3. Release 中的 wheel 与 sdist 大小、GitHub 计算的 digest 和本地 SHA-256 一致：
   **已验证**；
4. 本次发布后变更已追加 `RELEASE-05` 完成事件并重新生成 `CURRENT.md`；该事实以本 PR
   合入 `main` 为准。

Vega Runtime 不执行这些 Git 交付动作；本清单只记录仓库维护流程。

## 9. 完成条件

以下发布条件均已取得可复核事实：

- [x] 版本文件与公共入口输出 `0.5.0`；
- [x] 本地最小检查和完整基线均有退出码与计数；
- [x] wheel、sdist、`twine check` 和干净环境安装 smoke 通过；
- [x] Codex、Claude Code 真实主路径与 Reviewer timeout 确定性恢复证据完整；
- [x] PR required checks 与合并后 `main` CI 通过；
- [x] annotated Tag `v0.5.0` 指向通过验证的 `main` 提交；
- [x] GitHub Release 已创建且制品来自同一提交；
- [x] 发布状态事件已追加并生成当前计划视图。

`v0.5.0` 已发布。历史 `v0.4.0` 材料保持原样。
