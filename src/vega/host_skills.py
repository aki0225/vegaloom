from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class AdapterInitResult:
    target: str
    created_files: list[Path]
    skipped_files: list[Path]


CODEX_SKILLS: dict[str, str] = {
    "vega-agent": """---
name: "vega-agent"
description: "当任务需要先调查、批准边界，再由 Vega 调度 Coding Agent 持续实现、验证和独立审查时使用。"
---

# Vega Agent

工作主会话负责调查、调用 Vega 推进、读取材料和直接技术汇报，不要求用户搬运材料、猜命令或翻报告。用户负责关键业务与生产授权；Worker 由 Vega 启动，固定验证和独立 Reviewer 由控制器调度。

## 选择入口

在目标仓库根目录执行以下命令，后续操作留在同一目录。先读取适用的 `AGENTS.md`、`.vega.yaml` 和 Git 状态。已有任务时先看 `status`、`explain`，确认目标再继续；不要为了绕过失败新建任务。

- 还没有明确计划：使用自然语言入口，由 Vega 的只读 Planner 调查。宿主不必先做一遍相同调查。
- 主会话已经调查并形成计划：使用显式合同入口，沿用已确认的事实与边界，不再调用 Planner 重做。
- Work Item 按实际可检查的改动拆分；小任务可以只有一个，不为凑数量拆步骤。

## 自然语言任务

目标项目需要在已提交的 `.vega.yaml` 中登记真实的 `verification.commands`。先确认命令能在项目中执行；缺少配置时向用户说明，不能自行猜测并授权验证命令。

宿主调用使用 `--json`，避免等待终端输入：

```powershell
vega config check --repo . --change
vega change "描述目标或 Bug 现象" --worker-permissions <用户选择的模式> --json
```

从返回值读取真实 run_id。`awaiting_approval` 表示调查完成、仍待批准，不能报告成任务完成。展示当前 Plan Card、Contract 和 Execution Plan 中的范围、验收、风险和未决问题。

`vega change` 带文本会新建任务；继续任务使用 `vega change --run <run_id> --json`，不要重复传入目标。多个活动任务时明确选择，不猜最新一个。

## 已有计划

先复现再修复通常是工作方法，不默认写成必须证明历史执行顺序的验收条件；确需历史首败记录时，开工前明确合法保存位置和交给 Reviewer 的方式。不能事后用绿测试替代已约定的首败证据。
显式人工 Contract 可在保留原固定验证的前提下追加用户已授权的本题验证；这不改变 Planner/bounded 的命令登记策略，不以显式入口冒用自动批准。

把主会话已有计划整理为两份 JSON：Change Contract 记录目标、验收、不变量、Non-goals、范围、风险授权和必跑验证；Execution Plan 记录事实、假设、Work Item、实现安排和未决问题。

```powershell
vega start --repo . --contract <change-contract.json> --execution-plan <execution-plan.json>
```

转换后的合同与计划也要展示给用户。讨论中的假设不是事实，之前对方向的同意也不能替代对实际执行边界的确认。

## 批准和执行

默认人工模式下，两条入口都在实际材料核对并得到用户明确批准后继续：

```powershell
vega approve --run <run_id> --actor <实际授权执行者>
vega change --run <run_id> --timeout 900 --json
```

持久 Codex 首次执行显式选择 `--worker-permissions ask|auto-review|full-access`，如已有绑定则沿用，不默认 full-access、不宣称继承宿主权限。执行权限不是任务或交付授权。实际代执行用户授权时如实填写 actor，不冒充用户直接操作。

首次选择 Claude Code 时，自然语言入口的预检和 `change` 都加 `--provider claude`；显式合同入口在首次 `change` 选择 Provider。同一任务后续沿用绑定的 Provider，不把 Codex 权限参数套用到 Claude。

默认 Codex 通过 App Server 复用 Worker Thread。只有明确需要一次性短会话时才加 `--fresh-session`；App Server 不可用时不会静默换执行路径。

## 看进度和干预

```powershell
vega capabilities
vega status --run <run_id>
vega explain --run <run_id>
vega watch --run <run_id> --follow
vega steer --run <run_id> --role worker --text "补充检查这个边界"
```

状态和 `watch` 只显示阶段、Work Item、安全事件、变更、验证、风险及待响应请求，不转发模型推理、完整正文或原始命令参数。

- 方向需要微调但合同没变：用 `steer`。
- 合同或执行计划要改：先生成新 revision，再运行 `vega revise`；触及合同字段时重新等待人工批准。
- 仅 Plan 调整且明确请求人工批准：`vega revise --run <run_id> --contract <原合同.json> --execution-plan <execution-plan.json> --request-approval`，保留原合同内容。提交待批不是批准，不用于绕过预算或风险门禁。
- `respond` 只响应仍有活动 owner、Thread 和 Turn 的请求；`change` 已停止 attempt 并关闭的 pending 请求不能再响应。
- 响应 JSON 含凭据或其他敏感信息：不要写进 Vega Artifact，改用 `vega takeover` 接管原生会话。
- 只有空闲 Session、没有 active Writer binding 且 Workspace 没有变化时才能 `vega reclaim`。活动 attempt 被接管后先做 Recovery 或 Handoff。

## 按状态处理

- `ready`：再次运行 `vega change --run <run_id>`，执行当前 Work Item 或明确的 Repair。
- `awaiting_approval`：展示 revision 差异，等待批准。
- `needs_human`：先读 `explain` 的原因和安全动作，再按需检查 Checkpoint 和失败证据。不要把 `change` 当万能恢复命令。
- `finalizing`：重新运行同一个 `vega change`，幂等发布已有可信 Finish。
- `completed`：读取 `agent-final-report.md`，展示完整变更文件、Reviewer 重点、验证、风险和未证明事项；同时给出 `status` 中的代码目录、任务分支、累计 Diff 基线和 Candidate，供用户检查与交付。

只有 Runtime 允许的返修才沿同一 Run 推进；区分实际缺陷、必要验收缺口与明确可选建议，不把全部 minor 当可选。高风险范围内返修须由用户在合同明确批准 `allow_pending_risk_repair=true`，不是最终交付确认；当前版本仅支持宿主在显式 Contract 中代填该字段并交人批准，自然语言入口不会自动启用；旧任务不自动扩权。Risk 仍待人工确认，needs_human 不得靠提示词代审批。Reviewer 使用独立只读 Thread；不要把 Worker 的完整聊天或中间推理转给 Reviewer。

开工前只核对本次目标实际会用到的工具与版本、已登记 prepare/验证命令及分阶段预算；配置检查通过不等于工具可调用。Provider `--timeout` 与项目 `verification.timeout_seconds` 独立，后者同时限制每条固定验证和准备命令；未给 Provider 预算的批准入口应说明由调用决定，不预设 900 秒。

受管 Worktree 的依赖准备须在源项目已登记的 `verification.prepare_commands` 中声明，并与合同 `prepare_commands` 完全一致，经批准后由控制器执行。普通宿主环境安装不等于受管目录已准备；不要在 start/approve 后自行向受管目录安装依赖再继续，ignored 依赖变化也可能使快照失效。项目未登记准备策略时先说明接入要求，策略修改及提交另取授权，不临时补命令或自行认可漂移。

依据任务和项目确定固定验证与必要实际验收，由具备工具的会话完成，尽量在送审前提供材料；固定 Verification 由控制器执行，不要求 Worker 重复全套。合理脚本照常使用，不同目的的执行、检查和通知不要无故混成一个请求，更不能拆命令规避权限。无合法工具或材料来源时如实保留缺口，不以自述 passed 冒充验证。主会话低频汇报阶段变化、阻塞和可干预动作；终态直接说明行为变化、关键代码位置、影响与风险、实际验证及未覆盖、需要用户决定的具体事项。文件只作恢复和审计备用。

验收补充仅支持 Worker 用 `acceptance_refs` 引用已批准项目范围内的现有 UTF-8 md/txt/log/json 材料（相对 path、原文 sha256，最多4份、每份4096字节）。由控制器作为独立审查数据冻结，不是机器通过证明；不包含完整自述或推理，不为此新开目录权限或写 Run。图片、任意控制器日志和其他来源不在此接口支持范围。

若实际 Reviewer 将 `needs_human_reason` 明确标为 `acceptance_missing`，主会话可在验收后使用 `vega change --run <run-id> --acceptance-file acceptance/checks.json`。文件相对 Run Workspace，不能位于 runs/Git 控制目录或经符号链接；无需写目标业务仓库。JSON 包含 `run_id`、完整 `candidate_sha`、1..4 条 `records`；每条包含 `check/action/result/uncovered`（各最多2048字符）和 `source`（host_observation/tool_transcript/worker_report），总文件最多32768字节。仅提交明确提供且脱敏的记录，不扫描会话或读取凭据，不传完整对话。来源类型不是可信认证，passed 文本不等于机器验证。
控制器保留旧审查，在原 Candidate 重跑既有门禁及独立审查，沿用预算，不启动 Coding Worker；新结论仍须经过正常 Finish。未分类历史 needs_human、人工决策/风险、代码或授权变化、活动进程、证据缺损不能借补验解除。没有该入口资格时照实报告，不代填 Reviewer 分类或修改旧 Artifact。

遇到 `acceptance_missing`，主会话先自行核对本次绑定的现有产物；必要且获准时，按 Run/Provider 记录的精确 Thread 身份只读提取原生测试工具结果，再通过上述 acceptance-file 入口补交。只传必要命令、结果、时序和来源，不传完整对话或推理，不读其他 Thread，不要求用户搬运日志，不恢复第二个 Writer。找不到历史结果就如实报告，事后对照不得冒充原先执行，也不自动放宽人工业务条件。

## 中断和换机器

没有活动 Writer 时用 `pause` 暂停；运行中需要结束执行时用 `stop`，确认进程已停止后再交接。

```powershell
vega pause --run <run_id> --reason "暂时离开"
vega handoff --run <run_id> --reason "换机器继续"
```

`handoff` 只生成 Task Card 和本机恢复材料，不替用户提交或推送任务分支。人工检查 WIP 与 Task Card 后，在任务分支 commit、push；新机器拉取该分支，再运行：

```powershell
vega resume --repo .
```

Provider Thread ID 只用于本机续接。换机器时以任务分支、Candidate SHA、Change Contract、Execution Plan 和 Task Card 恢复，不依赖旧聊天记录。

旧执行协议的 Run 只可查看、停止和可信交接；不能直接恢复 Writer。新 Run 或 Task Card 恢复采用当前协议。已登记环境准备由控制器在 Worker 前执行，Worker 不负责安装依赖。

## 固定边界

- 一个 ChangeRun 同时只有一个可写 Worker。
- Reviewer 只读，不继承 Worker Thread。
- Verification、Risk 和 Reviewer 证据不足时 fail-closed。
- Git 自动化只限于受管 Worktree 中的本地 Candidate/Checkpoint Commit；用户分支、push、merge、release、部署、回滚、删除文件和长期 Memory 仍由人工控制。
""",
}

