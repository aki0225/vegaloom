<div align="center">

<img src="docs/assets/vega-hero.jpg" width="100%" alt="Vega：一个写，一个审，独立会话共享证据">

# Vega

<h3>软件工程 Agent：计划、实现、验证、独立审查</h3>

<p><strong>One writes, one reviews.</strong></p>

<p>
  <a href="https://github.com/aki0225/vegaloom/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/aki0225/vegaloom/ci.yml?branch=main&style=for-the-badge&label=CI" alt="CI"></a>
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+">
  <a href="https://github.com/aki0225/vegaloom/releases/tag/v0.5.1"><img src="https://img.shields.io/badge/Release-v0.5.1-4fb8d8?style=for-the-badge" alt="Vega v0.5.1"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-F8FAFC?style=for-the-badge" alt="MIT License"></a>
</p>

**[在线展示](https://aki0225.github.io/vegaloom/)** ·
**[快速开始](#快速开始)** ·
**[运行方式](#运行方式)** ·
**[设计](#设计)** ·
**[文档](#文档)**

</div>

Vega 管一次代码变更的外层流程。只读 Planner 先调查自然语言目标，Vega 把调查结果编译成
待批准的 Change Contract；批准后，Vega 在隔离 Worktree 中复用一个 Coding Agent 会话完成
实现，把 Git Candidate 交给项目验证、风险门禁和独立 Reviewer。合同内问题可以自动回到
Worker；越出批准、授权或证据边界时停下来问人。

> 最新稳定版本：[v0.5.1](https://github.com/aki0225/vegaloom/releases/tag/v0.5.1)。
> Codex bounded 与 Claude Code human 主路径已完成真实 smoke，发布提交的完整 CI 和
> Python 3.12 package smoke 已通过。

<p align="center">
  <img src="docs/assets/vega-pipeline.svg" width="100%" alt="Vega ChangeRun：计划批准、Worker、验证、独立 Reviewer 和最终报告">
</p>

## 快速开始

要求 Python `>=3.11`、Git，以及已安装的 Codex CLI 或 Claude Code CLI。Vega 只能确认命令是否存在；
Provider 是否已登录，要在实际启动会话时确认。

在用于运行 Vega 的 Python 环境中安装稳定版：

```powershell
python -m pip install https://github.com/aki0225/vegaloom/releases/download/v0.5.1/vegaloom-0.5.1-py3-none-any.whl
```

然后进入**自己的目标 Git 项目**。自然语言任务需要项目提交一份 `.vega.yaml`，登记实际验证
命令。下面是 pytest 项目的最小示例；其他项目填自己的测试命令：

```yaml
version: 1
verification:
  commands:
    - python -m pytest -q
```

先在项目中运行一次该命令，确认依赖已安装、测试能执行，再提交配置。Vega 从已提交版本读取
策略；未提交的配置不会被当成授权。

## 日常主路径

进入目标 Git 仓库后，日常任务不需要复制 Run ID 或手工串起多个阶段：

```powershell
vega change "导出按钮点击后没有反应"
vega change                 # 继续当前任务，不传新目标
vega status
vega explain
```

带文本的 `vega change` 创建新的 ChangeRun；如果当前仓库已有活动任务，它会拒绝覆盖旧任务。
不带文本的 `vega change` 才会继续当前仓库唯一未完成的 ChangeRun。多个未完成任务、损坏记录
或无法证明归属时，Vega 拒绝猜测。
`vega status` 显示当前阶段、会话、Diff、门禁和下一步，`vega explain` 只读解释决定、
已确认事实、未知项和安全动作；两者默认优先选择当前仓库唯一未完成的 Run，没有活动任务时
显示最近更新的终态 Run，也可以用 `--run` 显式指定。

`change` 默认在当前 TTY 请求人工批准。Provider 请求会在同一终端显示脱敏摘要；如果协调
状态没有保存足以安全判断的完整原始目标或权限上下文，Vega 会中断当前 attempt、关闭这条
待响应请求，再转到恢复或原生会话接管。**同终端可见不等于同终端自动批准**，复杂、敏感或
无法分类的请求不能只凭摘要接受。

## 高级路径：拆开调查、批准和执行

主线新增的启动预检可以运行 `vega config check --repo . --change`；Claude 加
`--provider claude`。这条检查使用已提交的项目配置，不会替项目猜测试命令。

需要显式控制阶段、传入已有 Contract，或使用脚本化流程时，保留 `start`、`approve` 和
`run`：

```powershell
vega start --repo . --text "导出按钮点击后没有反应"
vega run --run <run_id> --timeout 900
```

`run` 先生成带来源引用的 Planning Proposal，再由确定性 Contract Compiler 对照固定
source revision、`.vega.yaml`、路径、验证、风险和预算，生成：

- **Change Contract**：目标、验收、不变量、非目标、风险、验证和允许范围；
- **Execution Plan**：已确认事实、假设和有限 Work Item；
- **Plan Card**：原始要求、建议合同、未决问题和人工批准材料。

Compiler 不执行 Planner 自由生成的命令。路径越界、未知验证、高风险声明缺失或 source
revision 漂移时，run 进入 `needs_human`，不会启动 Worker。编译通过后停在
`awaiting_approval`；确认 `runs/<run_id>/plan-card.md` 后继续同一个 ChangeRun：

```powershell
vega approve --run <run_id> --actor human
vega run --run <run_id> --timeout 900
```

默认批准方式是人工确认。重复、低风险的小任务可以在仓库中预先登记 bounded 策略：

```yaml
approval:
  bounded:
    enabled: true
    policy_id: docs-and-tests-v1
    allowed_paths: [docs/**, tests/**]
    max_changed_files: 4
    max_work_items: 2
    max_repair_rounds: 1
    max_auto_replans: 0
```

调用方仍要显式选择：

```powershell
vega run --run <run_id> --timeout 900 --approval bounded
```

Vega 只放行范围、验证、预算和副作用都明确，且没有命中人工风险规则的 Contract。策略不匹配
时 run 留在 `awaiting_approval`；策略或 Contract 变化后，旧批准失效。bounded 不会自动
push、创建 PR 或合并。

目标、范围和验证已经明确时，也可以使用显式
`vega start --contract ... --execution-plan ...` 跳过只读调查。

默认 Provider 是 Codex。首次执行加 `--provider claude` 可改用 Claude Code；选定后，同一
ChangeRun 会继续使用该 Provider，避免恢复时静默切换会话：

```powershell
vega run --run <run_id> --provider claude --timeout 900
```

两条路径复用同一个 ChangeRun、Git Candidate 和门禁。Worker Session 可跨 Work Item
继续，Reviewer 使用独立 Session。只有明确需要一次性短会话时才传 `--fresh-session`。

## 运行方式

### 看进度

日常查询优先使用不带 Run ID 的短命令：

```powershell
vega status
vega explain
```

```powershell
vega status --run <run_id>
vega status --run <run_id> --full
vega explain --run <run_id> --full
vega watch --run <run_id> --follow
vega latest
```

状态卡显示当前 Work Item、Provider Session、变更文件、验证、风险、Reviewer、待处理请求和
下一步。`watch` 只输出低频安全事件，不转发模型推理、完整正文、命令参数或凭据。

### 中途调整

```powershell
vega steer --run <run_id> --role worker --text "补充检查导入失败后的回滚路径"
vega pause --run <run_id> --reason "等待需求确认"
vega stop --run <run_id> --reason "方向变化"
```

`steer` 只能补充当前执行，不能改写批准合同。范围、验收或风险边界变化时，提交新的 Contract
和 Execution Plan：

高级 `vega run` 仍在另一个终端持有活动 Codex Turn 时，可以用
`vega respond --run <run_id> --interaction <request_id> ...` 响应已经核对的请求。
`vega change` 遇到这类请求会先停止 attempt 并关闭 pending；停止后再 `respond` 会被拒绝。

Codex 可以在当前 Turn 的安全事件边界接收 Steer；Claude Code V1 把它排到下一 Turn，不会把
不支持的中途控制伪装成已经送达。

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
- Core Work Item Reviewer 明确 `timed_out`，且 Candidate、Workspace、Verification、Risk、
  副作用和预算均可重新证明时，Vega 会使用新的独立 Reviewer Session 自动恢复一次；
  它复用原 Candidate 和 child，完整重跑 Verification、Risk 和 Reviewer，不启动新的
  Coding Worker。第二次超时、`error`、`stopped`、终止未确认、最终集成审查或任一前提不一致
  时保持 `needs_human`。
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

Provider Thread ID 只用于本机续接。跨机器恢复依赖任务分支和 Git 跟踪的 Task Card；已进入
执行阶段时携带 Candidate、Change Contract 与 Execution Plan，只读调查阶段则携带固定
source revision 的 Planning Proposal。恢复后的历史 Proposal 不算当前验证证据。

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

仓库也可以为低风险范围登记 bounded 策略。它只是批准来源，不改变 ChangeRun、验证、Reviewer
或最终人工 Git 交付边界。

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

- 自动 Provider Adapter 支持 Codex 和 Claude Code；两者共用同一套成功语义。
- Claude Code 使用 safe-mode、固定工具白名单和 Vega Worktree。这里没有额外宣称
  OS 或容器级沙箱。
- Provider 请求即使在当前终端可见，也不会因为缺少完整原始上下文而简化为自动批准；
  无法安全分类时必须转高级或原生会话处理。
- Reviewer 会话隔离用于切断 Worker 聊天上下文，不等于系统安全边界。
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
| v0.5.1 发布说明 | [RELEASE-NOTES-0.5.1](docs/RELEASE-NOTES-0.5.1.md) |
| v0.5.1 发布摘要 | [RELEASE-SUMMARY-0.5.1](docs/RELEASE-SUMMARY-0.5.1.md) |
| v0.5.0 历史发布说明 | [RELEASE-NOTES-0.5.0](docs/RELEASE-NOTES-0.5.0.md) |
| v0.5.0 历史发布摘要 | [RELEASE-SUMMARY-0.5.0](docs/RELEASE-SUMMARY-0.5.0.md) |
| 发布检查清单 | [RELEASE-CHECKLIST](docs/RELEASE-CHECKLIST.md) |
| v0.4.0 历史发布说明 | [RELEASE-NOTES-0.4.0](docs/RELEASE-NOTES-0.4.0.md) |
| v0.3.1 历史说明 | [RELEASE-NOTES-0.3.1](docs/RELEASE-NOTES-0.3.1.md) |
| v0.3.0 历史说明 | [RELEASE-NOTES-0.3.0](docs/RELEASE-NOTES-0.3.0.md) |
| 真实运行记录 | [real-world-runs](eval/real-world-runs.md) |

## Codex 宿主接入

在目标项目中生成仓库级 `$vega-agent` Skill：

```powershell
vega adapters init codex --repo .
```

它不会安装 Hook 或修改 Codex 全局配置。直接从终端运行 Vega 可以跳过。

## 开发

`Vega` 是产品名，`vegaloom` 是仓库和 Python distribution，`vega` 是 CLI 与导入包。

```powershell
git clone https://github.com/aki0225/vegaloom.git
cd vegaloom
python -m venv .venv
.\.venv\Scripts\Activate.ps1
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
