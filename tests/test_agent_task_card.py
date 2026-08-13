from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from vega.agent_contract import AgentPlan, AgentWorkItem, approve_plan
from vega.agent_task_card import (
    AgentTaskCard,
    ResumeCapsule,
    TaskCardError,
    discover_handoff_task_cards,
    parse_task_card,
    render_task_card,
    save_task_card,
)


REVISION = "a" * 40
WORKSPACE_DIGEST = "b" * 64


def test_task_card_round_trip_preserves_resume_capsule() -> None:
    card = _handoff_card()

    restored = parse_task_card(render_task_card(card))

    assert restored == card
    assert restored.resume_capsule is not None
    assert restored.resume_capsule.gate_evidence == []


def test_handoff_ready_requires_stopped_writer_and_explained_workspace() -> None:
    payload = _handoff_card().model_dump(mode="json")
    payload["resume_capsule"]["writer_stopped"] = False

    with pytest.raises(ValidationError, match="Writer 已停止"):
        AgentTaskCard.model_validate(payload)


def test_task_card_tampering_is_detected() -> None:
    content = render_task_card(_handoff_card())
    marker = "<!-- vega-task-card-state:v1\n"
    payload_text = content.split(marker, maxsplit=1)[1].split("\n-->", maxsplit=1)[0]
    payload = json.loads(payload_text)
    payload["status"] = "completed"
    tampered = content.replace(
        payload_text,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )

    with pytest.raises(TaskCardError, match="digest 不一致"):
        parse_task_card(tampered)


def test_discovery_only_returns_current_branch_handoff(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.name", "Vega Test")
    _git(tmp_path, "config", "user.email", "vega@example.invalid")
    _git(tmp_path, "checkout", "-b", "feature/current")
    task_root = tmp_path / ".vega" / "tasks" / "2026-08"
    save_task_card(task_root / "current.md", _handoff_card(branch="feature/current"))
    save_task_card(task_root / "other.md", _handoff_card(branch="feature/other"))
    _git(tmp_path, "add", ".vega/tasks")
    _git(tmp_path, "commit", "-m", "测试：加入交接卡片")

    matches = discover_handoff_task_cards(tmp_path)

    assert [path.name for path in matches] == ["current.md"]


def _handoff_card(*, branch: str = "feature/current") -> AgentTaskCard:
    plan = approve_plan(
        AgentPlan(
            task_id="2026-08-13-fix-timeout",
            user_goal="修复超时判断",
            non_goals=["不改发布流程"],
            success_conditions=["定向测试通过"],
            observed_facts=["tests/test_timeout.py 可复现"],
            hypotheses=["轮询可能没有预算"],
            work_items=[
                AgentWorkItem(
                    work_item_id="W1",
                    objective="修复轮询预算",
                    allowed_paths=["src/vega/execution_output.py"],
                    forbidden_paths=["eval/real-world-runs.md"],
                    verification=["python -m pytest tests/test_timeout.py"],
                    status="active",
                )
            ],
        ),
        actor="user",
        approved_at="2026-08-13T00:00:00+00:00",
    )
    return AgentTaskCard(
        task_id=plan.task_id,
        status="paused",
        branch=branch,
        base_revision=REVISION,
        plan=plan,
        current_work_item="W1",
        handoff_sequence=1,
        handoff_status="handoff_ready",
        handoff_base_revision=REVISION,
        handoff_workspace_digest=WORKSPACE_DIGEST,
        last_handoff_checkpoint="checkpoint-1",
        progress_notes=["已完成定向复现"],
        resume_capsule=ResumeCapsule(
            current_work_item="W1",
            stopped_at="修复前",
            confirmed_facts=["高频输出会延迟 timeout"],
            unresolved_hypotheses=["有界 poll 能否解决"],
            restrictions=["不得修改 eval 历史记录"],
            changed_files=["src/vega/execution_output.py"],
            workspace_digest=WORKSPACE_DIGEST,
            writer_stopped=True,
            workspace_explained=True,
            allowed_actions=["repair", "human"],
            next_step="先重新核对当前 Diff，再执行定向测试",
        ),
    )


def _git(repo: Path, *args: str) -> None:
    process = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert process.returncode == 0, process.stderr
