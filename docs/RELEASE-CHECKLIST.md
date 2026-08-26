# Vega 发布前检查清单

这份清单用于正式打标签、公开演示或在 fresh clone 中复核 Vega 是否可用。它不新增产品能力，
只把安装、验证、证据和边界检查整理成可重复步骤。

## 一、适用范围

本清单适用于当前稳定主线：

- Python distribution：`vegaloom`
- CLI：`vega`
- 稳定公共 Python API：仅 `vega.__version__`
- 日常入口：`vega do`、`vega loop`、`vega status`、`vega finish`
- opt-in Agent 入口：`vega agent`、`vega adapters init codex`
- 只读 inspection 入口：`vega run engineering-change`

不在本清单中验证：

- 自动 commit、push、release 或部署。
- 生产数据库迁移或生产 backfill。
- 多 Work Item 自动派发、长期 Memory、Provider 平台或多 Reviewer 默认集成。
- 操作系统级 sandbox 或容器隔离。

## 二、干净工作区检查

发布前先确认工作区没有混入本地生成物：

```powershell
git status --short --branch
git diff --check
git check-ignore -v .env .tmp .local-validation runs memory .agents .claude .codex .trellis
```

期望：

- `git status` 只显示预期变更，发布前应干净。
- `.env`、`.tmp/`、`.local-validation/`、`runs/`、`memory/` 和本地 AI 工具目录被忽略。
- 不提交凭证、运行产物、本地验证日志或构建产物。

## 三、本地开发验证

常规验证：

```powershell
python -m compileall src scripts/check_repository_hygiene.py
python scripts/check_repository_hygiene.py --base-ref origin/main
ruff check src tests scripts/check_repository_hygiene.py
python -m pytest --collect-only -q
git diff --check
```

如果要做完整本地测试，优先让 pytest 使用项目内临时目录：

```powershell
python -m pytest --basetemp .tmp\pytest\runs\full-local
```

注意：

- 超时不是通过。必须看到明确的 passed、failed、skipped 计数。
- 长测试可按文件或 CI 分片拆开运行，但要记录覆盖范围。
- 本地 Windows 结果不能替代 GitHub Actions 的 Python 3.11、Python 3.12、POSIX、Windows
  和 package smoke。

## 四、干净安装 smoke

从源码树外创建临时目录安装 wheel，验证 CLI 可用。不要依赖当前 Python 已经全局安装
`build`；发布验证应使用项目内临时 venv：

```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$root = Join-Path ".tmp\release-readiness" $stamp
$buildVenv = Join-Path $root "build-venv"
$outDir = Join-Path $root "dist"
$smokeDir = Join-Path $root "package-smoke"

New-Item -ItemType Directory -Force $outDir | Out-Null

python -m venv $buildVenv
$buildPython = Join-Path $buildVenv "Scripts\python.exe"
& $buildPython -m pip install --upgrade pip build
& $buildPython -m build --outdir $outDir

python -m venv (Join-Path $smokeDir ".venv")
$smokePython = Join-Path $smokeDir ".venv\Scripts\python.exe"
& $smokePython -m pip install --upgrade pip
$wheel = Get-ChildItem $outDir -Filter "vegaloom-*.whl" |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
& $smokePython -m pip install $wheel.FullName

Push-Location $smokeDir
& ".\.venv\Scripts\vega.exe" --version
& ".\.venv\Scripts\vega.exe" list-loops
Pop-Location

$agentSmokeDir = Join-Path $root "agent-package-smoke"
python -m venv (Join-Path $agentSmokeDir ".venv")
$agentSmokePython = Join-Path $agentSmokeDir ".venv\Scripts\python.exe"
& $agentSmokePython -m pip install --upgrade pip
$agentWheel = "$($wheel.FullName)[agent]"
& $agentSmokePython -m pip install $agentWheel
Push-Location $agentSmokeDir
& ".\.venv\Scripts\vega.exe" agent capabilities
& $agentSmokePython -I -c `
  "from langgraph.checkpoint.sqlite import SqliteSaver; print(SqliteSaver.__name__)"
