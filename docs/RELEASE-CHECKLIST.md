# Vega 发布前检查清单

这份清单用于正式打标签、公开演示或在新机器上复核 Vega 是否可用。它不新增产品能力，
只把安装、验证、证据和边界检查整理成可重复步骤。

## 一、适用范围

本清单适用于当前稳定主线：

- Python distribution：`vegaloom`
- CLI：`vega`
- 稳定公共 Python API：仅 `vega.__version__`
- 日常入口：`vega do`、`vega loop`、`vega status`、`vega finish`
- 只读 inspection 入口：`vega run engineering-change`

不在本清单中验证：

- 自动 commit、push、release 或部署。
- 生产数据库迁移或生产 backfill。
- LangGraph、Memory、Goal P1 或多 Reviewer 默认集成。
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
python -m compileall src
ruff check src tests scripts
python -m pytest --collect-only -q
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
```

期望：

- `vega --version` 输出当前版本。
- `vega list-loops` 在源码树外仍能看到包内 baseline loop。
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

## 六、CI 与标签门禁

正式标签前必须确认 GitHub Actions 主线同一 commit 的任务全部成功：

- 静态检查与节点收集。
- Python 3.11 全量测试。
- Python 3.12 分片。
- Windows 专项与 wheel smoke。
- POSIX 临时目录专项。
- wheel/sdist 构建、安装和 package smoke。

通过后再人工决定是否创建 tag 或 GitHub Release。Vega 自身不会执行这些发布动作。

## 七、对外表述边界

可以说：

- Vega 是本地优先的 AI 编码工作流 harness。
- worker 与 reviewer 使用独立会话边界，reviewer 在只读视图中结合证据审查。
- 结构化验证、workspace snapshot、risk gate 和 finish evidence 共同决定是否可交付。
- Assurance Stage 1/2/3 是公开可复核的实验与证据，不等于默认 Runtime 能力。

不要说：

- Vega 是通用 Agent 框架或多 Agent 平台。
- Vega 提供操作系统级 sandbox。
- Vega 自动提交、部署、发布或修改生产数据库。
- Stage 3 已经证明通用生产 backfill 安全。
