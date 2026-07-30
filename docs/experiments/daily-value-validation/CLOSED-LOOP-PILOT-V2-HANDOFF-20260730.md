# Vega 真实写审闭环实验 V2 接力说明

- 日期：2026-07-30
- 分支：`experiment/ma2b-pilot-next`
- 实验合同：`docs/experiments/daily-value-validation/CLOSED-LOOP-PILOT-V2.md`
- Run ID：`20260730-175833-282706-bug-loop`
- 当前阶段：首次 Reviewer 已完成，等待一次隔离修复 Worker

## 1. 当前结论

V2 的 `reviewer_detection` 已满足预注册通过条件：

- controlled negative patch 只修改两个允许文件；
- 两条有限 verification 全部通过，`failed_count=0`；
- 新的只读 Reviewer 返回 `request_changes`；
- finding 明确指出实现忽略 rolling bucket，以及 daily/rolling 同时存在时没有取更严格剩余额度；
- Reviewer 没有接收 Worker 对话，审查期间 workspace 指纹保持不变。

因此当前可以记录：

```text
reviewer_detection=passed
repair_recovery=not_started
```

V2 尚未完成，不能声称修复恢复成功或完整闭环成功。

## 2. 已完成证据

### 2.1 Vega 状态

```text
status=needs_human
current_step=done
current_iteration=1
iteration.lifecycle=completed
verification_status=passed
verification_failed_count=0
reviewer_status=success
verdict=request_changes
findings_count=2
```

### 2.2 Reviewer 隔离与终态

- sandbox：`read-only`
- profile：`vega-daily-value-v1`
- model：`gpt-5.6-sol`
- reasoning：`medium`
- ephemeral：`true`
- 执行状态：`completed`
- return code：`0`
- 用时：约 `107` 秒
- `termination_unconfirmed=false`
- `contains_worker_chat=false`
- `evidence_consistent=true`
- `workspace_changed_during_review=false`

Reviewer 的两个 major finding：

1. `backend/app/quota.py` 只读取 daily 状态，忽略 rolling 剩余额度；
2. `backend/tests/test_quota_limits.py` 没有覆盖仅 rolling 和 daily/rolling 组合取严格值。

### 2.3 目标 workspace

```text
HEAD=305d05ed68a6f6626ba602df5c91e60cec27544c
remote=0
changed_files=2
insertions=46
deletions=9
git_diff_check=passed
```

当前 diff 仍是 frozen controlled negative patch，没有启动修复 Worker，也没有 commit：

```text
backend/app/quota.py
backend/tests/test_quota_limits.py
```

截至交接前检查，没有与本 run 关联的存活 Vega、Codex 或 Node 进程。

## 3. 原始证据哈希

原始证据位于 ignored `runs/` 与 `.local-validation/`，下表只记录相对身份和 SHA-256：

| Artifact | SHA-256 |
|---|---|
| `state.json` | `507e2afba33421da0c2eac9c7ff554e39578c7de3e4786d1629d7108f5dade47` |
| `trace.jsonl` | `90606d36a7841a71c561387d0e1b6e638cb0025d3c154c87ad6be9926687cab7` |
| `final-report.md` | `ea70bfe80422db2cf05478df28b9b6e69b6c9612b682d79ca79c4d11c2bf3107` |
| iteration 1 `verification-result.json` | `1517c51784c2c65523895937ad08fe183dd6c8fd4b14d799b0c133182f058843` |
| iteration 1 `review-verdict.json` | `095912d42053da6f64e2d9d90e99df121205eaf0034ef99a101b8b9b4eabe87f` |
| iteration 1 `review-findings.md` | `2623cd176850416503918b272fc422c5306692a079506a5894a223d8224bed33` |
| iteration 1 `fix-prompt.md` | `c1b131fc9fc3c378b05d512ffee3a2bba07f05470713d11eb413943c9c9c8641` |
| iteration 1 `review-context.json` | `4e3c0a5c14813801d61ff25efdd98366184b54b17ce263f5e64e79261127333d` |
| Reviewer `execution.json` | `c71711aed782e1b8ada4070a692716c9116afe521f36b83720aeb18eb05fd6e2` |
| Reviewer `process-output.txt` | `4767f27424d2a3d377937ee0e557e3d124c60eae06a537ae0dff48a314e1c8b4` |
| controlled `backend/app/quota.py` | `fafed5db2612768d337fdf3618f70612c4fd66c7d608197c7b200b01e5ddc5c8` |
| controlled `backend/tests/test_quota_limits.py` | `9b0a89320d582bd93bb114ee4df27c29aea741257280be8dae0cb8caf991e9cc` |

## 4. 继续前先确认

`runs/` 和 `.local-validation/` 默认不进入 Git。推送只保存合同、阶段记录和本交接，不会保存
可直接续跑的本地 workspace、venv 或 run state。

- 若仍在原工作目录继续，可以直接使用下面的命令；
- 若换到新 clone 或新机器，必须先完整复制原工作目录中的对应 ignored 目录并核对上表哈希；
- 如果拿不到原始 ignored 证据，不得伪造同一 run 的续跑结果，应预注册 V3 后重新运行。