Pop-Location
```

期望：

- `vega --version` 输出当前版本。
- `vega list-loops` 在源码树外仍能看到包内 baseline loop。
- 安装 `agent` extra 后，`vega agent capabilities` 中的 `supervisor_runtime`、`langgraph`
  为 `true`，并且 `SqliteSaver` 可以从干净环境直接导入。
- 生成的 `.tmp/release-readiness/`、`build/` 和 egg-info 中间产物不提交。
- 如果系统 PATH 上已有旧版 `vega`，以当前 venv 或 smoke venv 中的 `vega.exe` 为准。

## 五、日常使用 smoke

在一个小型目标仓库上跑 assist 路径，避免无意启动外部 worker：

```powershell
vega loop feature --repo <target-repo> --text "补充 README 使用说明" --mode assist
vega latest --kind loop
vega status --run <run_id>
vega finish --run <run_id>
```

如需验证自动 worker，必须显式确认目标仓库可被修改，并确保目标仓库有可运行验证命令：

```powershell
vega do feature --repo <target-repo> --text "补充 README 使用说明" --mode auto
```

期望：

- run 产物只写入当前 Vega workspace 的 `runs/`。
- 目标仓库只出现任务相关 diff。
- 验证失败、证据不足或 reviewer 打回时进入人工处理状态。
- Vega 不自动 commit、push、release 或写长期 memory。

`vega finish --run <loop_run>` 面向普通 Core run；`vega agent finalize --run <agent_run>` 只在
父 Agent 已处于 `finalizing` 时采用已绑定的可信 Core Finish。后者不重新运行验证或 Reviewer，
不能替代前者生成 Core 交付证据。

### Codex JSONL 与显式 Worker 重跑验收

涉及 Codex 实时进度和终态解析的发布候选，还必须在最终候选提交上使用 fresh 小型目标仓库
完成一次真实 auto loop。不得复用较早提交、超时 run 或人工清理后的现场作为通过证据。

通过条件：

- Worker 和 Reviewer execution 都在各自 timeout 内正常退出，`execution.json`、根 state 和
  Finish 对终态的记录一致，且没有 owned process 残留。
- 两个 `process-output.txt` 都非空；输出可按行解析为 JSONL，并存在非空的最终
  `item.completed / agent_message`。
- Codex 的 `process-stderr.txt` 与 CLI stderr 只包含脱敏诊断或固定安全进度，不得混入
  JSONL stdout，也不得包含原始命令、文件路径、命令输出、模型正文、推理、工具参数或凭据。
- 终态前至少观察到一个固定安全进度事件。
- 目标仓库只保留任务允许的变更，Workspace、Scope、Verification、Risk、Reviewer 和 Finish
  均通过，最终 `Finish=ready_to_commit`。
- run artifacts 的高置信凭据扫描无匹配。

发布候选包含 `--rerun-worker` 变更时，还要在受控仓库中验证：

- 普通 continue 在无新成果的 Worker 中断后无副作用拒绝，不创建下一 iteration。
- 只有人工显式传入 `--rerun-worker` 才进入同一 child 的下一 iteration。
- baseline、授权、trace、重跑事务与 `worker_started` 能唯一绑定；证据缺失、冲突或事务文件
  无法删除时不调用可写 runner。
- 最新真实 Codex dogfood 与候选提交之间若只有版本和发布文档差异，可以复用该运行解释模型
  边界，但仍须在候选提交上执行确定性的重跑事务与 package smoke。

如果 Codex CLI 没有及时输出 JSONL、进程超时、终态消息缺失或证据不一致，本次 smoke 必须
记为未通过并保留现场；不得仅凭单元测试、CI 或安全终止行为创建 Tag。

### Supervisor Agent V1 发布验收

包含 `vega agent` 产品变更的发布候选还必须完成一个真实单 Work Item 案例。通过条件：

- 可以从任意目录运行 `vega adapters init codex --repo <target-repo>`，但随后必须进入
  `<target-repo>` 再执行 Agent CLI；初始化命令不会改变 shell 工作目录。

- Plan、Non-goals、成功条件、允许路径、验证命令和风险说明由人工显式批准。
- Writer 只有一个绑定的 child/operation；Provider 失败、进程消失或 unknown 副作用不会
  自动重试。
- Handoff 只提交 WIP 与 Task Card；新的隔离 clone 不复制旧 `runs/`、Trace、SQLite、
  虚拟环境、临时目录或聊天。
- 恢复侧重新生成 Workspace、Scope、Verification、Risk、独立 Reviewer 和 Finish。
- Reviewer 可以打回 Worker 的完成 Claim；人工修订 Plan 后使用新的 attempt，不覆盖旧证据。
- 最终 `Agent phase=completed` 且 `terminal_status=ready_to_commit`，目标变更再由人工 PR 处理。

如果最新真实验收使用的 Runtime commit 与发布候选之间只有版本号、CI 版本断言、发布文档和
对应版本测试差异，可以复用该真实运行解释模型边界；候选提交仍必须通过完整 CI、wheel/sdist
安装和 `vega agent capabilities` package smoke。

## 六、CI 与标签门禁

正式标签前必须确认 GitHub Actions 主线同一 commit 的任务全部成功：

- 静态检查与分片文件覆盖。
- Python 3.11 全量测试（main、release 或手工触发工作流）。
- Python 3.12 四个完整测试分片。
- Windows 专项与 wheel smoke。
- POSIX 临时目录专项。
- wheel/sdist 构建、安装和 package smoke。

通过后再人工决定是否创建 tag 或 GitHub Release。Vega 自身不会执行这些发布动作。

## 七、对外表述边界

可以说：

- Vega 是 AI 编码 Supervisor Agent 与验证 Harness。
- Vega 提供 opt-in、单 Work Item 的 Supervisor Agent 控制层。
- worker 与 reviewer 使用独立会话边界，reviewer 在只读视图中结合证据审查。
- 结构化验证、workspace snapshot、risk gate 和 finish evidence 共同决定是否可交付。
- Assurance Stage 1/2/3 是公开可复核的实验与证据，不等于默认 Runtime 能力。

不要说：

- Vega 是通用 Agent 框架或多 Agent 平台。
- Vega 已支持多 Worker 自治、完整 Provider 平台或无人值守长期运行。
- Vega 提供操作系统级 sandbox。
- Vega 自动提交、部署、发布或修改生产数据库。
- Stage 3 已经证明通用生产 backfill 安全。
