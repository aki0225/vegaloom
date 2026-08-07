[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,

    [Parameter(Mandatory = $true)]
    [string]$Python,

    [string]$PrivateCheckpoint
)

$ErrorActionPreference = "Stop"
# 恢复脚本会跨 PowerShell、Python 和 Git 传递中文状态，显式统一为 UTF-8，避免重定向时按系统代码页解码。
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$checkpointRoot = $PSScriptRoot
$resumeStatePath = Join-Path $checkpointRoot "state\resume-state.json"
$resumeState = Get-Content -LiteralPath $resumeStatePath -Raw | ConvertFrom-Json
$expectedHead = $resumeState.generator_revision
$repo = (Resolve-Path -LiteralPath $RepoRoot).Path
$pythonCommand = (Get-Command $Python -ErrorAction Stop).Source
$localRoot = Join-Path $repo ".local-validation\rcb-01"
if ($resumeState.experiment_id -ne "RCB-01") {
    throw "resume-state 的实验标识不匹配"
}
if (-not $expectedHead) {
    throw "resume-state 缺少 generator_revision"
}
if ((Split-Path -Leaf $repo) -ne "vegaloom") {
    throw "目标目录名必须为 vegaloom；项目画像会把目录名写入冻结 Prompt，其他名称会造成哈希漂移"
}

function Invoke-GitText {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    $output = & git -C $repo @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Git 检查失败：git $($Arguments -join ' ')"
    }
    return ($output -join "`n").Trim()
}

$actualHead = Invoke-GitText rev-parse HEAD
if ($actualHead -ne $expectedHead) {
    throw "目标 worktree 必须停在冻结提交 $expectedHead，当前为 $actualHead"
}
$status = Invoke-GitText status --porcelain=v2 --untracked-files=all
if ($status) {
    throw "目标 worktree 不是干净状态，拒绝恢复实验现场"
}

New-Item -ItemType Directory -Path $localRoot -Force | Out-Null
$protected = @(
    "run_reviewer_experiment.py",
    "fake_codex.py",
    "fake_codex.cmd",
    "model-availability.json",
    "experiment-freeze.json",
    "C1", "C2", "C3", "C4", "C5",
    "formal-runs"
)
foreach ($name in $protected) {
    if (Test-Path -LiteralPath (Join-Path $localRoot $name)) {
        throw "目标已包含 RCB-01 关键现场，拒绝覆盖：$name"
    }
}

Copy-Item -LiteralPath (Join-Path $checkpointRoot "runner\run_reviewer_experiment.py") -Destination $localRoot
Copy-Item -LiteralPath (Join-Path $checkpointRoot "runner\fake_codex.py") -Destination $localRoot
Copy-Item -LiteralPath (Join-Path $checkpointRoot "runner\fake_codex.cmd") -Destination $localRoot
Copy-Item -LiteralPath (Join-Path $checkpointRoot "state\model-availability.json") -Destination $localRoot
Copy-Item -LiteralPath (Join-Path $checkpointRoot "state\experiment-freeze.json") -Destination $localRoot

foreach ($case in "C1", "C2", "C3", "C4", "C5") {
    $verification = Join-Path $checkpointRoot "verification-results\$case.json"
    $output = Join-Path $localRoot $case
    & $pythonCommand (Join-Path $repo "scripts\rcb01_materializer.py") `
        --repo-root $repo `
        materialize `
        --case $case `
        --verification-result $verification `
        --output-dir $output
    if ($LASTEXITCODE -ne 0) {
        throw "重新物化失败：$case"
    }
}

$formalRuns = Join-Path $localRoot "formal-runs"
$consumed = @($resumeState.consumed_run_labels)
if ($consumed.Count -eq 0) {
    throw "resume-state 缺少已消费序号"
}
if ($PrivateCheckpoint) {
    $privateArchive = (Resolve-Path -LiteralPath $PrivateCheckpoint).Path
    & $pythonCommand (Join-Path $checkpointRoot "private_checkpoint.py") restore `
        --archive $privateArchive `
        --destination $formalRuns `
        --resume-state $resumeStatePath `
        --runner (Join-Path $localRoot "run_reviewer_experiment.py") `
        --freeze (Join-Path $localRoot "experiment-freeze.json")
    if ($LASTEXITCODE -ne 0) {
        throw "私有 Artifact 检查点恢复失败"
    }
}
else {
    New-Item -ItemType Directory -Path $formalRuns | Out-Null
    foreach ($label in $consumed) {
        New-Item -ItemType Directory -Path (Join-Path $formalRuns $label) | Out-Null
    }
}

& $pythonCommand (Join-Path $localRoot "run_reviewer_experiment.py") preflight
if ($LASTEXITCODE -ne 0) {
    throw "恢复后预检失败；不要修改 Freeze 或补跑已消费样本"
}

Write-Host "RCB-01 恢复完成；下一项固定为 $($resumeState.next_run.label)。"
