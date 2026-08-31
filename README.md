<div align="center">

<img src="docs/assets/vega-hero.jpg" width="100%" alt="Vega：一个写，一个审，独立会话共享证据">

# Vega

<h3>软件工程 Agent：计划、实现、验证、独立审查</h3>

<p><strong>One writes, one reviews.</strong></p>

<p>
  <a href="https://github.com/aki0225/vegaloom/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/aki0225/vegaloom/ci.yml?branch=main&style=for-the-badge&label=CI" alt="CI"></a>
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+">
  <a href="https://github.com/aki0225/vegaloom/releases/tag/v0.3.1"><img src="https://img.shields.io/badge/Release-v0.3.1-4fb8d8?style=for-the-badge" alt="Vega v0.3.1"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-F8FAFC?style=for-the-badge" alt="MIT License"></a>
</p>

**[在线展示](https://aki0225.github.io/vegaloom/)** ·
**[快速开始](#快速开始)** ·
**[运行方式](#运行方式)** ·
**[设计](#设计)** ·
**[文档](#文档)**

</div>

Vega 管一次代码变更的外层流程。主会话先调查并提交 Change Contract；人工批准后，Vega
在隔离 Worktree 中复用一个 Coding Agent 会话完成实现，把 Git Candidate 交给项目验证、
风险门禁和独立 Reviewer。普通问题自动回到 Worker，越出批准边界才停下来问人。

> 最新稳定版本：[v0.3.1](https://github.com/aki0225/vegaloom/releases/tag/v0.3.1)。

<p align="center">
  <img src="docs/assets/vega-pipeline.svg" width="100%" alt="Vega ChangeRun：计划批准、Worker、验证、独立 Reviewer 和最终报告">
</p>

## 快速开始

要求 Python `>=3.11`、Git，以及已经安装并登录的 Codex CLI。

```powershell
git clone https://github.com/aki0225/vegaloom.git
cd vegaloom
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .

vega capabilities
vega config check --repo .
vega adapters init codex --repo .
```

最后一条命令写入仓库级 `$vega-agent` Skill。它不会安装 Hook，也不会修改 Codex 全局配置。

如果只有 Bug 现象，先让 Vega 做只读调查：

```powershell
vega start --repo . --text "导出按钮点击后没有反应"
vega run --run <run_id> --timeout 900
```

这一步只生成 `planning-proposal.json` 和 `planning-proposal.md`，记录已确认事实、假设、
未决问题、建议范围和验证建议。它不会启动 Worker。当前版本仍需由主会话或人工把 Proposal
整理为下面两份可批准文件：

- **Change Contract**：目标、验收、不变量、非目标、风险、验证和允许范围；
- **Execution Plan**：已确认事实、假设和有限 Work Item。

确认内容后启动 ChangeRun：

```powershell
vega start --repo . `
  --contract <change-contract.json> `
  --execution-plan <execution-plan.json>
vega approve --run <run_id> --actor human
vega run --run <run_id> --timeout 900
```

默认执行器是 Codex App Server。一个 ChangeRun 复用同一个 Worker Thread；Reviewer 使用
独立只读 Thread。只有明确需要一次性短会话时才传 `--fresh-session`。

## 运行方式

### 看进度

```powershell
vega status --run <run_id>
vega watch --run <run_id> --follow
vega latest
```

状态卡显示当前 Work Item、Provider Session、变更文件、验证、风险、Reviewer、待处理请求和
下一步。`watch` 只输出低频安全事件，不转发模型推理、完整正文、命令参数或凭据。

### 中途调整

```powershell
vega steer --run <run_id> --role worker --text "补充检查导入失败后的回滚路径"
vega respond --run <run_id> --interaction <request_id> --decision accept
vega pause --run <run_id> --reason "等待需求确认"
vega stop --run <run_id> --reason "方向变化"
```

`steer` 只能补充当前执行，不能改写批准合同。范围、验收或风险边界变化时，提交新的 Contract
和 Execution Plan：

```powershell
vega revise --run <run_id> `
  --contract <change-contract-v2.json> `
  --execution-plan <execution-plan-v2.json>
```

只调整合同内的实现安排可以继续执行；合同字段变化会重新进入人工批准。

### 失败和恢复

- 代码没变，只是验证环境或命令需要重跑：`vega retry --run <run_id>`。
- Worker 失去可信终态：准备 Recovery Request，再运行
  `vega recover --run <run_id> --input <recovery.json>`。
- 原生会话必须由人处理：`vega takeover --run <run_id> --role worker`。
- 空闲会话接管后且 Workspace 未变化时，可用
  `vega reclaim --run <run_id> --role worker` 交还控制权；活动 attempt 被中断后先做
  `recover` 或 Handoff。

Vega 不把超时、Provider 异常或 Reviewer 未完成包装成成功。现场无法解释时，状态进入
`needs_human`，并保留 Worktree、Checkpoint、Trace 和报告。

### 换机器继续

```powershell
vega handoff --run <run_id> --reason "换机器继续"
```

Handoff 生成 Git 可跟踪的 Task Card 和本机 Resume Capsule。人工检查 WIP 与 Task Card 后，
在任务分支完成 commit、push；新机器拉取同一分支，再运行：

```powershell
vega resume --repo .
```

Provider Thread ID 只用于本机续接。跨机器恢复依赖任务分支、Git Candidate、Change Contract、
Execution Plan 和 Task Card。

## 最终报告

所有 Work Item 完成后，Vega 从现有 Git、Verification、Risk 和 Reviewer Artifact
确定性生成：

```text
runs/<run_id>/agent-final-report.json
runs/<run_id>/agent-final-report.md
```

报告列出完整变更文件、Reviewer 建议优先查看的位置、实际验证结果、风险命中和未证明事项。
它不调用额外模型做总结。最终状态是 `completed / ready_to_commit` 时，仍由人决定是否 push、
创建 PR 或合并。

## 设计

### 批准边界和执行计划分开

人批准“做什么、验收是什么、哪里不能动”。Agent 可以在这个边界内调整实现方案、Work Item
和测试；触及合同字段就重新请求批准。

### Git 保存代码事实

自主执行发生在 Vega 管理的 Worktree。每个 Candidate 绑定 Git SHA；SHA 变化后，旧验证和
Reviewer 结论自动失效。Worker 的自述只算 Claim。

### Worker 和 Reviewer 分开

Worker Thread 可以在 Repair 和后续 Work Item 中复用。Reviewer 不继承 Worker Thread、
完整聊天或写权限；同一 Work Item 的复查可以复用自己的只读 Thread。多 Work Item、Replan
或高风险变更会触发最终集成审查。

### 路由由代码决定

LLM 调查、写代码和找语义问题。确定性状态机决定 `next`、`repair`、`replan`、`human`
和 `finalize`，并限制 Repair、Review、Replan 与验证重试次数。

## 边界

- 当前自动 Provider Adapter 只支持 Codex；Provider Session 合同不依赖 Codex 专有成功语义。
- Reviewer 会话隔离不是容器或操作系统级安全沙箱。
- 项目没有可靠验证命令时，Vega 也无法凭空证明代码可交付。
- 数据库迁移、支付、权限、部署和外部写入仍需要明确风险配置与人工判断。
- 用户当前分支保持不动。Vega 只在受管 Worktree 中创建本地 Candidate/Checkpoint Commit；
  push、merge、release、回滚、删除用户文件和接受长期 Memory 仍由人工控制。

## 文档

| 内容 | 文档 |
|---|---|
| 完整命令与恢复场景 | [USAGE-WALKTHROUGH](docs/USAGE-WALKTHROUGH.md) |
| 产品边界和成功语义 | [PRODUCT-CONTRACT](docs/PRODUCT-CONTRACT.md) |
| 当前架构 | [ARCHITECTURE](docs/ARCHITECTURE.md) |
| 修改前调查与计划 | [PLAN-FIRST-PROTOCOL](docs/PLAN-FIRST-PROTOCOL.md) |
| 当前事项 | [CURRENT](docs/CURRENT.md) |
| 文档导航 | [docs/README](docs/README.md) |
| v0.3.1 发布说明 | [RELEASE-NOTES-0.3.1](docs/RELEASE-NOTES-0.3.1.md) |
| v0.3.0 历史说明 | [RELEASE-NOTES-0.3.0](docs/RELEASE-NOTES-0.3.0.md) |
| 真实运行记录 | [real-world-runs](eval/real-world-runs.md) |

## 开发

`Vega` 是产品名，`vegaloom` 是仓库和 Python distribution，`vega` 是 CLI 与导入包。

```powershell
python -m pip install -e ".[dev]"
python -m compileall src scripts/check_repository_hygiene.py
python scripts/check_repository_hygiene.py --base-ref origin/main
python scripts/plan_state.py check --base-ref origin/main
python scripts/check_architecture_growth.py --base-ref origin/main
python -m pytest
ruff check src tests scripts/check_repository_hygiene.py
git diff --check
```

## License

[MIT](LICENSE)
