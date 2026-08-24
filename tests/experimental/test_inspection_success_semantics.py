from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vega.experimental.inspection.loop_spec import default_engineering_change_spec
from vega.experimental.inspection.reviewer import run_review
from vega.experimental.inspection.runtime import EngineeringChangeRuntime


class UnavailableLLM:
    model = "test-model"
    provider_alias = "test-provider"

    def available(self) -> bool:
        return False

    def generate_plan(self, task_text: str) -> str:
        raise AssertionError("unavailable LLM must not be called")

    def generate_report(
        self,
        task_text: str,
        tool_results: list[object],
        required_sections: list[str] | None = None,
    ) -> str:
        raise AssertionError("unavailable LLM must not be called")


def test_reviewer_does_not_treat_repeated_question_as_answer(tmp_path: Path) -> None:
    runtime = EngineeringChangeRuntime(tmp_path, llm_client=UnavailableLLM())
    task_text = "# Task\n\n问题：\n- README 是否说明安装命令？\n"
    state = _minimal_run_state(tmp_path)
    report = runtime._fallback_report(task_text, state)

    results = run_review(task_text, state, runtime.loop_spec, report)

    assert "FAIL: 报告没有覆盖 1 个任务问题" in results


def test_deterministic_fallback_with_unanswered_question_fails_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)
    task = tmp_path / "task.md"
    task.write_text(
        "# Task\n\n目标文件：\n- `README.md`\n\n问题：\n- README 是否说明安装命令？\n",
        encoding="utf-8",
    )

    run_dir = EngineeringChangeRuntime(
        workspace,
        llm_client=UnavailableLLM(),
    ).run(task, repo)

    state = _read_json(run_dir / "state.json")
    assert state["status"] == "failed"
    assert "FAIL: 报告没有覆盖 1 个任务问题" in state["review_results"]


def test_deterministic_fallback_without_explicit_questions_fails_with_artifacts(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)
    task = tmp_path / "task.md"
    task.write_text(
        "# Task\n\n目标文件：\n- `README.md`\n\n需求：\n- 检查 README 的安装命令并给出结论\n",
        encoding="utf-8",
    )

    run_dir = EngineeringChangeRuntime(
        workspace,
        llm_client=UnavailableLLM(),
    ).run(task, repo)

    state = _read_json(run_dir / "state.json")
    trace = _read_jsonl(run_dir / "trace.jsonl")
    for artifact in [
        "plan.md",
        "report.md",
        "review.md",
        "eval.md",
        "state.json",
        "trace.jsonl",
    ]:
        assert run_dir.joinpath(artifact).exists(), artifact
    assert state["status"] == "failed"
    assert state["current_step"] == "done"
    assert "FAIL: 报告明确声明未生成任务答案" in state["review_results"]
    assert "FAIL: reviewer pass 存在失败项" in state["eval_results"]
    assert [item["event"] for item in trace[-3:]] == [
        "review_written",
        "eval_written",
        "run_finished",
    ]
    assert trace[-1]["status"] == "failed"


@pytest.mark.parametrize(
    ("budget_name", "limit", "expected_operation"),
    [
        ("max_steps", 1, "plan"),
        ("max_tool_calls", 1, "file.search:TODO"),
        ("max_minutes", 0, "task_loaded"),
    ],
)
def test_engineering_change_enforces_runtime_budgets_before_excess_work(
    tmp_path: Path,
    budget_name: str,
    limit: int,
    expected_operation: str,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(repo)
    task = tmp_path / "task.md"
    task.write_text(
        "# Task\n\n目标文件：\n- `README.md`\n\n问题：\n- README 是否说明安装命令？\n",
        encoding="utf-8",
    )
    spec = default_engineering_change_spec()
    setattr(spec.budget, budget_name, limit)

    run_dir = EngineeringChangeRuntime(
        workspace,
        loop_spec=spec,
        llm_client=UnavailableLLM(),
    ).run(task, repo)

    state = _read_json(run_dir / "state.json")
    trace = _read_jsonl(run_dir / "trace.jsonl")
    budget_event = next(item for item in trace if item["event"] == "budget_exceeded")
    assert state["status"] == "failed"
    assert state["current_step"] == "budget_exceeded"
    assert any(f"runtime budget 超限：{budget_name}" in item for item in state["eval_results"])
    assert budget_event["kind"] == budget_name
    assert budget_event["operation"] == expected_operation

    if budget_name == "max_steps":
        assert not (run_dir / "plan.md").exists()
    elif budget_name == "max_tool_calls":
        assert len(state["tool_results"]) == 1
        assert state["tool_results"][0]["tool"] == "file.read"
    else:
        assert not any(item["event"] == "task_loaded" for item in trace)
        assert state["tool_results"] == []


def _minimal_run_state(tmp_path: Path):
    from vega.models import RunState

    return RunState(
        run_id="review-test",
        loop_name="engineering-change",
        status="running",
        repo_path=str(tmp_path),
        task_file=str(tmp_path / "task.md"),
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    repo.joinpath("README.md").write_text("# Demo\n", encoding="utf-8")
    repo.joinpath(".vega.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "verification:",
                "  commands:",
                "    - python -c \"print('verification passed')\"",
                "  max_commands: 1",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    subprocess.run(
        ["git", "add", "--", "README.md", ".vega.yaml"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            "init",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
