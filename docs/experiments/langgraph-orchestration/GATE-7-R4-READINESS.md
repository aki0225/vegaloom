# Gate 7 R4 Readiness

> 状态：`ready-for-baseline-freeze-not-frozen`
>
> 日期：`2026-07-20`

## 已验证

- R1、R2、R3 case、consumed tag 和失败结果保持原位；
- R4 使用独立 case、session、baseline tag 和 consumed tag 命名空间；
- R4 case/plan hash 已固定；
- R3 原始 transcript 可被 R4 parser 确定性判为超限；
- 命令审计已补充严格 `pwsh` wrapper、PowerShell 单引号 pattern、可展开语法拒绝和
  fixture cwd 绑定；
- worker Git 临时配置继续只允许当前 fixture repo 的 `safe.directory`；
- Codex runner 命令包含固定 `tool_output_token_limit`、verbosity 和 reasoning summary；
- Gate 7C 会重新读取 process output、audit、checkpoint 和 event ledger，不只信 summary；
- 安全修复后的 Gate 7 专项共 `42` 个节点，已由 `g01`、`g02`、`g03`、`g99`
  全部分片覆盖通过；
- execution control：使用仓库原先的系统 Python 运行，`17 passed`；
- `test_smoke.py`：127 个收集节点全部通过分片覆盖；
- `compileall`、Ruff、`git diff --check` 通过；
- 修复前 fake linear 与 fake LangGraph v1 双臂成功，但 prompt 已因安全修复改变，
  v1 不再作为 baseline 冻结证据；
- 修复后的 `gate7-r4-fake-linear-v2` 与 `gate7-r4-fake-langgraph-v2` 双臂均为
  `success`，case/plan/prompt/final tree/canonical diff 全部一致，自动重试为 `0`，
  scope、重复外部副作用、canary 和敏感材料命中均为 `0`；
- 项目全量 LangGraph 环境收集 `838` 个记录，node id 去重后仍为 `838`；
- 短路径复验已按互斥证据集合精确覆盖 `838/838`，最终 `838 passed`；
- Windows 硬退出窗口新增 `checkpoint-pending.json`，只有新 checkpoint 行与 manifest
  完成 seal 后才清除；`os._exit` 会保留 marker 并 fail-closed；
- 已验证实现提交为 `private-gate-7-r4-crash-marker-fix-redacted`。

## 本机测试说明

Windows 深路径下，dogfood smoke 的普通 pytest 临时路径超过 Win32 长度限制；该节点继续
使用同一仓库 `.tmp/pytest/runs/` 下的 `\\?\` 扩展路径。Gate 7 的 LangGraph SQLite
节点不能使用扩展路径，因为 `Path.as_uri()` 会让 SQLite 将 `?` 误判为 URI authority；
该节点改用最短合规普通路径 `.tmp/pytest/runs/g7l`，明确 `1 passed`。

LangGraph 补充回归使用项目 `.tmp/gate7-r4-test-env/` 的一次性离线环境，不修改项目
`.venv`、全局包或锁定依赖。

execution control 在上述一次性环境中曾因子 Python 启动超过测试冻结的 5 秒 marker
窗口出现 `1 failed, 16 passed`；改用仓库原先的系统 Python，避免双层 venv 启动后，
完整 17 节点明确通过。该环境时序失败不作为通过证据，最终通过证据来自完整系统 Python
运行。

## 全量回归闭环

2026-07-20 在短路径 checkout `<short-checkout>` 重新执行：

```text
473 个节点由 27 个完整文件分片明确通过
348 个节点由 12 个超时文件拆成完整 node id 后明确通过
17 个 execution control 节点由系统 Python 完整文件明确通过
------------------------------------------------------------
838 passed
0 failed
0 skipped
0 timeout-unresolved
```

12 个文件分片在冻结的 58 秒窗口内超时，因此文件级运行本身没有被计为通过；随后拆出的
348 个完整 node id 全部取得明确终态。覆盖清单按 node id 去重后仍为 `838/838`，没有
重复计数，也没有把超时当成通过。

短路径复验最初发现一个可稳定复现的真实恢复回归：terminal execution 和 Step Result
已经落盘，但 `os._exit` 发生在 `state.json` 与下一条 Graph checkpoint 之前。上一条
checkpoint 的 manifest 仍然自洽，无法单靠 SQLite hash 判断本次 Graph 提交是否闭合。

修复后，Runtime 在 Step Result 后写 `graph/checkpoint-pending.json`，并只在新 checkpoint
行与 manifest 成功 seal 后清除。硬退出会保留 marker，恢复因此安全停止，不打开可写
checkpointer，也不重复启动 worker。完整分析见
`GATE-7-R4-TEST-CLOSURE.md`。

## Baseline 冻结动作

- Gate 7、execution control、compileall、Ruff 和 `git diff --check` 已完成；
- `gate7-r4-fake-linear-v2` 与 `gate7-r4-fake-langgraph-v2` 已完成并重新冻结
  prompt hash；
- 项目全量 pytest 已明确闭合 `838/838`；
- 运行 `git status --short --branch` 与 `git check-ignore -v`，核对本地产物和提交范围；
- 使用 GitHub noreply 身份提交；
- 等待项目 owner 再次授权 baseline freeze；
- 授权后创建并推送两个 annotated baseline tags；
- 确认远端实验分支和两个 tag peel 到同一 baseline commit。

当前测试阻塞已经解除，但 baseline 尚未冻结。按本轮授权边界，不创建任何 R4 baseline
或 consumed tag，也不调用真实 provider。只有 owner 明确授权、baseline tag 身份复核
完成后，才允许启动一次真实 Gate 7A。
