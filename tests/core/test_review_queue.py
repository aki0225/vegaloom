from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from vega.reflect_runtime import ReflectRuntime
from vega.review_runtime import ReviewRuntime
from vega.run_status import render_run_status, run_status_payload
from vega.runner import RunnerResult


class QueueReviewer:
    def __init__(
        self,
        *,
        fail_on_call: int | None = None,
        workspace: Path | None = None,
    ) -> None:
        self.fail_on_call = fail_on_call
        self.workspace = workspace
        self.prompts: list[str] = []
        self.execution_contexts: list[object] = []
        self.live_status: list[dict[str, object]] = []

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        del repo_path, timeout_seconds
        assert sandbox == "read-only"
        self.prompts.append(prompt)
        self.execution_contexts.append(execution_context)
        if self.workspace is not None:
            self.live_status.append(
                run_status_payload(
                    self.workspace,
                    execution_context.execution_root.name,
                )
            )
        if self.fail_on_call == len(self.prompts):
            return RunnerResult(
                status="timed_out",
                output="",
                error="模拟 Reviewer 超时",
                command=["queue-reviewer"],
            )
        reviewed_files = _target_files(prompt)
        findings = []
        if len(self.prompts) == 1:
            findings.append(
                {
                    "severity": "minor",
                    "file": reviewed_files[0],
                    "line": 1,
                    "title": "保留队列 Finding",
                    "evidence": "第一项审查返回的结构化 finding。",
                    "recommendation": "最终汇总时保留。",
                }
            )
        return RunnerResult(
            status="success",
            output=json.dumps(
                {
                    "verdict": "approve",
                    "summary": "当前队列任务已检查。",
                    "findings": findings,
                    "risk_disclosures": [],
                    "reviewed_files": reviewed_files,
                    "checked_items": ["当前任务文件覆盖"],
                },
                ensure_ascii=False,
            ),
            command=["queue-reviewer"],
        )


def test_review_queue_splits_over_budget_diff_and_aggregates_coverage(
    tmp_path: Path,
) -> None:
    repo = _review_repo(tmp_path / "repo", file_count=3, diff_budget=1000)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    reflect_run = ReflectRuntime(workspace).run(repo)
    reviewer = QueueReviewer(workspace=workspace)

    review_run = ReviewRuntime(workspace, runner=reviewer).run(
        repo,
        reflect_run.name,
    )

    queue = _read_json(review_run / "review-queue.json")
    state = _read_json(review_run / "state.json")
    verdict = _read_json(review_run / "review-verdict.json")
    context = _read_json(review_run / "review-context.json")
    progress = _read_jsonl(review_run / "progress.jsonl")

    assert len(reviewer.prompts) >= 2
    assert all("worker 的完整聊天记录" in prompt for prompt in reviewer.prompts)
    assert len(
        {
            str(execution.execution_dir)
            for execution in reviewer.execution_contexts
        }
    ) == len(reviewer.execution_contexts)
    assert reviewer.live_status[0]["review_queue_status"] == "running"
    assert reviewer.live_status[0]["review_queue_completed"] == 0
    assert reviewer.live_status[0]["review_queue_total"] >= 2
    assert queue["status"] == "completed"
    assert queue["covered"] == ["src/file_0.py", "src/file_1.py", "src/file_2.py"]
    assert queue["remaining"] == []
    assert len(queue["findings"]) == 1
    assert all(item["prompt_chars"] <= 60000 for item in queue["items"])
    assert all(item["diff_chars"] <= 1000 for item in queue["items"])
    assert state["status"] == "success"
    assert verdict["verdict"] == "approve"
    assert verdict["reviewed_files"] == queue["covered"]
    assert context["truncated_sections"] == ["full_diff"]
    assert context["review_queue"]["remaining"] == []
    assert any(item["event"] == "review_queue_started" for item in progress)
    assert any(item["event"] == "review_queue_completed" for item in progress)
    assert "PASS: 完整 Diff 已由 Review Queue 分片覆盖" in (
        review_run / "eval.md"
    ).read_text(encoding="utf-8")

    payload = run_status_payload(workspace, review_run.name)
    rendered = render_run_status(workspace, review_run.name)
    assert payload["review_queue_status"] == "completed"
    assert payload["review_queue_completed"] == payload["review_queue_total"]
    assert "Review Queue：`completed`" in rendered


def test_review_queue_is_not_created_when_single_prompt_fits(
    tmp_path: Path,
) -> None:
    repo = _review_repo(tmp_path / "repo", file_count=1, diff_budget=30000)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    reflect_run = ReflectRuntime(workspace).run(repo)
    reviewer = QueueReviewer()

    review_run = ReviewRuntime(workspace, runner=reviewer).run(
        repo,
        reflect_run.name,
    )

    state = _read_json(review_run / "state.json")
    assert len(reviewer.prompts) == 1
    assert not (review_run / "review-queue.json").exists()
    assert state["status"] == "success"
    assert (
        run_status_payload(workspace, review_run.name)["review_queue_status"]
        == "not_used"
    )


