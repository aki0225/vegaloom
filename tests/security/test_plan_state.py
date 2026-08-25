from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _plan_state_module() -> ModuleType:
    script = PROJECT_ROOT / "scripts" / "plan_state.py"
    spec = importlib.util.spec_from_file_location("plan_state", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


plan_state = _plan_state_module()


def _plan_item(
    item_id: str,
    *,
    depends_on: list[str] | None = None,
    title: str | None = None,
) -> dict[str, object]:
    return {
        "id": item_id,
        "title": title or f"{item_id} 标题",
        "summary": f"{item_id} 摘要",
        "depends_on": depends_on or [],
        "acceptance": [f"{item_id} 验收"],
        "required_checks": [f"{item_id.lower()}-check"],
    }


def _write_plan(repo: Path, items: list[dict[str, object]]) -> None:
    _write_json(
        repo / plan_state.PLAN_PATH,
        {
            "schema_version": 1,
            "plan_id": "test-plan",
            "title": "测试计划",
            "summary": "用于验证计划状态合同。",
            "items": items,
        },
    )
    (repo / plan_state.EVENTS_DIR).mkdir(parents=True, exist_ok=True)


def _write_event(
    repo: Path,
    item_id: str,
    transition: str,
    *,
    recorded_at: str,
    checks: list[str] | None = None,
) -> Path:
    timestamp = recorded_at.replace("-", "").replace(":", "")
    event_id = f"{timestamp}-{item_id}-{transition}"
    path = repo / plan_state.EVENTS_DIR / f"{event_id}.json"
    _write_json(
        path,
        {
            "schema_version": 1,
            "event_id": event_id,
            "plan_id": "test-plan",
            "item_id": item_id,
            "transition": transition,
            "recorded_at": recorded_at,
            "summary": f"{item_id} {transition}",
            "evidence": {
                "pull_requests": [],
                "commits": [],
                "checks": checks or [f"{item_id.lower()}-check"],
                "artifacts": ["docs/evidence.md"],
            },
        },
    )
    return path


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=True,
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    return repo


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)


def test_repository_plan_and_generated_current_view_are_consistent() -> None:
    snapshot = plan_state.load_snapshot(PROJECT_ROOT)

    assert snapshot.current_item_id == "ARCH-01"
    assert snapshot.completed_count == 4
    assert plan_state.check_current_view(PROJECT_ROOT, snapshot) == []


def test_stale_generated_current_view_is_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_plan(repo, [_plan_item("FIRST")])
    current = repo / plan_state.CURRENT_VIEW_PATH
    current.parent.mkdir()
    current.write_text("stale\n", encoding="utf-8")

    issues = plan_state.check_current_view(repo, plan_state.load_snapshot(repo))

    assert issues == [
        "当前计划视图与计划事件不一致；"
        "请运行 `python scripts/plan_state.py render`"
    ]


