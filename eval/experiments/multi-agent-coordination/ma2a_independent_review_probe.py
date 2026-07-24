from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"
TEST_MODULE_PATH = REPO_ROOT / "tests" / "test_delegation_runtime_bridge.py"
PROBE_ROOT = (
    REPO_ROOT
    / ".tmp"
    / "pytest"
    / "runs"
    / f"ma2a-independent-review-probe-{os.getpid()}"
)

sys.path.insert(0, str(SRC_ROOT))

from vega.delegation import DelegationValidationContext, PlanContract  # noqa: E402
from vega.delegation_runtime import DelegationRuntimeBridge  # noqa: E402
from vega.review_runtime import render_review_context  # noqa: E402


def _load_frozen_test_helpers() -> Any:
    spec = importlib.util.spec_from_file_location(
        "ma2a_frozen_test_helpers",
        TEST_MODULE_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 MA-2A 冻结测试辅助代码")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _case_root(name: str) -> Path:
    path = PROBE_ROOT / name
    if path.exists():
        raise RuntimeError(f"复审探针目录已存在：{name}")
    path.mkdir(parents=True)
    return path


def _bridge(
    helpers: Any,
    *,
    root: Path,
    worker: Any,
    plan: PlanContract | None = None,
    context: DelegationValidationContext | None = None,
) -> tuple[Any, Path]:
    repo = helpers._init_repo(root / "repo")
    run_dir = root / "workspace" / "runs" / helpers.RUN_ID
    outcome = DelegationRuntimeBridge(
        run_dir=run_dir,
        repo_path=repo,
        worker_runner=worker,
        worker_tier="budget",
        validation_context=context or helpers._delegation_context(),
        shell_kind="posix",
        scope_gate=helpers.ArtifactProbe(),
        verification_runner=helpers.ArtifactProbe(),
    ).run(
        plan=plan or helpers._plan(),
        slice_id=helpers.SLICE_ID,
        prompt="独立复审探针输入。",
    )
    return outcome, repo


class _ControlPlaneTamperWorker:
    def __init__(self, helpers: Any) -> None:
        self._delegate = helpers.RecordingWorker()

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: Any = None,
    ) -> Any:
        result = self._delegate.run(
            prompt,
            repo_path,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
            execution_context=execution_context,
        )
        run_dir = execution_context.execution_dir.parent.parent
        plan_path = run_dir / "delegation" / "delegation-plan.json"
        plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
        plan_payload["goal"]["non_goals"] = ["Worker 已改写控制面计划。"]
        plan_path.write_text(
            json.dumps(plan_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        readiness_path = run_dir / "delegation" / "delegation-readiness.json"
        readiness_payload = json.loads(readiness_path.read_text(encoding="utf-8"))
        readiness_payload["context_sha256"] = "e" * 64
        readiness_path.write_text(
            json.dumps(readiness_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return result


class _StageNewFileWorker:
    def __init__(self, helpers: Any) -> None:
        self._helpers = helpers
        self._delegate = helpers.RecordingWorker()

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: Any = None,
    ) -> Any:
        result = self._delegate.run(
            prompt,
            repo_path,
            sandbox=sandbox,
            timeout_seconds=timeout_seconds,
            execution_context=execution_context,
        )
        repo_path.joinpath("NEW.md").write_text(
            "new file\n",
            encoding="utf-8",
            newline="\n",
        )
        self._helpers._git(repo_path, "add", "NEW.md")
        return result


def main() -> int:
    helpers = _load_frozen_test_helpers()
    PROBE_ROOT.mkdir(parents=True, exist_ok=True)
    findings: list[dict[str, Any]] = []

    recording_worker = helpers.RecordingWorker()
    live_outcome, live_repo = _bridge(
        helpers,
        root=_case_root("live-snapshot"),
        worker=recording_worker,
    )
    findings.append(
        {
            "finding": "live_workspace_snapshot_not_bound",
            "observed_status": live_outcome.status,
            "worker_calls": len(recording_worker.calls),
            "declared_head_sha": helpers._delegation_snapshot()["head_sha"],
            "actual_head_sha": helpers._git(live_repo, "rev-parse", "HEAD").strip(),
        }
    )

    tamper_outcome, _ = _bridge(
        helpers,
        root=_case_root("control-plane-tamper"),
        worker=_ControlPlaneTamperWorker(helpers),
    )
    findings.append(
        {
            "finding": "worker_control_plane_tamper_accepted",
            "observed_status": tamper_outcome.status,
            "issue_codes": tamper_outcome.issue_codes,
        }
    )

    payload = helpers._plan_payload()
    payload["task_dag"][0]["read_paths"] = ["README.md"]
    payload["task_dag"][0]["allowed_write_paths"] = ["NEW.md"]
    context_payload = helpers._delegation_context().model_dump(mode="json")
    context_payload["allowed_read_paths"] = ["README.md"]
    context_payload["allowed_write_paths"] = ["NEW.md"]
    staged_outcome, _ = _bridge(
        helpers,
        root=_case_root("staged-new-file"),
        worker=_StageNewFileWorker(helpers),
        plan=PlanContract.model_validate(payload),
        context=DelegationValidationContext.model_validate(context_payload),
    )
    findings.append(
        {
            "finding": "staged_new_file_bypasses_max_new_files",
            "observed_status": staged_outcome.status,
            "issue_codes": staged_outcome.issue_codes,
            "configured_max_new_files": 0,
        }
    )

    summary_inputs = helpers._review_inputs(
        summary={"plan_id": "PLAN-MA2A"},
        worker_chat="PRIVATE_WORKER_CHAT_MUST_NOT_APPEAR",
    )
    controlled_summary = render_review_context(summary_inputs)["delegation_summary"]
    findings.append(
        {
            "finding": "partial_delegation_summary_is_exposed",
            "controlled_summary": controlled_summary,
        }
    )

    verification_path = (
        Path(live_outcome.run_dir)
        / "delegation"
        / "verification.json"
    )
    findings.append(
        {
            "finding": "status_only_verification_artifact_is_accepted",
            "observed_status": live_outcome.status,
            "verification_artifact": json.loads(
                verification_path.read_text(encoding="utf-8")
            ),
        }
    )

    expected = {
        "live_workspace_snapshot_not_bound": "attempt_recorded",
        "worker_control_plane_tamper_accepted": "attempt_recorded",
        "staged_new_file_bypasses_max_new_files": "attempt_recorded",
    }
    by_name = {item["finding"]: item for item in findings}
    for name, status in expected.items():
        if by_name[name]["observed_status"] != status:
            raise AssertionError(f"{name} 未按 2026-07-24 复审结果复现")
    if by_name["partial_delegation_summary_is_exposed"]["controlled_summary"] != {
        "plan_id": "PLAN-MA2A"
    }:
        raise AssertionError("残缺 delegation summary 未按复审结果复现")
    if by_name["status_only_verification_artifact_is_accepted"][
        "verification_artifact"
    ] != {"schema_version": 1, "status": "passed"}:
        raise AssertionError("未绑定 verification artifact 未按复审结果复现")

    print(
        json.dumps(
            {
                "schema_version": 1,
                "review_date": "2026-07-24",
                "pid": os.getpid(),
                "result": "current_gate_gaps_reproduced",
                "findings": findings,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