def test_review_queue_splits_only_after_prompt_budget_is_exceeded(
    tmp_path: Path,
) -> None:
    repo = _review_repo(
        tmp_path / "repo",
        file_count=3,
        diff_budget=30000,
        prompt_budget=6700,
        changed_lines=20,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    reflect_run = ReflectRuntime(workspace).run(repo)
    reviewer = QueueReviewer()

    review_run = ReviewRuntime(workspace, runner=reviewer).run(
        repo,
        reflect_run.name,
    )

    queue = _read_json(review_run / "review-queue.json")
    assert queue["trigger"] == ["prompt_budget"]
    assert queue["status"] == "completed"
    assert len(reviewer.prompts) == 2
    assert all(item["prompt_chars"] <= 6700 for item in queue["items"])
    assert queue["remaining"] == []


def test_review_queue_preserves_partial_coverage_after_reviewer_timeout(
    tmp_path: Path,
) -> None:
    repo = _review_repo(tmp_path / "repo", file_count=3, diff_budget=1000)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    reflect_run = ReflectRuntime(workspace).run(repo)
    reviewer = QueueReviewer(fail_on_call=2)

    review_run = ReviewRuntime(workspace, runner=reviewer).run(
        repo,
        reflect_run.name,
    )

    queue = _read_json(review_run / "review-queue.json")
    state = _read_json(review_run / "state.json")
    verdict = _read_json(review_run / "review-verdict.json")

    assert len(reviewer.prompts) == 2
    assert queue["status"] == "blocked"
    assert queue["covered"]
    assert queue["remaining"]
    assert queue["findings"][0]["title"] == "保留队列 Finding"
    assert state["status"] == "needs_human"
    assert state["current_step"] == "timed_out"
    assert (
        run_status_payload(workspace, review_run.name)["review_queue_status"]
        == "blocked"
    )
    assert verdict["verdict"] == "needs_human"
    assert "Review Queue 未完成" in {
        item["title"] for item in verdict["findings"]
    }


def test_single_oversized_file_keeps_all_review_files_remaining(
    tmp_path: Path,
) -> None:
    repo = _review_repo(
        tmp_path / "repo",
        file_count=1,
        diff_budget=1000,
        changed_lines=80,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    reflect_run = ReflectRuntime(workspace).run(repo)
    reviewer = QueueReviewer()

    review_run = ReviewRuntime(workspace, runner=reviewer).run(
        repo,
        reflect_run.name,
    )

    queue = _read_json(review_run / "review-queue.json")
    state = _read_json(review_run / "state.json")
    progress = _read_jsonl(review_run / "progress.jsonl")

    assert reviewer.prompts == []
    assert queue["status"] == "blocked"
    assert queue["covered"] == []
    assert queue["remaining"] == ["src/file_0.py"]
    assert "单个不可拆分文件组" in queue["issue"]
    assert state["status"] == "needs_human"
    assert state["current_step"] == "evidence_truncated"
    assert any(item["event"] == "review_queue_blocked" for item in progress)


def test_required_risk_files_stay_in_one_review_task(tmp_path: Path) -> None:
    repo = _review_repo(
        tmp_path / "repo",
        file_count=3,
        diff_budget=1800,
        required_review_paths=["src/file_0.py", "src/file_1.py"],
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    reflect_run = ReflectRuntime(workspace).run(repo)
    reviewer = QueueReviewer()

    review_run = ReviewRuntime(workspace, runner=reviewer).run(
        repo,
        reflect_run.name,
    )

    queue = _read_json(review_run / "review-queue.json")
    target_groups = [item["target_files"] for item in queue["items"]]

    assert queue["status"] == "completed"
    assert ["src/file_0.py", "src/file_1.py"] in target_groups
    assert ["src/file_2.py"] in target_groups
    assert all(
        not (
            "src/file_0.py" in group
            and "src/file_1.py" not in group
        )
        for group in target_groups
    )


def _target_files(prompt: str) -> list[str]:
    section = prompt.split("## 完整变更文件清单", maxsplit=1)[1]
    section = re.split(r"\n## ", section, maxsplit=1)[0]
    return re.findall(r"(?m)^- `([^`]+)`$", section)


def _review_repo(
    path: Path,
    *,
    file_count: int,
    diff_budget: int,
    prompt_budget: int = 60000,
    changed_lines: int = 12,
    required_review_paths: list[str] | None = None,
) -> Path:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.name", "Vega Test")
    _git(path, "config", "user.email", "vega@example.invalid")
    _git(path, "config", "core.autocrlf", "false")
    (path / "src").mkdir()
    for index in range(file_count):
        (path / "src" / f"file_{index}.py").write_text(
            f"VALUE = {index}\n",
            encoding="utf-8",
            newline="\n",
        )
    config_lines = [
        "version: 1",
        "prompt_budget:",
        f"  reviewer_max_chars: {prompt_budget}",
        f"  reviewer_diff_max_chars: {diff_budget}",
    ]
    if required_review_paths:
        config_lines.extend(
            [
                "risk:",
                "  required_reviews:",
                "    - id: queue-risk",
                "      label: 队列高风险文件组",
                "      paths:",
                *[f"        - {item}" for item in required_review_paths],
            ]
        )
    (path / ".vega.yaml").write_text(
        "\n".join(config_lines)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(path, "add", ".")
    _git(path, "commit", "-m", "测试：初始化 Review Queue 仓库")
    for index in range(file_count):
        lines = [
            f"VALUE_{line:02d} = '{index}-{line:02d}-review-queue-value'"
            for line in range(changed_lines)
        ]
        (path / "src" / f"file_{index}.py").write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return path


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


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
