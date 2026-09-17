from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .agent_contract import GitOidText
from .execution_paths import ExecutionPathGuard
from .loop_evidence import LoopEvidenceValidationSnapshot
from .redaction import assert_not_sensitive_path, redact_text

if TYPE_CHECKING:
    from .agent_contract import AgentState
    from .agent_change_driver import AgentChangeDriver
    from .agent_change_presentation import ChangeDriverResult
    from .agent_run import AgentRun


AcceptanceText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2048)]


class AcceptanceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check: AcceptanceText
    action: AcceptanceText
    result: AcceptanceText
    uncovered: AcceptanceText
    source: Literal["host_observation", "tool_transcript", "worker_report"]


class AcceptanceSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
    candidate_sha: GitOidText
    records: list[AcceptanceRecord] = Field(min_length=1, max_length=4)


def read_submission(workspace: Path, path: Path, run_id: str, candidate_sha: str) -> str:
    """只读显式的工作目录内 JSON；不展开引用，不扫描日志或会话。"""
    if path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
        raise ValueError("验收文件须为 workspace 内子目录中的相对 JSON 路径")
    if any(part.lower() in {"runs", ".git"} for part in path.parts):
        raise ValueError("验收输入不能引用 Run 或 Git 控制目录")
    assert_not_sensitive_path(path)
    if path.suffix.lower() != ".json":
        raise ValueError("验收输入只接受 JSON")
    target = workspace / path
    guard = ExecutionPathGuard(workspace, target.parent)
    guard.validate_artifact(target)
    with target.open("rb") as stream:
        content = stream.read(32769)
    guard.validate_artifact(target)
    if len(content) > 32768:
        raise ValueError("验收输入超过 32768 字节")
    try:
        submission = AcceptanceSubmission.model_validate_json(content)
    except ValueError as exc:
        # 不把 Pydantic 的 input_value 原文暴露到错误或进度流。
        raise ValueError("验收 JSON 无效：需精确绑定和 1..4 条完整记录") from exc
    if submission.run_id != run_id or submission.candidate_sha != candidate_sha:
        raise ValueError("验收记录的 Run/Candidate 绑定不匹配")
    from .agent_change_presentation import redact_change_message

    records = [{key: redact_change_message(value) for key, value in record.model_dump().items()}
               for record in submission.records]
    return redact_text(json.dumps({
        "authority": "untrusted_host_acceptance_statement",
        **submission.model_dump(mode="json"), "records": records,
    }, ensure_ascii=False))


def require_acceptance_verdict(evidence: LoopEvidenceValidationSnapshot) -> None:
    """仅使用既有完整性验证返回的实际 Verdict，不采纳宿主分类。"""
    verdicts = evidence.artifact_integrity.review_verdicts
    if not verdicts or verdicts[-1].verdict != "needs_human" or (
        verdicts[-1].needs_human_reason != "acceptance_missing"
    ):
        raise ValueError("仅支持实际 Reviewer 明确分类为 acceptance_missing 的补验再审；旧未分类人工阻断不自动转换")


def archive_submission(run_dir: Path, operation_id: str, content: str) -> None:
    from .agent_verification_retry_archive import retry_source_finish_ref

    directory = (run_dir / retry_source_finish_ref(operation_id)).parent
    guard = ExecutionPathGuard(run_dir, directory)
    guard.prepare()
    path = directory / "acceptance.json"
    guard.validate_artifact(path)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def continue_acceptance(
    driver: AgentChangeDriver, current: AgentRun, path: Path,
) -> ChangeDriverResult:
    from .agent_provider import resolve_run_provider

    provider = resolve_run_provider(current.run_dir, driver.requested_provider)
    if driver.worker_permissions is not None:
        raise ValueError("补验再审不接受 Worker 权限变更；沿用已批准绑定")
    # 与既有验证重试一致：先在锁内拒绝无资格输入，再运行只读 Reviewer。
    # 不能提前启动交互 pump，影响另一次调用已经拥有的活动进程。
    result = driver._verification_retry(provider).run(
        current.run_dir.name, retry_reason="acceptance_supplement", acceptance_file=path,
    )
    driver.selected_run = result
    if result.state.phase == "completed":
        return driver._completed(result, provider)
    return driver._attention(
        result, "review.acceptance_rechecked",
        "补验再审已返回；未启动 Coding Worker。请按当前门禁处理未决事项。",
    )


def acceptance_explanation(run_dir: Path, state: AgentState):
    """只读准入与执行共用；展示不能解除更高优先级阻断。"""
    from .agent_explain import AgentExplanation
    from .agent_reviewer_timeout_retry import prepare_reviewer_timeout_source

    if state.phase != "needs_human":
        return None
    try:
        prepare_reviewer_timeout_source(run_dir.parent.parent, run_dir.name, acceptance=True)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return None
    return AgentExplanation(
        run_id=state.run_id, phase=state.phase, outcome="attention_required",
        reason_code="review.acceptance_missing", block_category="evidence", source="evidence",
        actor="Reviewer 与当前证据门禁",
        reason="Reviewer 明确只缺验收材料。主会话完成必要验收后可显式补交并在原 Candidate 再审，不启动 Worker。",
        safe_actions=["review.supplement", "run.stop"],
    )
