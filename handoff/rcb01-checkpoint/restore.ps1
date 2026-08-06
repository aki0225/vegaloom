[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,

    [Parameter(Mandatory = $true)]
    [string]$Python
)

$ErrorActionPreference = "Stop"
$expectedHead = "4e195df3f27a9ce8037d9ba6ccbd173fdd8c0105"
$checkpointRoot = $PSScriptRoot
$repo = (Resolve-Path -LiteralPath $RepoRoot).Path
$pythonCommand = (Get-Command $Python -ErrorAction Stop).Source
$localRoot = Join-Path $repo ".local-validation\rcb-01"
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
New-Item -ItemType Directory -Path $formalRuns | Out-Null
$consumed = @(
    "01-C1-A1", "02-C1-B1", "03-C2-B1",
    "04-C2-A1", "05-C3-A1", "06-C3-B1",
    "07-C4-B1", "08-C4-A1", "09-C5-A1"
)
foreach ($label in $consumed) {
    New-Item -ItemType Directory -Path (Join-Path $formalRuns $label) | Out-Null
}

& $pythonCommand (Join-Path $localRoot "run_reviewer_experiment.py") preflight
if ($LASTEXITCODE -ne 0) {
    throw "恢复后预检失败；不要修改 Freeze 或补跑已消费样本"
}

Write-Host "RCB-01 恢复完成；下一项固定为 10-C5-B1。"