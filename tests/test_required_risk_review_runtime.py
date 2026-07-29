from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import vega.review_runtime as review_runtime_module
from vega.execution_control import RunnerExecutionContext
from vega.reflect_runtime import ReflectRuntime
from vega.review_runtime import ReviewPackRuntime, ReviewRuntime
from vega.runner import RunnerResult


class StaticReviewer:
    def __init__(
        self,
        output: str,
        *,
        status: str = "success",
        error: str | None = None,
        termination_unconfirmed: object = False,
    ) -> None:
        self.output = output
        self.status = status
        self.error = error
        self.termination_unconfirmed = termination_unconfirmed
        self.prompts: list[str] = []

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        self.prompts.append(prompt)
        return RunnerResult(
            status=self.status,
            output=self.output,
            error=self.error,
            command=["static-reviewer"],
            termination_unconfirmed=self.termination_unconfirmed,
        )


def test_complete_required_risk_reviews_are_reported_and_kept_for_human(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_risk_repo(repo)
    _modify_risk_files(repo)
    workspace.mkdir()
    reviewer = StaticReviewer(_complete_risk_verdict())
    reflect_run = ReflectRuntime(workspace).run(repo)

    review_run = ReviewRuntime(workspace, runner=reviewer).run(
        repo,
        reflect_run.name,
    )

    assert len(reviewer.prompts) == 1
    prompt = reviewer.prompts[0]
    for expected in [
        "`payment` — 支付与资金",
        "`database` — 数据库与迁移",
        "src/payments/charge.py",
        "src/payments/refund.py",
        "db/migrations/024_add_status.sql",
    ]:
        assert expected in prompt

    verdict = _read_json(review_run / "review-verdict.json")
    state = _read_json(review_run / "state.json")
    findings = (review_run / "review-findings.md").read_text(encoding="utf-8")

    assert verdict["verdict"] == "needs_human"
    assert state["status"] == "needs_human"
    assert state["current_step"] == "risk_gate_needs_human"
    assert [item["risk_id"] for item in verdict["risk_disclosures"]] == [
        "payment",
        "database",
    ]
    for expected in [
        "## 必须人工检查",
        "判断：`no_obvious_issue`",
        "调整扣款与退款的幂等处理。",
        "支付单测覆盖正常路径和重复请求。",
        "人工确认网关超时后的并发重试。",
        "新增订单状态字段并回填旧数据。",
        "迁移脚本包含回填语句和兼容读取测试。",
        "人工确认大表执行时的锁表时间。",
    ]:
        assert expected in findings


@pytest.mark.parametrize("omission", ["missing_category", "missing_file"])
def test_incomplete_required_risk_review_falls_back_for_every_category(
    tmp_path: Path,
    omission: str,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_risk_repo(repo)
    _modify_risk_files(repo)
    workspace.mkdir()
    reviewer = StaticReviewer(_incomplete_risk_verdict(omission))
    reflect_run = ReflectRuntime(workspace).run(repo)

    review_run = ReviewRuntime(workspace, runner=reviewer).run(
        repo,
        reflect_run.name,
    )

    verdict = _read_json(review_run / "review-verdict.json")
    eval_text = (review_run / "eval.md").read_text(encoding="utf-8")

    assert verdict["verdict"] == "needs_human"
    assert [item["risk_id"] for item in verdict["risk_disclosures"]] == [
        "payment",
        "database",
    ]
    assert all(
        item["assessment"] == "insufficient_evidence"
        for item in verdict["risk_disclosures"]
    )
    assert {
        location["file"]
        for item in verdict["risk_disclosures"]
        for location in item["locations"]
    } == {
        "src/payments/charge.py",
        "src/payments/refund.py",
        "db/migrations/024_add_status.sql",
    }
    assert "FAIL: required risk disclosure：" in eval_text


def test_low_risk_legacy_approve_remains_success(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(
        repo,
        config="version: 1\n",
        files={"src/core.py": "VALUE = 1\n"},
    )
    repo.joinpath("src/core.py").write_text(
        "VALUE = 2\n",
        encoding="utf-8",
        newline="\n",
    )
    workspace.mkdir()
    reviewer = StaticReviewer(
        json.dumps(
            {
                "verdict": "approve",
                "summary": "旧格式 Reviewer 未发现阻塞问题。",
                "findings": [],
                "checked_items": ["需求覆盖", "测试覆盖"],
            },
            ensure_ascii=False,
        )
    )
    reflect_run = ReflectRuntime(workspace).run(repo)

    review_run = ReviewRuntime(workspace, runner=reviewer).run(
        repo,
        reflect_run.name,
    )

    verdict = _read_json(review_run / "review-verdict.json")
    state = _read_json(review_run / "state.json")

    assert verdict["verdict"] == "approve"
    assert verdict["risk_disclosures"] == []
    assert state["status"] == "success"
    assert state["current_step"] == "done"
    assert "risk_disclosures` 必须返回空列表" in reviewer.prompts[0]
    assert '"risk_disclosures": []' in reviewer.prompts[0]


def test_low_risk_unbound_risk_disclosure_cannot_succeed(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(
        repo,
        config="version: 1\n",
        files={"src/core.py": "VALUE = 1\n"},
    )
    repo.joinpath("src/core.py").write_text(
        "VALUE = 2\n",
        encoding="utf-8",
        newline="\n",
    )
    workspace.mkdir()
    reviewer = StaticReviewer(
        json.dumps(
            {
                "verdict": "approve",
                "summary": "照抄了未被 Gate 要求的风险披露。",
                "findings": [],
                "risk_disclosures": [
                    {
                        "risk_id": "payment",
                        "assessment": "no_obvious_issue",
                        "locations": [{"file": "src/core.py", "line": 1}],
                        "change_summary": "修改普通核心逻辑。",
                        "evidence": "仅检查当前 diff。",
                        "residual_risk": "无支付变更。",
                    }
                ],
                "checked_items": ["需求覆盖"],
            },
            ensure_ascii=False,
        )
    )
    reflect_run = ReflectRuntime(workspace).run(repo)

    review_run = ReviewRuntime(workspace, runner=reviewer).run(
        repo,
        reflect_run.name,
    )

    verdict = _read_json(review_run / "review-verdict.json")
    state = _read_json(review_run / "state.json")
    findings = (review_run / "review-findings.md").read_text(encoding="utf-8")

    assert verdict["verdict"] == "needs_human"
    assert verdict["risk_disclosures"] == []
    assert verdict["findings"][-1]["title"] == "风险披露范围与 Gate 不一致"
    assert state["status"] != "success"
    assert "## 必须人工检查" not in findings


def test_review_pack_binds_required_risk_rules(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_risk_repo(repo)
    _modify_risk_files(repo)
    workspace.mkdir()
    reflect_run = ReflectRuntime(workspace).run(repo)

    review_pack = ReviewPackRuntime(workspace).run(repo, reflect_run.name)

    state = _read_json(review_pack / "state.json")
    context = _read_json(review_pack / "review-context.json")
    prompt = (review_pack / "review-prompt.md").read_text(encoding="utf-8")
    eval_text = (review_pack / "eval.md").read_text(encoding="utf-8")

    assert state["status"] == "success"
    assert context["risk_gate"]["status"] == "success"
    assert [
        item["id"]
        for item in context["risk_gate"]["result"]["required_reviews"]
    ] == ["payment", "database"]
    for marker in (
        "`payment` — 支付与资金",
        "`database` — 数据库与迁移",
        "src/payments/charge.py",
        "db/migrations/024_add_status.sql",
        '"risk_disclosures"',
    ):
        assert marker in prompt
    assert "PASS: 必审高风险已写入 Review Pack，等待逐类披露" in eval_text


def test_review_pack_risk_gate_failure_needs_human(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(
        repo,
        config="version: 1\n",
        files={"src/core.py": "VALUE = 1\n"},
    )
    repo.joinpath("src/core.py").write_text(
        "VALUE = 2\n",
        encoding="utf-8",
        newline="\n",
    )
    workspace.mkdir()
    reflect_run = ReflectRuntime(workspace).run(repo)
    monkeypatch.setattr(
        review_runtime_module,
        "_evaluate_review_risk_gate",
        lambda *_args, **_kwargs: {
            "status": "failed",
            "source_run": reflect_run.name,
            "diagnostic": "forced failure",
        },
    )

    review_pack = ReviewPackRuntime(workspace).run(repo, reflect_run.name)

    state = _read_json(review_pack / "state.json")
    eval_text = (review_pack / "eval.md").read_text(encoding="utf-8")

    assert state["status"] == "needs_human"
    assert state["current_step"] == "risk_gate_failed"
    assert "FAIL: review 风险门禁评估失败" in eval_text


@pytest.mark.parametrize(
    ("runner_status", "runner_error"),
    [
        ("skipped", None),
        ("error", "reviewer error"),
        ("timed_out", "reviewer timeout"),
        ("stopped", "reviewer stopped"),
        ("success", "reviewer reported an error"),
        ("success", ""),
    ],
)
def test_untrusted_runner_result_cannot_reuse_approve_output(
    tmp_path: Path,
    runner_status: str,
    runner_error: str | None,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(
        repo,
        config="version: 1\n",
        files={"src/core.py": "VALUE = 1\n"},
    )
    repo.joinpath("src/core.py").write_text(
        "VALUE = 2\n",
        encoding="utf-8",
        newline="\n",
    )
    workspace.mkdir()
    reviewer = StaticReviewer(
        json.dumps(
            {
                "verdict": "approve",
                "summary": "不可信 Runner 输出中的通过结论。",
                "findings": [
                    {
                        "severity": "minor",
                        "file": "src/core.py",
                        "line": 1,
                        "title": "不应被采信的部分 finding",
                        "evidence": "该结论来自未成功完成的 Runner。",
                        "recommendation": "不应进入最终 verdict。",
                    }
                ],
                "checked_items": ["不应被采信的检查项"],
            },
            ensure_ascii=False,
        ),
        status=runner_status,
        error=runner_error,
    )
    reflect_run = ReflectRuntime(workspace).run(repo)

    review_run = ReviewRuntime(workspace, runner=reviewer).run(
        repo,
        reflect_run.name,
    )

    verdict = _read_json(review_run / "review-verdict.json")
    state = _read_json(review_run / "state.json")
    eval_text = (review_run / "eval.md").read_text(encoding="utf-8")
    runner_output = (review_run / "review-runner-output.txt").read_text(
        encoding="utf-8"
    )

    assert verdict["verdict"] == "needs_human"
    assert state["status"] == "needs_human"
    assert state["runner_status"] == runner_status
    assert [item["title"] for item in verdict["findings"]] == [
        "需要人工处理 reviewer 输出"
    ]
    assert verdict["checked_items"] == ["runner-output-parse"]
    assert "FAIL: reviewer Runner 未形成可采信结论" in eval_text
    assert "PASS: runner=none 已跳过外部审查" not in eval_text
    assert "PASS: reviewer 输出 verdict 可解析" not in eval_text
    assert "不应被采信的部分 finding" in runner_output
    assert "不应被采信的检查项" in runner_output


@pytest.mark.parametrize("termination_unconfirmed", [None, 0, ""])
def test_malformed_termination_confirmation_cannot_reuse_approve_output(
    tmp_path: Path,
    termination_unconfirmed: object,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_repo(
        repo,
        config="version: 1\n",
        files={"src/core.py": "VALUE = 1\n"},
    )
    repo.joinpath("src/core.py").write_text(
        "VALUE = 2\n",
        encoding="utf-8",
        newline="\n",
    )
    workspace.mkdir()
    marker = "畸形终止确认不得进入正式 verdict"
    reviewer = StaticReviewer(
        json.dumps(
            {
                "verdict": "approve",
                "summary": marker,
                "findings": [],
                "checked_items": [marker],
            },
            ensure_ascii=False,
        ),
        termination_unconfirmed=termination_unconfirmed,
    )
    reflect_run = ReflectRuntime(workspace).run(repo)

    review_run = ReviewRuntime(workspace, runner=reviewer).run(
        repo,
        reflect_run.name,
    )

    verdict = _read_json(review_run / "review-verdict.json")
    state = _read_json(review_run / "state.json")
    eval_text = (review_run / "eval.md").read_text(encoding="utf-8")
    runner_output = (review_run / "review-runner-output.txt").read_text(
        encoding="utf-8"
    )

    assert verdict["verdict"] == "needs_human"
    assert state["status"] == "needs_human"
    assert marker not in json.dumps(verdict, ensure_ascii=False)
    assert "termination_unconfirmed_valid=false" in eval_text
    assert marker in runner_output


def test_required_risk_invalid_json_falls_back_for_every_category(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_risk_repo(repo)
    _modify_risk_files(repo)
    workspace.mkdir()
    reviewer = StaticReviewer("不是 JSON")
    reflect_run = ReflectRuntime(workspace).run(repo)

    review_run = ReviewRuntime(workspace, runner=reviewer).run(
        repo,
        reflect_run.name,
    )

    verdict = _read_json(review_run / "review-verdict.json")
    state = _read_json(review_run / "state.json")

    assert state["status"] == "needs_human"
    assert state["current_step"] == "risk_gate_needs_human"
    assert [item["risk_id"] for item in verdict["risk_disclosures"]] == [
        "payment",
        "database",
    ]
    assert all(
        item["assessment"] == "insufficient_evidence"
        for item in verdict["risk_disclosures"]
    )


def test_required_risk_issue_found_rejects_empty_finding_details(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_risk_repo(repo)
    _modify_risk_files(repo)
    workspace.mkdir()
    payload = json.loads(_complete_risk_verdict())
    payload["verdict"] = "request_changes"
    payload["risk_disclosures"][0]["assessment"] = "issue_found"
    payload["findings"] = [
        {
            "severity": "major",
            "file": "src/payments/charge.py",
            "line": 1,
            "title": "重复扣款风险",
            "evidence": "",
            "recommendation": "",
        }
    ]
    reviewer = StaticReviewer(json.dumps(payload, ensure_ascii=False))
    reflect_run = ReflectRuntime(workspace).run(repo)

    review_run = ReviewRuntime(workspace, runner=reviewer).run(
        repo,
        reflect_run.name,
    )

    verdict = _read_json(review_run / "review-verdict.json")
    state = _read_json(review_run / "state.json")
    eval_text = (review_run / "eval.md").read_text(encoding="utf-8")

    assert verdict["verdict"] == "needs_human"
    assert state["status"] == "needs_human"
    assert all(
        item["assessment"] == "insufficient_evidence"
        for item in verdict["risk_disclosures"]
    )
    assert "risk_disclosure_issue_without_finding" in eval_text


def test_required_risk_timeout_uses_real_stop_reason_and_drops_partial_output(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_risk_repo(repo)
    _modify_risk_files(repo)
    workspace.mkdir()
    reviewer = StaticReviewer(
        _complete_risk_verdict_with_finding(),
        status="timed_out",
        error="reviewer timeout",
    )
    reflect_run = ReflectRuntime(workspace).run(repo)

    review_run = ReviewRuntime(workspace, runner=reviewer).run(
        repo,
        reflect_run.name,
    )

    verdict = _read_json(review_run / "review-verdict.json")
    state = _read_json(review_run / "state.json")
    eval_text = (review_run / "eval.md").read_text(encoding="utf-8")

    assert state["status"] == "needs_human"
    assert state["current_step"] == "timed_out"
    assert [item["title"] for item in verdict["findings"]] == [
        "高风险审查证据不足"
    ]
    assert verdict["checked_items"] == ["必须披露的高风险变更"]
    assert all(
        item["assessment"] == "insufficient_evidence"
        for item in verdict["risk_disclosures"]
    )
    assert "WARN: 必审高风险披露结构完整，但审查证据不足" in eval_text
    assert "PASS: 必审高风险披露结构完整且固定交由人工确认" not in eval_text


def test_required_risk_truncation_uses_evidence_stop_reason(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    _init_risk_repo(repo, reviewer_diff_max_chars=1000)
    _modify_risk_files(repo)
    repo.joinpath("src/payments/charge.py").write_text(
        "\n".join(f"VALUE_{index} = {index}" for index in range(400)) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    workspace.mkdir()
    reviewer = StaticReviewer(_complete_risk_verdict())
    reflect_run = ReflectRuntime(workspace).run(repo)

    review_run = ReviewRuntime(workspace, runner=reviewer).run(
        repo,
        reflect_run.name,
    )

    verdict = _read_json(review_run / "review-verdict.json")
    state = _read_json(review_run / "state.json")
    context = _read_json(review_run / "review-context.json")

    assert context["truncated_sections"] == ["full_diff"]
    assert state["status"] == "needs_human"
    assert state["current_step"] == "evidence_truncated"
    assert all(
        item["assessment"] == "insufficient_evidence"
        for item in verdict["risk_disclosures"]
    )


def _complete_risk_verdict() -> str:
    return json.dumps(
        {
            "verdict": "approve",
            "summary": "两类高风险均已检查，最终交由人工确认。",
            "findings": [],
            "risk_disclosures": [
                {
                    "risk_id": "payment",
                    "assessment": "no_obvious_issue",
                    "locations": [
                        {"file": "src/payments/charge.py", "line": 1},
                        {"file": "src/payments/refund.py", "line": 1},
                    ],
                    "change_summary": "调整扣款与退款的幂等处理。",
                    "evidence": "支付单测覆盖正常路径和重复请求。",
                    "residual_risk": "人工确认网关超时后的并发重试。",
                },
                {
                    "risk_id": "database",
                    "assessment": "no_obvious_issue",
                    "locations": [
                        {
                            "file": "db/migrations/024_add_status.sql",
                            "line": 1,
                        }
                    ],
                    "change_summary": "新增订单状态字段并回填旧数据。",
                    "evidence": "迁移脚本包含回填语句和兼容读取测试。",
                    "residual_risk": "人工确认大表执行时的锁表时间。",
                },
            ],
            "checked_items": ["支付风险", "数据库迁移", "测试覆盖"],
        },
        ensure_ascii=False,
    )


def _complete_risk_verdict_with_finding() -> str:
    payload = json.loads(_complete_risk_verdict())
    payload["findings"] = [
        {
            "severity": "minor",
            "file": "src/payments/charge.py",
            "line": 1,
            "title": "未完成 Runner 的部分判断",
            "evidence": "该内容来自超时前的部分输出。",
            "recommendation": "不能直接采信。",
        }
    ]
    return json.dumps(payload, ensure_ascii=False)


def _incomplete_risk_verdict(omission: str) -> str:
    payment_locations = [{"file": "src/payments/charge.py", "line": 1}]
    if omission == "missing_category":
        payment_locations.append({"file": "src/payments/refund.py", "line": 1})
    disclosures = [
        {
            "risk_id": "payment",
            "assessment": "no_obvious_issue",
            "locations": payment_locations,
            "change_summary": "调整支付处理。",
            "evidence": "检查了支付相关 diff。",
            "residual_risk": "仍需人工复核。",
        }
    ]
    if omission == "missing_file":
        disclosures.append(
            {
                "risk_id": "database",
                "assessment": "no_obvious_issue",
                "locations": [
                    {
                        "file": "db/migrations/024_add_status.sql",
                        "line": 1,
                    }
                ],
                "change_summary": "修改数据库迁移。",
                "evidence": "检查了迁移 diff。",
                "residual_risk": "仍需人工复核。",
            }
        )
    return json.dumps(
        {
            "verdict": "approve",
            "summary": "Reviewer 返回了不完整的高风险披露。",
            "findings": [],
            "risk_disclosures": disclosures,
            "checked_items": ["风险审查"],
        },
        ensure_ascii=False,
    )


def _init_risk_repo(
    repo: Path,
    *,
    reviewer_diff_max_chars: int | None = None,
) -> None:
    prompt_budget = (
        [
            "prompt_budget:",
            "  reviewer_max_chars: 60000",
            f"  reviewer_diff_max_chars: {reviewer_diff_max_chars}",
        ]
        if reviewer_diff_max_chars is not None
        else []
    )
    _init_repo(
        repo,
        config="\n".join(
            [
                "version: 1",
                *prompt_budget,
                "risk:",
                "  required_reviews:",
                "    - id: payment",
                "      label: 支付与资金",
                "      paths:",
                "        - src/payments/**",
                "    - id: database",
                "      label: 数据库与迁移",
                "      paths:",
                "        - db/migrations/**",
            ]
        )
        + "\n",
        files={
            "src/payments/charge.py": "VALUE = 1\n",
            "src/payments/refund.py": "VALUE = 1\n",
            "db/migrations/024_add_status.sql": "SELECT 1;\n",
        },
    )


def _modify_risk_files(repo: Path) -> None:
    updates = {
        "src/payments/charge.py": "VALUE = 2\n",
        "src/payments/refund.py": "VALUE = 2\n",
        "db/migrations/024_add_status.sql": "SELECT 2;\n",
    }
    for relative_path, content in updates.items():
        repo.joinpath(relative_path).write_text(
            content,
            encoding="utf-8",
            newline="\n",
        )


def _init_repo(repo: Path, *, config: str, files: dict[str, str]) -> None:
    repo.mkdir(parents=True)
    subprocess.run(
        ["git", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    repo.joinpath(".vega.yaml").write_text(
        config,
        encoding="utf-8",
        newline="\n",
    )
    repo.joinpath("AGENTS.md").write_text(
        "# Rules\n\n- 高风险变更必须明确披露并交由人工确认。\n",
        encoding="utf-8",
        newline="\n",
    )
    for relative_path, content in files.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    subprocess.run(
        ["git", "add", "."],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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
            "初始化测试仓库",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