CODEX_SKILLS_ROOT = Path(".agents") / "skills"


def init_adapter(repo_path: Path, target: str, force: bool = False) -> AdapterInitResult:
    normalized = target.strip().lower()
    if normalized != "codex":
        raise ValueError(f"暂不支持的 adapter target：{target}")
    try:
        repo = repo_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("无法确认 adapter 目标仓库路径") from exc
    if not repo.is_dir():
        raise ValueError("adapter 目标仓库必须是目录")

    created: list[Path] = []
    skipped: list[Path] = []
    targets = [
        (repo / CODEX_SKILLS_ROOT / skill_name / "SKILL.md", content)
        for skill_name, content in CODEX_SKILLS.items()
    ]

    # 必须先校验整批目标，避免后一个危险链接让前一个文件已经部分落盘。
    for path, _ in targets:
        _resolve_adapter_write_path(repo, path)

    for path, content in targets:
        resolved_path = _resolve_adapter_write_path(repo, path)
        if resolved_path.exists() and not force:
            skipped.append(path)
            continue

        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        # mkdir 后再次解析逻辑目标；边界在检查期间发生变化时必须停止。
        resolved_path = _resolve_adapter_write_path(repo, path)
        if resolved_path.exists() and not force:
            skipped.append(path)
            continue
        resolved_path.write_text(content.rstrip() + "\n", encoding="utf-8")
        created.append(path)
    return AdapterInitResult(target=normalized, created_files=created, skipped_files=skipped)


def _resolve_adapter_write_path(repo: Path, path: Path) -> Path:
    relative_path = path.relative_to(repo).as_posix()
    try:
        resolved_path = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"无法确认 adapter 写入路径边界：{relative_path}") from exc
    if not resolved_path.is_relative_to(repo):
        raise ValueError(f"adapter 写入路径越过目标仓库边界：{relative_path}")
    return resolved_path


def render_adapter_init_summary(result: AdapterInitResult) -> str:
    lines = [f"adapter 初始化完成：{result.target}", ""]
    if result.created_files:
        lines.extend(["已写入：", *[f"- {path}" for path in result.created_files], ""])
    if result.skipped_files:
        lines.extend(
            [
                "已存在，未覆盖：",
                *[f"- {path}" for path in result.skipped_files],
                "",
                "如需覆盖，请重新运行并添加 `--force`。",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
