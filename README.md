<div align="center">

<img src="docs/assets/vega-hero.jpg" width="100%" alt="Vega：一个写，一个审，独立会话共享证据">

# Vega

<h3>One writes, one reviews — worker 与 reviewer 上下文隔离的 AI 编码工作流 Harness</h3>

<p>
  <a href="https://github.com/aki0225/vegaloom/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/aki0225/vegaloom/ci.yml?branch=main&style=for-the-badge&label=CI" alt="CI"></a>
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/Baseline-v0.1.3-4fb8d8?style=for-the-badge" alt="v0.1.3">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-F8FAFC?style=for-the-badge" alt="MIT License"></a>
</p>

**[核心能力](#核心能力)** ·
**[安装](#安装)** ·
**[快速开始](#快速开始)** ·
**[关键行为](#关键行为)** ·
**[文档](#文档)** ·
**[定位与边界](#定位与边界)**

</div>

Vega 是一个本地优先的 AI 编码工作流 Harness，用独立的 worker 与 reviewer 会话分开实现和审查。

Worker 负责修改代码；Reviewer 不继承 worker 的完整对话和中间推理，只读取明确编译的任务、代码 diff、
项目规则和验证证据。任务是否满足完成条件，由项目自己的测试、静态检查和风险规则决定。

当执行失败、中断，或者证据缺失、过期、相互不一致时，Vega 停止自动执行，保存 state、trace、验证结果
和报告，交还人工处理。Vega 不是通用 Multi-Agent 框架，也不把会话隔离包装成操作系统级安全沙箱。

<p align="center">
  <img src="docs/assets/vega-pipeline.svg" width="100%" alt="Vega 任务流水线：task 到 report，worker 与 reviewer 使用独立会话，失败 fail-closed 交还人工">
</p>

<p align="center"><sub>写与审使用独立会话；验证失败或证据不足时，Vega 停止自动执行并交还人工。</sub></p>

## 核心能力

- 从任务、`AGENTS.md`、项目画像和 `.vega.yaml` 编译执行上下文。
- 支持 bug、feature 的人工协作 `assist` 与显式自动化 `auto` 流程。
- 运行项目自己的测试、静态检查和其他确定性验证。
- 使用独立只读 reviewer 会话审查 diff、测试证据和项目规则，不传递 worker 完整对话。
- 根据变更路径、diff 规模和预算输出风险等级与审查建议。
- 在失败、中断或证据不足时保存 state、trace、报告和人工接管入口。
- 不自动 commit、push、release，也不自动接受长期 Memory。

## 安装

要求 Python `>=3.11` 和 Git。只有使用自动 worker 或隔离 reviewer 时才需要已安装并登录
Codex CLI。

命名约定：`Vega` 是产品名，`vegaloom` 是公开仓库、Python distribution 和发布制品名，
`vega` 是 Python 导入包与 CLI 命令。

稳定用户入口是本文和产品契约记录的 `vega` CLI。Python 程序化接口只承诺
`vega.__version__`；`vega` 下的其他模块属于内部实现，允许随核心精简移动，不提供旧导入路径
兼容层。`vega.experimental.*` 更不承诺跨版本稳定，不能作为默认成功语义的扩展点。

```powershell
git clone https://github.com/aki0225/vegaloom.git
cd vegaloom

python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\Activate.ps1
vega --version
```

## 快速开始

推荐由当前主会话或人工负责实现，Vega 负责收集证据、执行验证并启动隔离审查：

```powershell
vega loop bug --repo . --text "修复导出按钮无响应" --mode assist
vega latest --kind loop
vega loop continue --repo . --run <run_id>
```

后续命令应在同一个 workspace 中执行。边界清晰的小任务可以使用默认启用 auto 的 `do`；
如需人工实现，仍可显式传入 `--mode assist`：

```powershell
vega do feature --repo . --text "新增批量导入用户功能"
```

查看运行状态并生成交付结论：

```powershell
vega status --run <run_id>
vega finish --run <run_id>
```

只需要只读检查和报告时，可以使用兼容的 Inspection Loop：

```powershell
vega run engineering-change --task examples/tasks/check-vega-runtime-docs.md --repo .
```

`vega do/loop` 使用 `LoopAutomationRuntime`，是当前日常 Coding Harness 主线；
`vega run engineering-change` 使用 `EngineeringChangeRuntime`，保留为 YAML 驱动的只读基线。

## 关键行为

- 确定性验证高于模型结论；测试失败时 reviewer 的 `approve` 不能把运行变成成功。
- auto 首轮不会接管已有 tracked diff，避免把历史改动错误归因给本轮 worker。
- staged 与 unstaged 变更都会进入审查证据，不使用可能相互抵消的净差异代替。
- 高风险路径、超预算变更或明确的 `human-review` 不会被 AI reviewer 自动放行。
- 证据缺失、过期或相互不一致时 fail-closed，并交还人工判断。
- Vega 不会自动提交、推送、发布、删除文件或写入长期 Memory。

## 文档

| 想了解 | 文档 |
|---|---|
| 完整使用闭环 | [USAGE-WALKTHROUGH](docs/USAGE-WALKTHROUGH.md) |
| 产品定位、非目标与成功语义 | [PRODUCT-CONTRACT](docs/PRODUCT-CONTRACT.md) |
| 当前演进路线与下一步 | [ROADMAP](docs/ROADMAP.md) |
| v0.1.3 发布摘要 | [RELEASE-SUMMARY-0.1.3](docs/RELEASE-SUMMARY-0.1.3.md) |
| v0.1.2 发布摘要 | [RELEASE-SUMMARY-0.1.2](docs/RELEASE-SUMMARY-0.1.2.md) |
| 安装、验收与发布前检查 | [RELEASE-CHECKLIST](docs/RELEASE-CHECKLIST.md) |
| Runtime、配置、证据链与风险门禁 | [ARCHITECTURE](docs/ARCHITECTURE.md) |
| Assurance 威胁模型与证据充分性候选合同 | [ASSURANCE-CONTRACT-CANDIDATE](docs/ASSURANCE-CONTRACT-CANDIDATE.md) |
| Assurance 逐项验证记录 | [assurance-validation](eval/assurance-validation.md) |
| 长任务 Goal 与 checkpoint | [LONG-RUNNING-GOALS](docs/LONG-RUNNING-GOALS.md) |
| v0.1 范围与取舍 | [MVP-SCOPE](docs/MVP-SCOPE.md) |
| 真实 Issue 上的运行记录与边界 | [real-world-runs](eval/real-world-runs.md) |
| 工作区与验证规范 | [WORKSPACE-HYGIENE](docs/WORKSPACE-HYGIENE.md) |
| v0.1.3 维护发布 | [RELEASE-NOTES-0.1.3](docs/RELEASE-NOTES-0.1.3.md) |
| v0.1.1 安全维护更新与迁移 | [RELEASE-NOTES-0.1.1](docs/RELEASE-NOTES-0.1.1.md) |
| v0.1.2 成功语义安全修复 | [RELEASE-NOTES-0.1.2](docs/RELEASE-NOTES-0.1.2.md) |

## 定位与边界

- Vega 面向个人和小团队的 AI 辅助研发流程治理，不是通用 Agent 框架或多 Agent 平台。
- Vega 是外围 Harness，不替代 Codex、Claude Code、Cursor 等编码工具。
- Vega 的本地策略、证据链和 reviewer 隔离不等同于操作系统级安全沙箱。
- `loop` 默认使用 `assist`；只有显式选择 `auto` 或 `do` 才启动外部 worker。
- Goal、Memory proposal 和 adapters 是可选能力，不扩大核心 loop 的成功条件。
- 当前稳定基线为 `v0.1.3`。它在 v0.1.2 成功语义安全修复基线上补充 Stage 2/3
  实验性证据、发布准备清单和公开说明，不扩大 v0.1 产品范围。

## 开发验证

```powershell
python -m compileall src scripts/check_repository_hygiene.py
python scripts/check_repository_hygiene.py --base-ref origin/main
python -m pytest
ruff check src tests scripts/check_repository_hygiene.py
git diff --check
```
