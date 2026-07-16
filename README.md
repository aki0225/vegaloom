# Vega ｜织女星

**One writes. One reviews. Never the same context.**

Vega 是一个本地优先的 AI 编码工作流 Harness。它将 worker 与 reviewer 隔离，并用项目自己的
验证命令、风险门禁和运行证据，把一次编码任务收口成可审查、可恢复、可人工接管的闭环。

```text
task → context → worker → verification → risk gate → isolated review → report
```

## 核心能力

- 从任务、`AGENTS.md`、项目画像和 `.vega.yaml` 编译执行上下文。
- 支持 bug、feature 的人工协作 `assist` 与显式自动化 `auto` 流程。
- 运行项目自己的测试、静态检查和其他确定性验证。
- 使用独立只读 reviewer 审查 diff、测试证据和项目规则。
- 根据变更路径、diff 规模和预算输出风险等级与审查建议。
- 在失败、中断或证据不足时保存 state、trace、报告和人工接管入口。
- 不自动 commit、push、release，也不自动接受长期 Memory。

## 安装

要求 Python `>=3.11` 和 Git。只有使用自动 worker 或隔离 reviewer 时才需要已安装并登录
Codex CLI。

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
| Runtime、配置、证据链与风险门禁 | [ARCHITECTURE](docs/ARCHITECTURE.md) |
| 长任务 Goal 与 checkpoint | [LONG-RUNNING-GOALS](docs/LONG-RUNNING-GOALS.md) |
| v0.1 范围与取舍 | [MVP-SCOPE](docs/MVP-SCOPE.md) |
| 工作区与验证规范 | [WORKSPACE-HYGIENE](docs/WORKSPACE-HYGIENE.md) |

## 定位与边界

- Vega 面向个人和小团队的 AI 辅助研发流程治理，不是通用 Agent 框架或多 Agent 平台。
- Vega 是外围 Harness，不替代 Codex、Claude Code、Cursor 等编码工具。
- Vega 的本地策略、证据链和 reviewer 隔离不等同于操作系统级安全沙箱。
- `loop` 默认使用 `assist`；只有显式选择 `auto` 或 `do` 才启动外部 worker。
- Goal、Memory proposal 和 adapters 是可选能力，不扩大核心 loop 的成功条件。
- 当前稳定基线为 `v0.1.0`。

## 开发验证

```powershell
python -m compileall src
python -m pytest
ruff check src tests
git diff --check
```

Vega 的重点不是增加更多 Agent，而是让写、验、审和交付之间的边界更清楚。