先同步并确认现场：

```powershell
git fetch origin
git switch experiment/ma2b-pilot-next
git pull --ff-only

$runId = "20260730-175833-282706-bug-loop"
$run = Join-Path "runs" $runId
$workspace = Join-Path ".local-validation/closed-loop-pilot-v2/negative" "workspace"

Get-Content (Join-Path $run "state.json")
Get-Content (Join-Path $run "iterations/01/fix-prompt.md")
git -C $workspace status --short --branch
git -C $workspace rev-parse HEAD
git -C $workspace diff --check
```

预期必须仍为：

```text
state.status=needs_human
state.current_iteration=1
latest verdict=request_changes
workspace HEAD=305d05ed68a6f6626ba602df5c91e60cec27544c
只修改两个允许文件
```

任一身份不一致都停止，不要继续正式调用。

## 5. 下一次正式 Worker

只允许一次新的 `workspace-write` ephemeral Codex Worker。输入只由 V2 `task.md`、
Vega 生成的 `fix-prompt.md` 和目标仓库自动加载的 `AGENTS.md` 组成。不要附加 oracle、
controlled patch 来源、V1、Reviewer 对话、旧 Worker 输出或其他会话内容。

为保留 360 秒超时和 owned process 终止语义，直接复用 Vega 的
`CodexExecRunner + RunnerExecutionContext`，不要裸跑一个无法被 run 追踪的后台
`codex exec`：

```powershell
$env:PYTHONPATH = (Resolve-Path "src").Path
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:NO_COLOR = "1"

$python = (Resolve-Path ".local-validation/closed-loop-pilot-v1/harness-runtime-venv/Scripts/python.exe").Path

@'
import json
from pathlib import Path

from vega.execution_control import RunnerExecutionContext
from vega.project_config import CodexExecOptions
from vega.runner import CodexExecRunner

repo_root = Path.cwd()
run_id = "20260730-175833-282706-bug-loop"
run_dir = repo_root / "runs" / run_id
workspace = repo_root / ".local-validation" / "closed-loop-pilot-v2" / "negative" / "workspace"
task_path = repo_root / ".local-validation" / "closed-loop-pilot-v2" / "task.md"
fix_path = run_dir / "iterations" / "01" / "fix-prompt.md"
execution_dir = run_dir / "repair-worker"

prompt = (
    "# 任务合同\n\n"
    + task_path.read_text(encoding="utf-8")
    + "\n\n---\n\n# 本轮修复要求\n\n"
    + fix_path.read_text(encoding="utf-8")
)

runner = CodexExecRunner(
    options=CodexExecOptions(
        profile="vega-daily-value-v1",
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        ephemeral=True,
    )
)
result = runner.run(
    prompt,
    workspace,
    sandbox="workspace-write",
    timeout_seconds=360,
    execution_context=RunnerExecutionContext(
        execution_dir=execution_dir,
        run_id=run_id,
        step="repair-worker",
        iteration=2,
    ),
)
(execution_dir / "result.json").write_text(
    json.dumps(
        {
            "status": result.status,
            "error": result.error,
            "command": result.command,
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
raise SystemExit(0 if result.status == "success" else 1)
'@ | & $python -
```

该调用会在 `runs/<run-id>/repair-worker/` 写入 owned execution。若需人工提前停止：

```powershell
& $python -m vega.cli stop `
  --run "20260730-175833-282706-bug-loop" `
  --reason "操作者要求停止 V2 repair Worker"
```

Worker 只有在 `status=completed`、`returncode=0`、`termination_unconfirmed=false` 且 workspace
仍只修改两个允许文件时，才允许进入下一步。不要采用 Worker 最终自述作为通过证据。

## 6. 第二次 continue

Worker 正常完成后，先人工查看 diff，再让同一 run 进入 iteration 2：

```powershell
git -C $workspace status --short --branch
git -C $workspace diff --check
$workspacePath = (Resolve-Path $workspace).Path

& $python -m vega.cli loop continue `
  --repo $workspacePath `
  --run "20260730-175833-282706-bug-loop"
```

最终只有同时满足以下条件，才能记录 `repair_recovery=passed`：

- iteration 2 的两条 verification 全绿；
- 新的只读 Reviewer 正常结束；
- Reviewer verdict 为 `approve`；
- 父 loop 为 `success`；
- workspace HEAD、策略、scope 和证据绑定均未变化；
- 没有 commit、remote 或范围外文件。

如果 Worker timeout/stop、verification 失败、Reviewer 再次要求修改、Provider error 或证据
不一致，立即 fail-closed；V2 不允许隐藏重试。

## 7. 最终记录

当前阶段证据已追加到：

```text
eval/experiments/daily-value-validation/runs/CLOSED-LOOP-PILOT-V2-20260730.md
```

该文件属于 append-only 证据。完成或失败后只能在文件末尾追加 repair 阶段和最终结论，
不得修改本次 detection 记录，也不得改写 V1。