def test_plan_rejects_dependency_defined_after_current_item(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_plan(
        repo,
        [
            _plan_item("SECOND", depends_on=["FIRST"]),
            _plan_item("FIRST"),
        ],
    )

    with pytest.raises(plan_state.PlanStateError, match="必须已经在计划中定义"):
        plan_state.load_plan(repo)


def test_completed_event_requires_all_declared_checks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_plan(repo, [_plan_item("FIRST")])
    _write_event(
        repo,
        "FIRST",
        "completed",
        recorded_at="2026-08-25T08:00:00Z",
        checks=["other-check"],
    )

    with pytest.raises(plan_state.PlanStateError, match="缺少事项要求的检查"):
        plan_state.load_snapshot(repo)


def test_event_filename_time_must_match_recorded_at(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_plan(repo, [_plan_item("FIRST")])
    path = _write_event(
        repo,
        "FIRST",
        "completed",
        recorded_at="2026-08-25T08:00:00Z",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["recorded_at"] = "2026-08-25T08:00:01Z"
    _write_json(path, payload)

    with pytest.raises(plan_state.PlanStateError, match="时间与 recorded_at 不一致"):
        plan_state.load_snapshot(repo)


def test_item_cannot_advance_before_dependency_is_completed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_plan(
        repo,
        [
            _plan_item("FIRST"),
            _plan_item("SECOND", depends_on=["FIRST"]),
        ],
    )
    _write_event(
        repo,
        "SECOND",
        "completed",
        recorded_at="2026-08-25T08:00:00Z",
    )

    with pytest.raises(plan_state.PlanStateError, match="前置事项完成前推进"):
        plan_state.load_snapshot(repo)


@pytest.mark.parametrize("change", ["rewrite", "delete"])
def test_baseline_event_cannot_be_rewritten_or_deleted(
    tmp_path: Path,
    change: str,
) -> None:
    repo = _init_repo(tmp_path)
    _write_plan(repo, [_plan_item("FIRST")])
    event = _write_event(
        repo,
        "FIRST",
        "completed",
        recorded_at="2026-08-25T08:00:00Z",
    )
    plan_state.write_current_view(repo, plan_state.load_snapshot(repo))
    _commit_all(repo, "基线")

    if change == "rewrite":
        payload = json.loads(event.read_text(encoding="utf-8"))
        payload["summary"] = "改写既有事件"
        _write_json(event, payload)
    else:
        event.unlink()

    issues = plan_state.check_event_history(repo, "HEAD")

    assert any("既有计划事件不得" in issue for issue in issues)


def test_baseline_event_allows_worktree_line_ending_conversion(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    _write_plan(repo, [_plan_item("FIRST")])
    event = _write_event(
        repo,
        "FIRST",
        "completed",
        recorded_at="2026-08-25T08:00:00Z",
    )
    plan_state.write_current_view(repo, plan_state.load_snapshot(repo))
    _commit_all(repo, "基线")

    event.write_bytes(event.read_bytes().replace(b"\n", b"\r\n"))

    assert plan_state.check_event_history(repo, "HEAD") == []


def test_event_rewrite_cannot_be_hidden_by_later_restore(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write_plan(repo, [_plan_item("FIRST")])
    event = _write_event(
        repo,
        "FIRST",
        "completed",
        recorded_at="2026-08-25T08:00:00Z",
    )
    plan_state.write_current_view(repo, plan_state.load_snapshot(repo))
    _commit_all(repo, "计划基线")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    original = event.read_bytes()

    payload = json.loads(event.read_text(encoding="utf-8"))
    payload["summary"] = "临时改写"
    _write_json(event, payload)
    _commit_all(repo, "改写事件")
    rewrite_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    event.write_bytes(original)
    _commit_all(repo, "恢复事件")

    issues = plan_state.check_event_history(repo, base_sha)

    assert any(rewrite_sha[:12] in issue for issue in issues)


def test_new_event_is_allowed_and_selects_next_executable_item(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    _write_plan(
        repo,
        [
            _plan_item("FIRST"),
            _plan_item("SECOND", depends_on=["FIRST"]),
        ],
    )
    plan_state.write_current_view(repo, plan_state.load_snapshot(repo))
    _commit_all(repo, "计划基线")

    _write_event(
        repo,
        "FIRST",
        "completed",
        recorded_at="2026-08-25T08:00:00Z",
    )
    snapshot = plan_state.load_snapshot(repo)
    plan_state.write_current_view(repo, snapshot)

    assert snapshot.current_item_id == "SECOND"
    assert plan_state.check_plan_state(repo, "HEAD") == []


def test_event_backed_plan_items_are_immutable_but_future_items_may_change(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    first = _plan_item("FIRST")
    second = _plan_item("SECOND", depends_on=["FIRST"])
    _write_plan(repo, [first, second])
    _write_event(
        repo,
        "FIRST",
        "completed",
        recorded_at="2026-08-25T08:00:00Z",
    )
    plan_state.write_current_view(repo, plan_state.load_snapshot(repo))
    _commit_all(repo, "计划基线")

    refined_second = _plan_item(
        "SECOND",
        depends_on=["FIRST"],
        title="根据新证据修订",
    )
    _write_plan(repo, [first, refined_second])
    current = plan_state.load_plan(repo)
    assert plan_state.check_plan_history(repo, "HEAD", current) == []

    _write_plan(repo, [_plan_item("FIRST", title="被改写"), refined_second])
    current = plan_state.load_plan(repo)
    assert plan_state.check_plan_history(repo, "HEAD", current) == [
        "已有状态事件的计划事项不得改写：FIRST（worktree）"
    ]


def test_future_plan_tail_may_be_reordered_or_replaced(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    first = _plan_item("FIRST")
    second = _plan_item("SECOND", depends_on=["FIRST"])
    third = _plan_item("THIRD", depends_on=["SECOND"])
    _write_plan(repo, [first, second, third])
    _write_event(
        repo,
        "FIRST",
        "completed",
        recorded_at="2026-08-25T08:00:00Z",
    )
    plan_state.write_current_view(repo, plan_state.load_snapshot(repo))
    _commit_all(repo, "计划基线")

    replacement = _plan_item("REPLACEMENT", depends_on=["FIRST"])
    refined_third = _plan_item("THIRD", depends_on=["REPLACEMENT"])
    _write_plan(repo, [first, replacement, refined_third])

    current = plan_state.load_plan(repo)
    assert plan_state.check_plan_history(repo, "HEAD", current) == []


def test_staged_plan_rewrite_cannot_be_hidden_by_safe_worktree(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    first = _plan_item("FIRST")
    _write_plan(repo, [first])
    _write_event(
        repo,
        "FIRST",
        "completed",
        recorded_at="2026-08-25T08:00:00Z",
    )
    plan_state.write_current_view(repo, plan_state.load_snapshot(repo))
    _commit_all(repo, "计划基线")

    _write_plan(repo, [_plan_item("FIRST", title="暂存改写")])
    _git(repo, "add", "--", plan_state.PLAN_PATH.as_posix())
    _write_plan(repo, [first])

    current = plan_state.load_plan(repo)
    assert plan_state.check_plan_history(repo, "HEAD", current) == [
        "已有状态事件的计划事项不得改写：FIRST（index）"
    ]


def test_plan_rewrite_cannot_be_hidden_by_later_restore(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    first = _plan_item("FIRST")
    _write_plan(repo, [first])
    _write_event(
        repo,
        "FIRST",
        "completed",
        recorded_at="2026-08-25T08:00:00Z",
    )
    plan_state.write_current_view(repo, plan_state.load_snapshot(repo))
    _commit_all(repo, "计划基线")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    _write_plan(repo, [_plan_item("FIRST", title="临时改写")])
    _commit_all(repo, "改写计划")
    rewrite_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _write_plan(repo, [first])
    _commit_all(repo, "恢复计划")

    issues = plan_state.check_plan_history(
        repo,
        base_sha,
        plan_state.load_plan(repo),
    )

    assert any(rewrite_sha[:12] in issue for issue in issues)


def test_outdated_branch_history_does_not_look_like_plan_deletion(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    first = _plan_item("FIRST")
    _write_plan(repo, [first])
    _write_event(
        repo,
        "FIRST",
        "completed",
        recorded_at="2026-08-25T08:00:00Z",
    )
    plan_state.write_current_view(repo, plan_state.load_snapshot(repo))
    _commit_all(repo, "初始计划")
    base_branch = _git(repo, "branch", "--show-current").stdout.strip()

    _git(repo, "switch", "-c", "feature")
    repo.joinpath("feature.txt").write_text("change\n", encoding="utf-8")
    _commit_all(repo, "功能分支修改")

    _git(repo, "switch", base_branch)
    second = _plan_item("SECOND", depends_on=["FIRST"])
    _write_plan(repo, [first, second])
    _write_event(
        repo,
        "SECOND",
        "completed",
        recorded_at="2026-08-25T08:01:00Z",
    )
    plan_state.write_current_view(repo, plan_state.load_snapshot(repo))
    _commit_all(repo, "主线追加计划")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    _git(repo, "merge", "--no-ff", "feature", "-m", "合并旧分支")

    assert plan_state.check_plan_history(
        repo,
        base_sha,
        plan_state.load_plan(repo),
    ) == []
    assert plan_state.check_event_history(repo, base_sha) == []


def test_new_plan_item_can_be_refined_before_it_enters_main(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    first = _plan_item("FIRST")
    _write_plan(repo, [first])
    plan_state.write_current_view(repo, plan_state.load_snapshot(repo))
    _commit_all(repo, "计划基线")
    base_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    second = _plan_item("SECOND", depends_on=["FIRST"])
    _write_plan(repo, [first, second])
    plan_state.write_current_view(repo, plan_state.load_snapshot(repo))
    _commit_all(repo, "追加计划事项")

    refined_second = _plan_item(
        "SECOND",
        depends_on=["FIRST"],
        title="确认后的标题",
    )
    _write_plan(repo, [first, refined_second])
    plan_state.write_current_view(repo, plan_state.load_snapshot(repo))
    _commit_all(repo, "修订新事项")

    assert plan_state.check_plan_history(
        repo,
        base_sha,
        plan_state.load_plan(repo),
    ) == []
