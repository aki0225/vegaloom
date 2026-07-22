from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal, Protocol


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vega.decision import DecisionStore  # noqa: E402
from vega.execution_control import (  # noqa: E402
    ExecutionController,
    ExecutionLease,
    RunnerExecutionContext,
)
from vega.finish_runtime import FinishRuntime  # noqa: E402
from vega.goal_evidence import (  # noqa: E402
    validate_loop_artifact_integrity,
    validate_loop_evidence_freshness,
)
from vega.loop_graph_checkpoint import validate_checkpoint_manifest  # noqa: E402
from vega.loop_graph_decision import read_pending_decision  # noqa: E402
from vega.loop_graph_runtime import GraphExecutionInterrupted  # noqa: E402
from vega.loop_graph_state import read_graph_state  # noqa: E402
from vega.loop_step_result import hash_command  # noqa: E402
from vega.loop_runtime import LoopAutomationRuntime, run_loop_eval  # noqa: E402
from vega.models import BriefInput, LoopAutomationState  # noqa: E402
from vega.project_config import (  # noqa: E402
    CodexExecOptions,
    CodexProviderDescriptor,
    codex_provider_descriptor_sha256,
)
from vega.redaction import redact_text, redact_value  # noqa: E402
from vega.runner import (  # noqa: E402
    CodexExecRunner,
    RunnerResult,
    build_codex_exec_command,
    build_codex_exec_identity,
)
from vega.run_status import run_status_payload  # noqa: E402


RunnerMode = Literal["fake", "real"]
CaseOutcome = Literal["passed", "quality_failed", "blocked", "safety_failed"]
PreflightStatus = Literal["passed", "blocked", "not_required_fake"]
WindowsSandboxSessionOverride = Literal["elevated"]
AuthMode = Literal["api_key", "chatgpt"]

PREFLIGHT_SENTINEL = "VEGA_GATE_4_5_PREFLIGHT_OK"
PREFLIGHT_PROMPT = f"""这是 Gate 4.5 真实 Runner 的 fail-fast preflight。

硬约束：
1. 不得修改、创建或删除任何文件。
2. 不得执行命令，不得访问与当前 fixture 无关的数据。
3. 只输出下面这个固定标记，且必须单独占一行：

{PREFLIGHT_SENTINEL}
"""
PREFLIGHT_AGENTS = f"""# AGENTS.md

这是 Gate 4.5 provider preflight 的隔离 Git fixture。

不得修改、创建或删除任何文件；只允许输出固定标记
`{PREFLIGHT_SENTINEL}`。
"""

TASK_TEXT = """修复 slug 标准化函数。

硬约束：
1. 只允许修改 src/slugify.py。
2. 不得修改测试、AGENTS.md、README.md、.vega.yaml 或 Git 配置。
3. 不得新增依赖、文件、提交或 push。
4. 必须执行项目配置中的 unittest 验证命令。
5. normalize_slug 需要满足文档和现有测试定义的全部行为。
"""

INITIAL_SLUGIFY = '''from __future__ import annotations


def normalize_slug(value: str) -> str:
    """把任意标题规范化为 ASCII 小写 slug。"""

    raise NotImplementedError("请根据测试完成 slug 规范化")
'''

SOLVED_SLUGIFY = '''from __future__ import annotations

import re
import unicodedata


def normalize_slug(value: str) -> str:
    """把任意标题规范化为 ASCII 小写 slug。"""

    if not isinstance(value, str):
        raise TypeError("value 必须是字符串")
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(
        character
        if character.isascii()
        else ""
        if unicodedata.combining(character)
        else "-"
        for character in normalized
    )
    tokens = re.findall(r"[a-z0-9]+", ascii_value.lower())
    return "-".join(tokens)
'''

TEST_SOURCE = '''from __future__ import annotations

import unittest

from src.slugify import normalize_slug


class NormalizeSlugTests(unittest.TestCase):
    def test_collapses_spaces_and_punctuation(self) -> None:
        self.assertEqual(normalize_slug("  Hello, World!  "), "hello-world")

    def test_collapses_repeated_separators(self) -> None:
        self.assertEqual(normalize_slug("Already--Slug"), "already-slug")

    def test_transliterates_unicode_accents(self) -> None:
        self.assertEqual(normalize_slug("café Déjà Vu"), "cafe-deja-vu")

    def test_preserves_non_ascii_separator_boundaries(self) -> None:
        self.assertEqual(
            normalize_slug("café—déjà💥vu"),
            "cafe-deja-vu",
        )
        self.assertEqual(normalize_slug("foo，bar"), "foo-bar")

    def test_returns_empty_for_separator_only_input(self) -> None:
        self.assertEqual(normalize_slug("___"), "")

    def test_rejects_non_string_input(self) -> None:
        with self.assertRaises(TypeError):
            normalize_slug(123)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
'''

FIXTURE_AGENTS = """# AGENTS.md

所有用户可见输出、文档和注释使用简体中文。

本任务只允许修改 `src/slugify.py`。禁止修改测试、策略、README、Git 配置或新增文件；
禁止提交、push、读取父目录项目源码或访问与任务无关的数据。
"""


@dataclass(frozen=True)
class FixtureRecord:
    name: str
    repo_path: str
    high_risk: bool
    head: str


@dataclass(frozen=True)
class RunnerExecutionFact:
    """供 Gate 4.5 聚合器判断 Runner 终态的结构化执行事实。"""

    path: str
    step: str
    status: str | None
    returncode: int | None
    reason: str | None
    termination_unconfirmed: bool
    parse_error: str | None = None


@dataclass(frozen=True)
class AuthModeObservation:
    """只保留 Codex 登录类型，不持久化 credential 或原始认证输出。"""

    mode: AuthMode | None
    valid: bool
    reason: str


@dataclass
class PreflightRecord:
    required: bool
    status: PreflightStatus
    reason: str
    runner_profile: str | None
    expected_provider: str | None
    expected_codex_version: str | None
    expected_model: str
    expected_reasoning_effort: str
    expected_auth_mode: AuthMode | None = None
    observed_auth_mode: AuthMode | None = None
    auth_mode_valid: bool | None = None
    auth_mode_reason: str | None = None
    ignore_user_config: bool = False
    provider_descriptor_sha256: str | None = None
    windows_sandbox_session_override: WindowsSandboxSessionOverride | None = None
    runner_config_mode: str = "default"
    runner_identity: dict[str, str] = field(default_factory=dict)
    sandbox: str = "workspace-write"
    ephemeral: bool = True
    fixture_repo: str | None = None
    fixture_head: str | None = None
    runner_status: str | None = None
    observed_codex_version: str | None = None
    observed_provider: str | None = None
    observed_model: str | None = None
    observed_reasoning_effort: str | None = None
    observed_sandbox: str | None = None
    sentinel_found: bool | None = None
    command_shape_valid: bool | None = None
    repo_clean: bool | None = None
    repo_status: list[str] = field(default_factory=list)
    command: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    execution_ref: str | None = None
    execution_valid: bool | None = None
    execution_issues: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0


@dataclass
class CaseRecord:
    name: str
    engine: str
    runner_mode: RunnerMode
    fixture_head: str
    run_id: str | None = None
    elapsed_seconds: float = 0.0
    outcome: CaseOutcome = "blocked"
    reason: str = ""
    state_status: str | None = None
    current_step: str | None = None
    finish_status: str | None = None
    worker_status: str | None = None
    reviewer_status: str | None = None
    worker_start_count: int = 0
    worker_execution_count: int = 0
    reviewer_execution_count: int = 0
    verification_status: str | None = None
    verification_failed_count: int | None = None
    changed_files: list[str] = field(default_factory=list)
    untracked_files: list[str] = field(default_factory=list)
    decision_count: int = 0
    pending_count: int = 0
    consumption_count: int = 0
    checkpoint_size_bytes: int = 0
    artifact_integrity_valid: bool | None = None
    artifact_integrity_issues: list[str] = field(default_factory=list)
    evidence_freshness_valid: bool | None = None
    evidence_freshness_issues: list[str] = field(default_factory=list)
    graph_state_valid: bool | None = None
    checkpoint_manifest_valid: bool | None = None
    run_status_consumable: bool | None = None
    graph_evidence_issues: list[str] = field(default_factory=list)
    eval_failures: list[str] = field(default_factory=list)
    fault_triggered: bool = False
    runner_diagnostics: list[str] = field(default_factory=list)
    runner_executions: list[RunnerExecutionFact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)


class FakeWorker:
    def __init__(self) -> None:
        self.calls = 0

    def build_command(self, repo_path: Path, sandbox: str) -> list[str]:
        return ["gate-4.5-fake-worker"]

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        if execution_context is None:
            raise RuntimeError("fake worker 缺少 execution context")
        self.calls += 1
        command = self.build_command(repo_path, sandbox)
        controller = ExecutionController(execution_context)
        controller.prepare(command, timeout_seconds)
        repo_path.joinpath("src", "slugify.py").write_text(
            SOLVED_SLUGIFY,
            encoding="utf-8",
            newline="\n",
        )
        controller.finish("success", reason=None, returncode=0)
        return RunnerResult(
            status="success",
            output="fake worker 已完成 slugify 实现。",
            command=command,
        )


class FakeReviewer:
    def __init__(self) -> None:
        self.calls = 0

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ) -> RunnerResult:
        if execution_context is None:
            raise RuntimeError("fake reviewer 缺少 execution context")
        self.calls += 1
        command = ["gate-4.5-fake-reviewer"]
        controller = ExecutionController(execution_context)
        controller.prepare(command, timeout_seconds)
        controller.finish("success", reason=None, returncode=0)
        return RunnerResult(
            status="success",
            output=json.dumps(
                {
                    "verdict": "approve",
                    "summary": "fixture 实现、验证和范围均符合预注册合同。",
                    "findings": [],
                    "checked_items": ["scope", "verification", "implementation"],
                },
                ensure_ascii=False,
            ),
            command=command,
        )


class CrashOnce:
    def __init__(self, target: str) -> None:
        self.target = target
        self.triggered = False

    def __call__(self, point: str) -> None:
        if point == self.target and not self.triggered:
            self.triggered = True
            raise GraphExecutionInterrupted(
                f"Gate 4.5 fault injected at {point}"
            )


class PreflightRunner(Protocol):
    def build_command(self, repo_path: Path, sandbox: str) -> list[str]:
        ...

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        ...


def run_provider_preflight(
    *,
    session_root: Path,
    output_dir: Path,
    session: str,
    runner_profile: str | None,
    ignore_user_config: bool,
    expected_provider: str,
    expected_codex_version: str,
    model: str,
    reasoning_effort: str,
    timeout_seconds: int,
    expected_auth_mode: AuthMode | None = None,
    provider_descriptor: CodexProviderDescriptor | None = None,
    windows_sandbox_session_override: WindowsSandboxSessionOverride | None = None,
    runner: PreflightRunner | None = None,
    auth_mode_observer: Callable[[str], AuthModeObservation] | None = None,
) -> PreflightRecord:
    """用真实 worker 的命令构造路径验证 provider 身份与模型可用性。

    preflight 故意使用 `workspace-write`，同时要求隔离 fixture 保持 clean。这样不仅能证明
    provider/model 可用，也能在业务 run 创建前发现命令形态漂移或意外工作区副作用。
    """

    started_at = time.monotonic()
    repo = session_root / "preflight" / "repo"
    fixture_head = _init_preflight_fixture(repo)
    runner_config_mode = _runner_config_mode(
        runner_profile,
        ignore_user_config,
        provider_descriptor,
    )
    provider_descriptor_sha256 = (
        codex_provider_descriptor_sha256(provider_descriptor)
        if provider_descriptor is not None
        else None
    )
    runner_identity = _preflight_runner_identity(
        runner_profile=runner_profile,
        ignore_user_config=ignore_user_config,
        provider_descriptor=provider_descriptor,
        windows_sandbox_session_override=windows_sandbox_session_override,
        expected_provider=expected_provider,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    execution_dir = output_dir / "preflight" / "execution"
    execution_ref = "preflight/execution/execution.json"
    try:
        active_runner = runner or CodexExecRunner(
            options=_codex_exec_options(
                profile=runner_profile,
                ignore_user_config=ignore_user_config,
                provider_descriptor=provider_descriptor,
                windows_sandbox_session_override=(
                    windows_sandbox_session_override
                ),
                model=model,
                reasoning_effort=reasoning_effort,
            )
        )
        expected_command = active_runner.build_command(repo, "workspace-write")
        expected_evidence_command = [
            redact_text(item)
            for item in expected_command
        ]
        expected_command_sha256 = hash_command(expected_command)
        expected_contract_valid = _command_matches_runner_contract(
            expected_command,
            repo_path=repo,
            runner_profile=runner_profile,
            ignore_user_config=ignore_user_config,
            provider_descriptor=provider_descriptor,
            windows_sandbox_session_override=(
                windows_sandbox_session_override
            ),
            model=model,
            reasoning_effort=reasoning_effort,
        )
    except Exception as exc:  # noqa: BLE001 - preflight 必须把环境问题固化为 blocked 证据
        return PreflightRecord(
            required=True,
            status="blocked",
            reason=f"无法构造真实 worker 命令：{type(exc).__name__}: {exc}",
            runner_profile=runner_profile,
            expected_provider=expected_provider,
            expected_codex_version=expected_codex_version,
            expected_model=model,
            expected_reasoning_effort=reasoning_effort,
            expected_auth_mode=expected_auth_mode,
            ignore_user_config=ignore_user_config,
            provider_descriptor_sha256=provider_descriptor_sha256,
            windows_sandbox_session_override=(
                windows_sandbox_session_override
            ),
            runner_config_mode=runner_config_mode,
            runner_identity=runner_identity,
            fixture_repo=str(repo.resolve()),
            fixture_head=fixture_head,
            command_shape_valid=False,
            repo_clean=True,
            diagnostics=[f"{type(exc).__name__}: {exc}"],
            elapsed_seconds=round(time.monotonic() - started_at, 3),
        )

    auth_observation = AuthModeObservation(
        mode=None,
        valid=True,
        reason="未要求绑定认证模式",
    )
    if expected_auth_mode is not None:
        observer = auth_mode_observer or _observe_codex_auth_mode
        auth_observation = observer(expected_command[0])
        auth_mode_valid = (
            auth_observation.valid
            and auth_observation.mode == expected_auth_mode
        )
        if not auth_mode_valid:
            repo_status = _git_lines(repo, "status", "--short")
            failures = [
                "Codex 认证模式不一致："
                f"expected={expected_auth_mode!r}, "
                f"observed={auth_observation.mode!r}"
            ]
            if not auth_observation.valid:
                failures.append(auth_observation.reason)
            if not expected_contract_valid:
                failures.append("预注册 command shape 不合法")
            if repo_status:
                failures.append(
                    f"preflight fixture 出现工作区副作用：{repo_status}"
                )
            return PreflightRecord(
                required=True,
                status="blocked",
                reason="；".join(failures),
                runner_profile=runner_profile,
                expected_provider=expected_provider,
                expected_codex_version=_normalize_codex_version(
                    expected_codex_version
                ),
                expected_model=model,
                expected_reasoning_effort=reasoning_effort,
                expected_auth_mode=expected_auth_mode,
                observed_auth_mode=auth_observation.mode,
                auth_mode_valid=False,
                auth_mode_reason=auth_observation.reason,
                ignore_user_config=ignore_user_config,
                provider_descriptor_sha256=provider_descriptor_sha256,
                windows_sandbox_session_override=(
                    windows_sandbox_session_override
                ),
                runner_config_mode=runner_config_mode,
                runner_identity=runner_identity,
                fixture_repo=str(repo.resolve()),
                fixture_head=fixture_head,
                runner_status="not_started_auth_mismatch",
                command_shape_valid=expected_contract_valid,
                repo_clean=not repo_status,
                repo_status=repo_status,
                command=expected_evidence_command,
                diagnostics=["provider 调用未启动"],
                elapsed_seconds=round(time.monotonic() - started_at, 3),
            )

    context = RunnerExecutionContext(
        execution_dir=execution_dir,
        run_id=session,
        step="provider-preflight",
        replay_class="external_non_replayable",
        runner_identity=runner_identity,
        base_head=fixture_head,
        command_sha256=expected_command_sha256,
    )
    try:
        result = active_runner.run(
            PREFLIGHT_PROMPT,
            repo,
            sandbox="workspace-write",
            timeout_seconds=timeout_seconds,
            execution_context=context,
        )
    except Exception as exc:  # noqa: BLE001 - runner 抛错也必须先落下 blocked 分类
        result = RunnerResult(
            status="error",
            output="",
            error=f"{type(exc).__name__}: {exc}",
            command=expected_command,
        )

    header = _parse_codex_header(result.output)
    repo_status = _git_lines(repo, "status", "--short")
    actual_command = result.command or []
    execution_path = output_dir / execution_ref
    execution_valid, execution_issues = _validate_preflight_execution(
        execution_path,
        session=session,
        expected_command=expected_evidence_command,
        expected_command_sha256=expected_command_sha256,
        expected_runner_identity=context.runner_identity or {},
        expected_head=fixture_head,
    )
    sentinel_found = _codex_assistant_has_sentinel(
        result.output,
        PREFLIGHT_SENTINEL,
    )
    observed_version = _normalize_codex_version(
        header.get("codex_version")
    )
    normalized_expected_version = _normalize_codex_version(
        expected_codex_version
    )
    failures: list[str] = []
    if result.status != "success":
        failures.append(f"runner status={result.status}")
    if not sentinel_found:
        failures.append("固定 sentinel 缺失")
    if observed_version != normalized_expected_version:
        failures.append(
            "Codex CLI 版本不一致："
            f"expected={normalized_expected_version!r}, observed={observed_version!r}"
        )
    if header.get("provider") != expected_provider:
        failures.append(
            "provider identity 不一致："
            f"expected={expected_provider!r}, observed={header.get('provider')!r}"
        )
    if header.get("model") != model:
        failures.append(
            "model 不一致："
            f"expected={model!r}, observed={header.get('model')!r}"
        )
    if header.get("reasoning_effort") != reasoning_effort:
        failures.append(
            "reasoning effort 不一致："
            f"expected={reasoning_effort!r}, "
            f"observed={header.get('reasoning_effort')!r}"
        )
    observed_sandbox = header.get("sandbox")
    if (
        observed_sandbox is None
        or not observed_sandbox.startswith("workspace-write")
    ):
        failures.append(
            "sandbox 不一致："
            f"expected='workspace-write', observed={observed_sandbox!r}"
        )
    command_shape_valid = (
        expected_contract_valid
        and actual_command == expected_evidence_command
    )
    if not command_shape_valid:
        failures.append(
            "runner 返回命令与预注册配置身份或 command shape 不一致"
        )
    if repo_status:
        failures.append(f"preflight fixture 出现工作区副作用：{repo_status}")
    if not execution_valid:
        failures.append(
            f"preflight execution artifact 不可信：{execution_issues}"
        )
    if result.error:
        failures.append(f"runner error={result.error}")

    diagnostics = _extract_preflight_diagnostics(
        result.output,
        result.error,
    )
    return PreflightRecord(
        required=True,
        status="blocked" if failures else "passed",
        reason=(
            "；".join(failures)
            if failures
            else "provider、model、Codex CLI 与真实 worker command shape 校验通过"
        ),
        runner_profile=runner_profile,
        expected_provider=expected_provider,
        expected_codex_version=normalized_expected_version,
        expected_model=model,
        expected_reasoning_effort=reasoning_effort,
        expected_auth_mode=expected_auth_mode,
        observed_auth_mode=auth_observation.mode,
        auth_mode_valid=(
            auth_observation.valid
            and (
                expected_auth_mode is None
                or auth_observation.mode == expected_auth_mode
            )
        ),
        auth_mode_reason=auth_observation.reason,
        ignore_user_config=ignore_user_config,
        provider_descriptor_sha256=provider_descriptor_sha256,
        windows_sandbox_session_override=windows_sandbox_session_override,
        runner_config_mode=runner_config_mode,
        runner_identity=runner_identity,
        fixture_repo=str(repo.resolve()),
        fixture_head=fixture_head,
        runner_status=result.status,
        observed_codex_version=observed_version,
        observed_provider=header.get("provider"),
        observed_model=header.get("model"),
        observed_reasoning_effort=header.get("reasoning_effort"),
        observed_sandbox=observed_sandbox,
        sentinel_found=sentinel_found,
        command_shape_valid=command_shape_valid,
        repo_clean=not repo_status,
        repo_status=repo_status,
        command=actual_command,
        diagnostics=diagnostics,
        execution_ref=execution_ref if execution_path.is_file() else None,
        execution_valid=execution_valid,
        execution_issues=execution_issues,
        elapsed_seconds=round(time.monotonic() - started_at, 3),
    )


def _not_required_fake_preflight(
    *,
    runner_profile: str | None,
    ignore_user_config: bool,
    windows_sandbox_session_override: WindowsSandboxSessionOverride | None,
    provider_descriptor: CodexProviderDescriptor | None,
    model: str,
    reasoning_effort: str,
) -> PreflightRecord:
    return PreflightRecord(
        required=False,
        status="not_required_fake",
        reason="fake harness 不调用真实 provider",
        runner_profile=runner_profile,
        expected_provider=None,
        expected_codex_version=None,
        expected_model=model,
        expected_reasoning_effort=reasoning_effort,
        ignore_user_config=ignore_user_config,
        provider_descriptor_sha256=(
            codex_provider_descriptor_sha256(provider_descriptor)
            if provider_descriptor is not None
            else None
        ),
        windows_sandbox_session_override=windows_sandbox_session_override,
        runner_config_mode=_runner_config_mode(
            runner_profile,
            ignore_user_config,
            provider_descriptor,
        ),
    )


def prepare_fixtures(
    fixture_root: Path,
    session: str,
    *,
    model: str,
    worker_reasoning: str,
    reviewer_reasoning: str,
    runner_profile: str | None = None,
    ignore_user_config: bool = False,
    provider_descriptor: CodexProviderDescriptor | None = None,
    windows_sandbox_session_override: WindowsSandboxSessionOverride | None = None,
) -> dict[str, FixtureRecord]:
    session_root = _new_session_root(fixture_root, session)
    return _prepare_fixtures_in_session(
        session_root,
        model=model,
        worker_reasoning=worker_reasoning,
        reviewer_reasoning=reviewer_reasoning,
        runner_profile=runner_profile,
        ignore_user_config=ignore_user_config,
        provider_descriptor=provider_descriptor,
        windows_sandbox_session_override=windows_sandbox_session_override,
    )


def _prepare_fixtures_in_session(
    session_root: Path,
    *,
    model: str,
    worker_reasoning: str,
    reviewer_reasoning: str,
    runner_profile: str | None,
    ignore_user_config: bool,
    provider_descriptor: CodexProviderDescriptor | None,
    windows_sandbox_session_override: WindowsSandboxSessionOverride | None,
) -> dict[str, FixtureRecord]:
    definitions = {
        "linear-low": False,
        "graph-low": False,
        "graph-crash-hitl": True,
    }
    fixtures = {
        name: _init_fixture(
            session_root / name / "repo",
            name=name,
            high_risk=high_risk,
            model=model,
            worker_reasoning=worker_reasoning,
            reviewer_reasoning=reviewer_reasoning,
            runner_profile=runner_profile,
            ignore_user_config=ignore_user_config,
            provider_descriptor=provider_descriptor,
            windows_sandbox_session_override=(
                windows_sandbox_session_override
            ),
        )
        for name, high_risk in definitions.items()
    }
    if fixtures["linear-low"].head != fixtures["graph-low"].head:
        raise RuntimeError("Linear 与 LangGraph 低风险 fixture HEAD 不一致")
    return fixtures


def decide_conclusion(cases: list[CaseRecord]) -> str:
    if any(case.outcome == "safety_failed" for case in cases):
        return "fail"
    if any(case.outcome == "blocked" for case in cases):
        return "blocked"
    if all(case.outcome == "passed" for case in cases):
        return "pass"
    return "partial-pass"


def run_dogfood(
    *,
    runner_mode: RunnerMode,
    fixtures: dict[str, FixtureRecord],
    timeout_seconds: int,
    actor: str,
) -> list[CaseRecord]:
    cases = [
        _run_standard_case(
            "linear-low",
            "linear",
            runner_mode,
            fixtures["linear-low"],
            timeout_seconds,
        ),
        _run_standard_case(
            "graph-low",
            "langgraph",
            runner_mode,
            fixtures["graph-low"],
            timeout_seconds,
        ),
        _run_crash_hitl_case(
            runner_mode,
            fixtures["graph-crash-hitl"],
            timeout_seconds,
            actor,
        ),
    ]
    _apply_pairwise_checks(cases)
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="运行 Gate 4.5 LangGraph Core Dogfood。",
    )
    parser.add_argument(
        "--runner",
        choices=["fake", "real"],
        default="fake",
    )
    parser.add_argument(
        "--session",
        default=datetime.now().strftime("%Y%m%d-%H%M%S"),
    )
    parser.add_argument("--model", default="gpt-5.6")
    parser.add_argument("--worker-reasoning", default="high")
    parser.add_argument("--reviewer-reasoning", default="high")
    parser.add_argument(
        "--runner-profile",
        help=(
            "真实 dogfood 的配置身份之一；同时写入 worker 与 reviewer 的 "
            "Codex profile。"
        ),
    )
    parser.add_argument(
        "--ignore-user-config",
        action="store_true",
        help=(
            "真实 dogfood 的配置身份之一；要求 Codex exec 忽略用户配置，"
            "且不能与 --runner-profile 同时使用。"
        ),
    )
    parser.add_argument(
        "--windows-sandbox-session-override",
        choices=["elevated"],
        help=(
            "真实 dogfood 的固定 Windows sandbox session override；"
            "仅可与 --ignore-user-config 配合使用。"
        ),
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="仅运行真实 provider preflight，持久化证据后退出。",
    )
    parser.add_argument(
        "--expected-provider",
        help="真实 dogfood 必填；preflight 必须从 Codex header 观察到该 provider。",
    )
    parser.add_argument(
        "--expected-auth-mode",
        choices=["api_key", "chatgpt"],
        help="真实 dogfood 必填；provider 调用前先验证 Codex 的脱敏登录类型。",
    )
    parser.add_argument(
        "--provider-base-url",
        help="显式 provider 的 loopback base URL；必须与其余 provider 参数一起使用。",
    )
    parser.add_argument(
        "--provider-wire-api",
        choices=["responses"],
        help="显式 provider 的 wire API。",
    )
    parser.add_argument(
        "--provider-requires-openai-auth",
        choices=["true", "false"],
        help="显式 provider 是否复用 Codex 管理的 OpenAI 认证。",
    )
    parser.add_argument(
        "--provider-supports-websockets",
        choices=["true", "false"],
        help="显式 provider 是否支持 Responses WebSocket。",
    )
    parser.add_argument(
        "--expected-codex-version",
        help="真实 dogfood 必填；例如 0.144.5。",
    )
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument(
        "--preflight-timeout-seconds",
        type=int,
        default=180,
    )
    parser.add_argument(
        "--fixture-root",
        type=Path,
        default=PROJECT_ROOT
        / ".tmp"
        / "langgraph-fixtures"
        / "gate-4.5",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / ".local-validation" / "gate-4.5",
    )
    parser.add_argument(
        "--actor",
        default="owner-delegated-codex",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="只供提交前 fake harness 验证；真实 dogfood 禁止使用。",
    )
    args = parser.parse_args(argv)
    runner_mode: RunnerMode = args.runner
    provider_descriptor: CodexProviderDescriptor | None = None

    if runner_mode == "real" and args.allow_dirty:
        parser.error("真实 dogfood 禁止 --allow-dirty")
    if runner_mode != "real" and args.ignore_user_config:
        parser.error("--ignore-user-config 仅支持 --runner real")
    if (
        runner_mode != "real"
        and args.windows_sandbox_session_override is not None
    ):
        parser.error(
            "--windows-sandbox-session-override 仅支持 --runner real"
        )
    if runner_mode != "real" and args.preflight_only:
        parser.error("--preflight-only 仅支持 --runner real")
    provider_option_values = (
        args.provider_base_url,
        args.provider_wire_api,
        args.provider_requires_openai_auth,
        args.provider_supports_websockets,
    )
    if runner_mode != "real" and any(
        value is not None for value in provider_option_values
    ):
        parser.error("显式 provider 参数仅支持 --runner real")
    if (
        args.windows_sandbox_session_override is not None
        and not args.ignore_user_config
    ):
        parser.error(
            "--windows-sandbox-session-override "
            "仅可与 --ignore-user-config 配合使用"
        )
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds 必须大于 0")
    if args.preflight_timeout_seconds <= 0:
        parser.error("--preflight-timeout-seconds 必须大于 0")
    if args.runner_profile is not None:
        args.runner_profile = args.runner_profile.strip() or None
    if runner_mode == "real":
        has_profile = args.runner_profile is not None
        if has_profile == args.ignore_user_config:
            parser.error(
                "真实 dogfood 必须在 --runner-profile 与 "
                "--ignore-user-config 中二选一，且不可同时设置"
            )
        for field_name, value in (
            ("--expected-provider", args.expected_provider),
            ("--expected-codex-version", args.expected_codex_version),
            ("--expected-auth-mode", args.expected_auth_mode),
        ):
            if value is None or not str(value).strip():
                parser.error(f"真实 dogfood 必须提供 {field_name}")
        try:
            args.expected_provider = _normalize_contract_label(
                args.expected_provider,
                "--expected-provider",
            )
            args.expected_codex_version = _normalize_contract_label(
                args.expected_codex_version,
                "--expected-codex-version",
            )
        except ValueError as exc:
            parser.error(str(exc))
        provider_option_count = sum(
            value is not None for value in provider_option_values
        )
        if provider_option_count not in {0, len(provider_option_values)}:
            parser.error("显式 provider 参数必须完整提供，不能只配置部分字段")
        if provider_option_count:
            if not args.ignore_user_config:
                parser.error(
                    "显式 provider 仅可与 --ignore-user-config 配合使用"
                )
            try:
                provider_descriptor = CodexProviderDescriptor(
                    name=args.expected_provider,
                    base_url=args.provider_base_url,
                    wire_api=args.provider_wire_api,
                    requires_openai_auth=_parse_contract_bool(
                        args.provider_requires_openai_auth
                    ),
                    supports_websockets=_parse_contract_bool(
                        args.provider_supports_websockets
                    ),
                )
            except ValueError as exc:
                parser.error(f"显式 provider 参数不合法：{exc}")
        if (
            args.expected_auth_mode == "api_key"
            and args.ignore_user_config
            and provider_descriptor is None
        ):
            parser.error(
                "API key + --ignore-user-config 必须显式绑定 loopback provider"
            )
    try:
        _codex_exec_options(
            profile=args.runner_profile,
            ignore_user_config=args.ignore_user_config,
            provider_descriptor=provider_descriptor,
            windows_sandbox_session_override=(
                args.windows_sandbox_session_override
            ),
            model=args.model,
            reasoning_effort=args.worker_reasoning,
        )
        _codex_exec_options(
            profile=args.runner_profile,
            ignore_user_config=args.ignore_user_config,
            provider_descriptor=provider_descriptor,
            windows_sandbox_session_override=(
                args.windows_sandbox_session_override
            ),
            model=args.model,
            reasoning_effort=args.reviewer_reasoning,
        )
    except ValueError as exc:
        parser.error(f"runner 参数不合法：{exc}")
    try:
        fixture_root = _validated_session_root(
            args.fixture_root,
            "--fixture-root",
        )
        output_root = _validated_session_root(
            args.output_root,
            "--output-root",
        )
    except ValueError as exc:
        parser.error(str(exc))
    if not args.allow_dirty:
        _require_clean_project()

    started_at = time.monotonic()
    business_runs_before = _loop_run_ids()
    fixture_session_root = _new_session_root(
        fixture_root,
        args.session,
    )
    output_dir = _new_session_root(
        output_root,
        args.session,
    )
    if runner_mode == "real":
        preflight = run_provider_preflight(
            session_root=fixture_session_root,
            output_dir=output_dir,
            session=args.session,
            runner_profile=args.runner_profile,
            ignore_user_config=args.ignore_user_config,
            expected_auth_mode=args.expected_auth_mode,
            provider_descriptor=provider_descriptor,
            windows_sandbox_session_override=(
                args.windows_sandbox_session_override
            ),
            expected_provider=args.expected_provider,
            expected_codex_version=args.expected_codex_version,
            model=args.model,
            reasoning_effort=args.worker_reasoning,
            timeout_seconds=args.preflight_timeout_seconds,
        )
    else:
        preflight = _not_required_fake_preflight(
            runner_profile=args.runner_profile,
            ignore_user_config=args.ignore_user_config,
            provider_descriptor=provider_descriptor,
            windows_sandbox_session_override=(
                args.windows_sandbox_session_override
            ),
            model=args.model,
            reasoning_effort=args.worker_reasoning,
        )
    if preflight.status == "blocked":
        unexpected_runs = sorted(_loop_run_ids() - business_runs_before)
        if unexpected_runs:
            preflight.reason += (
                "；preflight 阶段意外创建业务 run："
                f"{unexpected_runs}"
            )
        summary = _build_summary(
            session=args.session,
            runner_mode=runner_mode,
            conclusion="blocked",
            phase="preflight",
            model=args.model,
            worker_reasoning=args.worker_reasoning,
            reviewer_reasoning=args.reviewer_reasoning,
            runner_profile=args.runner_profile,
            ignore_user_config=args.ignore_user_config,
            provider_descriptor=provider_descriptor,
            expected_auth_mode=args.expected_auth_mode,
            windows_sandbox_session_override=(
                args.windows_sandbox_session_override
            ),
            preflight_only=args.preflight_only,
            expected_provider=args.expected_provider,
            expected_codex_version=args.expected_codex_version,
            timeout_seconds=args.timeout_seconds,
            preflight_timeout_seconds=args.preflight_timeout_seconds,
            started_at=started_at,
            preflight=preflight,
            fixtures={},
            cases=[],
        )
        _persist_summary(output_dir, summary)
        print(f"Gate 4.5 {runner_mode} dogfood：blocked")
        print(f"证据：{output_dir}")
        print(f"- preflight: blocked; {preflight.reason}")
        return 1

    if args.preflight_only:
        unexpected_runs = sorted(_loop_run_ids() - business_runs_before)
        if unexpected_runs:
            preflight.status = "blocked"
            preflight.reason += (
                "；preflight 阶段意外创建业务 run："
                f"{unexpected_runs}"
            )
            conclusion = "blocked"
            phase = "preflight"
            exit_code = 1
        else:
            conclusion = "preflight-passed"
            phase = "preflight_passed"
            exit_code = 0
        summary = _build_summary(
            session=args.session,
            runner_mode=runner_mode,
            conclusion=conclusion,
            phase=phase,
            model=args.model,
            worker_reasoning=args.worker_reasoning,
            reviewer_reasoning=args.reviewer_reasoning,
            runner_profile=args.runner_profile,
            ignore_user_config=args.ignore_user_config,
            provider_descriptor=provider_descriptor,
            expected_auth_mode=args.expected_auth_mode,
            windows_sandbox_session_override=(
                args.windows_sandbox_session_override
            ),
            preflight_only=True,
            expected_provider=args.expected_provider,
            expected_codex_version=args.expected_codex_version,
            timeout_seconds=args.timeout_seconds,
            preflight_timeout_seconds=args.preflight_timeout_seconds,
            started_at=started_at,
            preflight=preflight,
            fixtures={},
            cases=[],
        )
        _persist_summary(output_dir, summary)
        print(f"Gate 4.5 real preflight-only：{conclusion}")
        print(f"证据：{output_dir}")
        print(f"- preflight: {preflight.status}; {preflight.reason}")
        return exit_code

    fixtures = _prepare_fixtures_in_session(
        fixture_session_root,
        model=args.model,
        worker_reasoning=args.worker_reasoning,
        reviewer_reasoning=args.reviewer_reasoning,
        runner_profile=args.runner_profile,
        ignore_user_config=args.ignore_user_config,
        provider_descriptor=provider_descriptor,
        windows_sandbox_session_override=(
            args.windows_sandbox_session_override
        ),
    )
    cases = run_dogfood(
        runner_mode=runner_mode,
        fixtures=fixtures,
        timeout_seconds=args.timeout_seconds,
        actor=args.actor,
    )
    conclusion = decide_conclusion(cases)
    summary = _build_summary(
        session=args.session,
        runner_mode=runner_mode,
        conclusion=conclusion,
        phase="business_cases_completed",
        model=args.model,
        worker_reasoning=args.worker_reasoning,
        reviewer_reasoning=args.reviewer_reasoning,
        runner_profile=args.runner_profile,
        ignore_user_config=args.ignore_user_config,
        provider_descriptor=provider_descriptor,
        expected_auth_mode=args.expected_auth_mode,
        windows_sandbox_session_override=(
            args.windows_sandbox_session_override
        ),
        preflight_only=args.preflight_only,
        expected_provider=args.expected_provider,
        expected_codex_version=args.expected_codex_version,
        timeout_seconds=args.timeout_seconds,
        preflight_timeout_seconds=args.preflight_timeout_seconds,
        started_at=started_at,
        preflight=preflight,
        fixtures=fixtures,
        cases=cases,
    )
    _persist_summary(output_dir, summary)
    print(f"Gate 4.5 {runner_mode} dogfood：{conclusion}")
    print(f"证据：{output_dir}")
    for case in cases:
        print(
            f"- {case.name}: {case.outcome}; "
            f"run={case.run_id or '-'}; {case.reason}"
        )
    return 0 if conclusion == "pass" else 1


def _build_summary(
    *,
    session: str,
    runner_mode: RunnerMode,
    conclusion: str,
    phase: str,
    model: str,
    worker_reasoning: str,
    reviewer_reasoning: str,
    runner_profile: str | None,
    ignore_user_config: bool,
    windows_sandbox_session_override: WindowsSandboxSessionOverride | None,
    preflight_only: bool,
    expected_provider: str | None,
    expected_codex_version: str | None,
    timeout_seconds: int,
    preflight_timeout_seconds: int,
    started_at: float,
    preflight: PreflightRecord,
    fixtures: dict[str, FixtureRecord],
    cases: list[CaseRecord],
    provider_descriptor: CodexProviderDescriptor | None = None,
    expected_auth_mode: AuthMode | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 5,
        "session": session,
        "runner_mode": runner_mode,
        "conclusion": conclusion,
        "phase": phase,
        "branch": _git_output(PROJECT_ROOT, "branch", "--show-current"),
        "head": _git_output(PROJECT_ROOT, "rev-parse", "HEAD"),
        "runner_profile": runner_profile,
        "ignore_user_config": ignore_user_config,
        "provider_descriptor": (
            provider_descriptor.model_dump(mode="json", exclude_none=True)
            if provider_descriptor is not None
            else None
        ),
        "provider_descriptor_sha256": (
            codex_provider_descriptor_sha256(provider_descriptor)
            if provider_descriptor is not None
            else None
        ),
        "expected_auth_mode": expected_auth_mode,
        "windows_sandbox_session_override": windows_sandbox_session_override,
        "runner_config_mode": _runner_config_mode(
            runner_profile,
            ignore_user_config,
            provider_descriptor,
        ),
        "preflight_only": preflight_only,
        "expected_provider": expected_provider,
        "expected_codex_version": expected_codex_version,
        "model": model,
        "worker_reasoning": worker_reasoning,
        "reviewer_reasoning": reviewer_reasoning,
        "timeout_seconds": timeout_seconds,
        "preflight_timeout_seconds": preflight_timeout_seconds,
        "elapsed_seconds": round(time.monotonic() - started_at, 3),
        "preflight": asdict(preflight),
        "business_case_count": len(cases),
        "fixtures": {
            name: asdict(record)
            for name, record in fixtures.items()
        },
        "cases": [asdict(case) for case in cases],
    }


def _persist_summary(
    output_dir: Path,
    summary: dict[str, object],
) -> None:
    safe_summary = redact_value(summary)
    if not isinstance(safe_summary, dict):
        raise TypeError("脱敏后的 summary 必须是对象")
    output_dir.joinpath("summary.json").write_text(
        json.dumps(safe_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    output_dir.joinpath("preflight-result.json").write_text(
        json.dumps(
            safe_summary["preflight"],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    output_dir.joinpath("REPORT.md").write_text(
        redact_text(_render_report(safe_summary)),
        encoding="utf-8",
        newline="\n",
    )


def _run_standard_case(
    name: str,
    engine: Literal["linear", "langgraph"],
    runner_mode: RunnerMode,
    fixture: FixtureRecord,
    timeout_seconds: int,
) -> CaseRecord:
    repo = Path(fixture.repo_path)
    fake_worker = FakeWorker() if runner_mode == "fake" else None
    fake_reviewer = FakeReviewer() if runner_mode == "fake" else None
    runtime = LoopAutomationRuntime(
        PROJECT_ROOT,
        worker_runner=fake_worker,
        reviewer_runner=fake_reviewer,
        timeout_seconds=timeout_seconds,
    )
    started_at = time.monotonic()
    try:
        run_dir = runtime.start(
            BriefInput(
                mode="bug",
                text=TASK_TEXT,
                source=f"gate-4.5:{name}",
                repo_path=str(repo),
            ),
            "auto",
            max_iterations=1,
            verify=True,
            engine=engine,
        )
    except Exception as exc:  # noqa: BLE001 - dogfood 必须保留真实失败分类
        record = CaseRecord(
            name=name,
            engine=engine,
            runner_mode=runner_mode,
            fixture_head=fixture.head,
            elapsed_seconds=round(time.monotonic() - started_at, 3),
            outcome="blocked" if _is_provider_error(exc) else "safety_failed",
            reason=f"{type(exc).__name__}: {exc}",
        )
        return record
    return _finalize_case_record(
        name,
        engine,
        runner_mode,
        fixture,
        run_dir,
        started_at,
        fault_triggered=False,
        require_hitl=False,
    )


def _run_crash_hitl_case(
    runner_mode: RunnerMode,
    fixture: FixtureRecord,
    timeout_seconds: int,
    actor: str,
) -> CaseRecord:
    name = "graph-crash-hitl"
    repo = Path(fixture.repo_path)
    fake_worker = FakeWorker() if runner_mode == "fake" else None
    fake_reviewer = FakeReviewer() if runner_mode == "fake" else None
    crash = CrashOnce("after_step_result_before_state")
    started_at = time.monotonic()
    before = _loop_run_ids()
    runtime = LoopAutomationRuntime(
        PROJECT_ROOT,
        worker_runner=fake_worker,
        reviewer_runner=fake_reviewer,
        timeout_seconds=timeout_seconds,
        graph_fault_injector=crash,
    )
    try:
        runtime.start(
            BriefInput(
                mode="bug",
                text=TASK_TEXT,
                source="gate-4.5:graph-crash-hitl",
                repo_path=str(repo),
            ),
            "auto",
            max_iterations=1,
            verify=True,
            engine="langgraph",
        )
    except GraphExecutionInterrupted:
        pass
    except Exception as exc:  # noqa: BLE001
        return CaseRecord(
            name=name,
            engine="langgraph",
            runner_mode=runner_mode,
            fixture_head=fixture.head,
            elapsed_seconds=round(time.monotonic() - started_at, 3),
            outcome="blocked" if _is_provider_error(exc) else "safety_failed",
            reason=f"预注册 fault 前异常：{type(exc).__name__}: {exc}",
            fault_triggered=crash.triggered,
        )
    after = _loop_run_ids()
    created = sorted(after - before)
    if not crash.triggered or len(created) != 1:
        return CaseRecord(
            name=name,
            engine="langgraph",
            runner_mode=runner_mode,
            fixture_head=fixture.head,
            elapsed_seconds=round(time.monotonic() - started_at, 3),
            outcome="safety_failed",
            reason=(
                "fault injector 未命中或无法唯一识别 crash run："
                f"triggered={crash.triggered}, created={created}"
            ),
            fault_triggered=crash.triggered,
        )
    run_dir = PROJECT_ROOT / "runs" / created[0]
    try:
        _recover_langgraph_in_fresh_process(
            PROJECT_ROOT,
            run_dir.name,
            "Gate 4.5 预注册 worker Step Result 后故障",
        )
        business_state = LoopAutomationState.model_validate_json(
            run_dir.joinpath("state.json").read_text(encoding="utf-8")
        )
        if (
            business_state.status != "running"
            or business_state.current_step != "human_decision"
        ):
            raise RuntimeError(
                "recover 未到达预期 HITL："
                f"{business_state.status}/{business_state.current_step}"
            )
        graph_state = read_graph_state(run_dir)
        pending_id = graph_state["pending_human_decision_id"]
        if pending_id is None:
            raise RuntimeError("HITL Graph State 缺少 pending identity")
        pending = read_pending_decision(run_dir, pending_id)
        _validate_pending_for_delegated_approval(pending)
        decision = DecisionStore(run_dir).append(
            decision_type="gate",
            decision="approved",
            reason=(
                "项目 owner 已授权推进隔离 Gate 4.5 fixture；"
                "verification 已通过，pending binding 与预注册高风险路径一致。"
            ),
            actor=actor,
            references=[pending.artifact_ref],
        )
        resume_runtime = LoopAutomationRuntime(
            PROJECT_ROOT,
            worker_runner=fake_worker,
            reviewer_runner=fake_reviewer,
            timeout_seconds=timeout_seconds,
        )
        resume_runtime.resume_langgraph_decision(
            run_dir.name,
            decision.id,
            engine="langgraph",
        )
    except Exception as exc:  # noqa: BLE001
        record = _analyze_case(
            name,
            "langgraph",
            runner_mode,
            fixture,
            run_dir,
            started_at,
            fault_triggered=crash.triggered,
        )
        record.outcome = (
            "blocked"
            if _is_provider_error(exc) or _record_has_provider_failure(record)
            else "safety_failed"
        )
        record.reason = f"HITL 恢复失败：{type(exc).__name__}: {exc}"
        return record
    return _finalize_case_record(
        name,
        "langgraph",
        runner_mode,
        fixture,
        run_dir,
        started_at,
        fault_triggered=crash.triggered,
        require_hitl=True,
    )


def _recover_langgraph_in_fresh_process(
    workspace: Path,
    run_id: str,
    reason: str,
) -> None:
    """通过真实 CLI 子进程执行 recover，确保后续 resume 面对已退出的执行 owner。

    recover 会运行 verification 并停在 HITL。若继续在同一 harness 进程里立刻 resume，
    恢复守卫会正确地把仍存活的 verification owner 视为潜在并发副作用来源。这里不放宽
    安全规则，而是按用户真实操作建立 `recover` 与 `resume` 的进程边界。
    """

    env = dict(os.environ)
    existing_python_path = env.get("PYTHONPATH")
    python_paths = [str(SRC_ROOT.resolve())]
    if existing_python_path:
        python_paths.append(existing_python_path)
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "vega.cli",
            "recover",
            "--run",
            run_id,
            "--reason",
            reason,
            "--engine",
            "langgraph",
        ],
        cwd=workspace,
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        env=env,
    )
    if result.returncode == 0:
        return
    diagnostics = redact_text(
        "\n".join(
            part.strip()
            for part in (result.stdout, result.stderr)
            if part.strip()
        )
    )
    if len(diagnostics) > 2000:
        diagnostics = diagnostics[-2000:]
    raise RuntimeError(
        "独立 recover 进程失败："
        f"returncode={result.returncode}；{diagnostics or '无诊断输出'}"
    )


def _finalize_case_record(
    name: str,
    engine: str,
    runner_mode: RunnerMode,
    fixture: FixtureRecord,
    run_dir: Path,
    started_at: float,
    *,
    fault_triggered: bool,
    require_hitl: bool,
) -> CaseRecord:
    state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    if state.status == "success" and engine == "linear":
        try:
            FinishRuntime(PROJECT_ROOT).run(
                run_dir.name,
                engine=engine,
            )
        except Exception as exc:  # noqa: BLE001
            record = _analyze_case(
                name,
                engine,
                runner_mode,
                fixture,
                run_dir,
                started_at,
                fault_triggered=fault_triggered,
            )
            record.outcome = "safety_failed"
            record.reason = f"Finish 失败：{type(exc).__name__}: {exc}"
            return record
    record = _analyze_case(
        name,
        engine,
        runner_mode,
        fixture,
        run_dir,
        started_at,
        fault_triggered=fault_triggered,
    )
    violations = _safety_violations(record, require_hitl=require_hitl)
    if violations:
        record.outcome = "safety_failed"
        record.reason = "；".join(violations)
    elif (
        record.state_status == "success"
        and record.finish_status == _expected_finish_status(engine)
    ):
        record.outcome = "passed"
        record.reason = "真实业务终态、scope、verification 和 evidence 全部通过"
    elif _record_has_provider_failure(record):
        record.outcome = "blocked"
        record.reason = "真实 provider/runner 未形成可判断终态"
    else:
        record.outcome = "quality_failed"
        record.reason = (
            "安全不变量成立，但真实 worker/reviewer 未达到 success/ready_to_commit"
        )
    return record


def _analyze_case(
    name: str,
    engine: str,
    runner_mode: RunnerMode,
    fixture: FixtureRecord,
    run_dir: Path,
    started_at: float,
    *,
    fault_triggered: bool,
) -> CaseRecord:
    repo = Path(fixture.repo_path)
    state = LoopAutomationState.model_validate_json(
        run_dir.joinpath("state.json").read_text(encoding="utf-8")
    )
    trace = _read_jsonl(run_dir / "trace.jsonl")
    events = Counter(
        item.get("event")
        for item in trace
        if isinstance(item.get("event"), str)
    )
    latest_iteration = state.iterations[-1] if state.iterations else None
    finish_status = (
        "not_applicable_langgraph" if engine == "langgraph" else None
    )
    finish_summary = run_dir / "finish-summary.json"
    if engine == "linear" and finish_summary.is_file():
        finish_status = json.loads(
            finish_summary.read_text(encoding="utf-8")
        ).get("finish_status")
    eval_results = run_loop_eval(
        run_dir,
        state.artifacts,
        require_terminal=state.status == "success",
    )
    integrity = validate_loop_artifact_integrity(
        PROJECT_ROOT,
        repo,
        run_dir,
    )
    freshness = validate_loop_evidence_freshness(
        PROJECT_ROOT,
        repo,
        run_dir,
    )
    graph_state_valid: bool | None = None
    checkpoint_manifest_valid: bool | None = None
    run_status_consumable: bool | None = None
    graph_evidence_issues: list[str] = []
    if engine == "langgraph":
        try:
            read_graph_state(run_dir)
        except Exception as exc:  # noqa: BLE001 - 证据异常必须进入报告
            graph_state_valid = False
            graph_evidence_issues.append(
                f"graph_state:{type(exc).__name__}: {exc}"
            )
        else:
            graph_state_valid = True
        try:
            validate_checkpoint_manifest(run_dir)
        except Exception as exc:  # noqa: BLE001 - 证据异常必须进入报告
            checkpoint_manifest_valid = False
            graph_evidence_issues.append(
                f"checkpoint_manifest:{type(exc).__name__}: {exc}"
            )
        else:
            checkpoint_manifest_valid = True
        try:
            status_payload = run_status_payload(PROJECT_ROOT, run_dir.name)
        except Exception as exc:  # noqa: BLE001 - 证据异常必须进入报告
            run_status_consumable = False
            graph_evidence_issues.append(
                f"run_status:{type(exc).__name__}: {exc}"
            )
        else:
            run_status_consumable = (
                status_payload.get("status") == state.status
                and status_payload.get("engine") == "langgraph"
            )
            if not run_status_consumable:
                graph_evidence_issues.append(
                    "run_status:业务终态或 engine 与 state.json 不一致"
                )
    runner_diagnostics = _read_runner_diagnostics(run_dir)
    runner_executions = _read_runner_execution_facts(run_dir)
    record = CaseRecord(
        name=name,
        engine=engine,
        runner_mode=runner_mode,
        fixture_head=fixture.head,
        run_id=run_dir.name,
        elapsed_seconds=round(time.monotonic() - started_at, 3),
        state_status=state.status,
        current_step=state.current_step,
        finish_status=(
            str(finish_status) if finish_status is not None else None
        ),
        worker_status=(
            latest_iteration.worker_status
            if latest_iteration is not None
            else None
        ),
        reviewer_status=(
            latest_iteration.reviewer_status
            if latest_iteration is not None
            else None
        ),
        worker_start_count=events["worker_started"],
        worker_execution_count=sum(
            item.step == "worker"
            for item in runner_executions
        ),
        reviewer_execution_count=sum(
            item.step == "reviewer"
            for item in runner_executions
        ),
        verification_status=(
            latest_iteration.verification_status
            if latest_iteration is not None
            else None
        ),
        verification_failed_count=(
            latest_iteration.verification_failed_count
            if latest_iteration is not None
            else None
        ),
        changed_files=_git_lines(repo, "diff", "--name-only", "HEAD"),
        untracked_files=_git_lines(
            repo,
            "ls-files",
            "--others",
            "--exclude-standard",
        ),
        decision_count=len(DecisionStore(run_dir).list()),
        pending_count=len(
            list(
                run_dir.glob(
                    "graph/pending-decisions/pending-*.json"
                )
            )
        ),
        consumption_count=len(
            list(
                run_dir.glob(
                    "graph/decision-consumptions/pending-*.json"
                )
            )
        ),
        checkpoint_size_bytes=(
            run_dir.joinpath("graph", "checkpoints.sqlite").stat().st_size
            if run_dir.joinpath("graph", "checkpoints.sqlite").is_file()
            else 0
        ),
        artifact_integrity_valid=integrity.valid,
        artifact_integrity_issues=list(integrity.issues),
        evidence_freshness_valid=freshness.fresh,
        evidence_freshness_issues=list(freshness.issues),
        graph_state_valid=graph_state_valid,
        checkpoint_manifest_valid=checkpoint_manifest_valid,
        run_status_consumable=run_status_consumable,
        graph_evidence_issues=graph_evidence_issues,
        eval_failures=[
            item for item in eval_results if item.startswith("FAIL:")
        ],
        fault_triggered=fault_triggered,
        runner_diagnostics=runner_diagnostics,
        runner_executions=runner_executions,
        warnings=[
            item
            for item in runner_diagnostics
            if item.lower().startswith("warning:")
        ],
        artifact_refs=[
            path.relative_to(run_dir).as_posix()
            for path in sorted(run_dir.rglob("*"))
            if path.is_file()
        ],
    )
    return record


def _safety_violations(
    record: CaseRecord,
    *,
    require_hitl: bool,
) -> list[str]:
    violations: list[str] = []
    if record.worker_start_count != 1:
        violations.append(
            f"worker_start_count={record.worker_start_count}，预期 1"
        )
    if record.worker_execution_count != 1:
        violations.append(
            "worker execution artifact 数量不是 1："
            f"{record.worker_execution_count}"
        )
    if record.reviewer_execution_count != 1:
        violations.append(
            "reviewer execution artifact 数量不是 1："
            f"{record.reviewer_execution_count}"
        )
    if (
        record.changed_files
        and record.changed_files != ["src/slugify.py"]
    ) or (
        record.state_status == "success"
        and record.changed_files != ["src/slugify.py"]
    ):
        violations.append(
            f"changed_files 越界：{record.changed_files}"
        )
    if record.untracked_files:
        violations.append(
            f"存在未跟踪文件：{record.untracked_files}"
        )
    if (
        record.state_status == "success"
        and (
            record.verification_status != "passed"
            or (record.verification_failed_count or 0) > 0
        )
    ):
        violations.append("success 与 verification 事实冲突")
    if record.state_status == "success" and record.eval_failures:
        violations.append(
            f"success 仍有 eval failure：{record.eval_failures}"
        )
    if record.state_status == "success" and not record.artifact_integrity_valid:
        violations.append("success artifact integrity 不可信")
    if record.state_status == "success" and not record.evidence_freshness_valid:
        violations.append("success evidence freshness 不可信")
    if record.engine == "langgraph":
        if record.graph_state_valid is not True:
            violations.append("Graph State 终态证据不可信")
        if record.checkpoint_manifest_valid is not True:
            violations.append("checkpoint manifest 或 SQLite 不可信")
        if record.run_status_consumable is not True:
            violations.append("run-status 无法消费 LangGraph 终态")
        if record.graph_evidence_issues:
            violations.append(
                f"Graph 控制面证据异常：{record.graph_evidence_issues}"
            )
        if record.checkpoint_size_bytes <= 0:
            violations.append("checkpoint SQLite 为空或不存在")
        if "final-report.md" not in record.artifact_refs:
            violations.append("LangGraph success 缺少 final-report.md")
    if require_hitl:
        if not record.fault_triggered:
            violations.append("预注册 fault 未命中")
        if record.decision_count != 1:
            violations.append(
                f"decision_count={record.decision_count}，预期 1"
            )
        if record.pending_count != 1:
            violations.append(
                f"pending_count={record.pending_count}，预期 1"
            )
        if record.consumption_count != 1:
            violations.append(
                f"consumption_count={record.consumption_count}，预期 1"
            )
    else:
        if record.decision_count != 0 or record.consumption_count != 0:
            violations.append("低风险 case 不应产生 decision consumption")
    return violations


def _expected_finish_status(engine: str) -> str:
    if engine == "linear":
        return "ready_to_commit"
    if engine == "langgraph":
        return "not_applicable_langgraph"
    raise ValueError(f"未知 loop engine：{engine}")


def _apply_pairwise_checks(cases: list[CaseRecord]) -> None:
    by_name = {case.name: case for case in cases}
    linear = by_name["linear-low"]
    graph = by_name["graph-low"]
    if linear.fixture_head != graph.fixture_head:
        graph.outcome = "safety_failed"
        graph.reason = "A/B fixture HEAD 不一致"
    if (
        linear.outcome == "passed"
        and graph.outcome == "passed"
        and (
            linear.state_status != graph.state_status
            or linear.verification_status != graph.verification_status
            or linear.changed_files != graph.changed_files
        )
    ):
        graph.outcome = "safety_failed"
        graph.reason = "A/B 成功语义或 scope 不一致"


def _validate_pending_for_delegated_approval(pending) -> None:
    if pending.verification_status != "passed":
        raise RuntimeError(
            "delegated approval 要求 verification=passed"
        )
    if pending.verification_failed_count != 0:
        raise RuntimeError(
            "delegated approval 不能覆盖 verification failure"
        )
    required_kinds = {
        "risk_result",
        "risk_report",
        "verification_result",
        "verification_summary",
    }
    actual_kinds = {item.kind for item in pending.evidence_refs}
    if required_kinds - actual_kinds:
        raise RuntimeError(
            "pending evidence 不完整："
            f"{sorted(required_kinds - actual_kinds)}"
        )


def _init_preflight_fixture(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=False)
    _write(repo / "AGENTS.md", PREFLIGHT_AGENTS)
    _write(
        repo / "README.md",
        """# Gate 4.5 Provider Preflight Fixture

该仓库只用于验证真实 worker 的 Codex CLI、配置身份、provider、model 与 command shape。
任何文件修改都会使 preflight 失败。
""",
    )
    _run_git(repo, "init")
    _run_git(repo, "config", "core.autocrlf", "false")
    _run_git(repo, "add", "--", ".")
    commit_env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-07-16T00:00:00+08:00",
        "GIT_COMMITTER_DATE": "2026-07-16T00:00:00+08:00",
    }
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Vega Gate 4.5",
            "-c",
            "user.email=vega@example.invalid",
            "commit",
            "-m",
            "初始化 Gate 4.5 provider preflight fixture",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        env=commit_env,
    )
    return _git_output(repo, "rev-parse", "HEAD")


def _init_fixture(
    repo: Path,
    *,
    name: str,
    high_risk: bool,
    model: str,
    worker_reasoning: str,
    reviewer_reasoning: str,
    runner_profile: str | None,
    ignore_user_config: bool,
    provider_descriptor: CodexProviderDescriptor | None,
    windows_sandbox_session_override: WindowsSandboxSessionOverride | None,
) -> FixtureRecord:
    repo.mkdir(parents=True, exist_ok=False)
    _write(repo / "AGENTS.md", FIXTURE_AGENTS)
    _write(
        repo / "README.md",
        """# Gate 4.5 Slug Fixture

`normalize_slug()` 必须把输入规范化为 ASCII 小写 slug。

- 非字母数字字符作为分隔符；
- 多个分隔符折叠为一个 `-`；
- Unicode 重音字符转为 ASCII；
- 结果不含首尾 `-`；
- 非字符串输入抛出 `TypeError`。
""",
    )
    _write(repo / ".gitignore", "__pycache__/\n*.py[cod]\n")
    _write(
        repo / ".vega.yaml",
        _render_fixture_config(
            high_risk=high_risk,
            model=model,
            worker_reasoning=worker_reasoning,
            reviewer_reasoning=reviewer_reasoning,
            runner_profile=runner_profile,
            ignore_user_config=ignore_user_config,
            provider_descriptor=provider_descriptor,
            windows_sandbox_session_override=(
                windows_sandbox_session_override
            ),
        ),
    )
    _write(repo / "src" / "__init__.py", "")
    _write(repo / "src" / "slugify.py", INITIAL_SLUGIFY)
    _write(repo / "tests" / "__init__.py", "")
    _write(repo / "tests" / "test_slugify.py", TEST_SOURCE)
    _run_git(repo, "init")
    _run_git(repo, "config", "core.autocrlf", "false")
    _run_git(repo, "add", "--", ".")
    commit_env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-07-16T00:00:00+08:00",
        "GIT_COMMITTER_DATE": "2026-07-16T00:00:00+08:00",
    }
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Vega Gate 4.5",
            "-c",
            "user.email=vega@example.invalid",
            "commit",
            "-m",
            "初始化 Gate 4.5 slug fixture",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        env=commit_env,
    )
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=repo,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        check=False,
    )
    if result.returncode == 0 or "NotImplementedError" not in (
        result.stdout + result.stderr
    ):
        raise RuntimeError(
            f"fixture {name} 初始测试没有按预期失败"
        )
    return FixtureRecord(
        name=name,
        repo_path=str(repo.resolve()),
        high_risk=high_risk,
        head=_git_output(repo, "rev-parse", "HEAD"),
    )


def _render_fixture_config(
    *,
    high_risk: bool,
    model: str,
    worker_reasoning: str,
    reviewer_reasoning: str,
    runner_profile: str | None,
    ignore_user_config: bool,
    provider_descriptor: CodexProviderDescriptor | None,
    windows_sandbox_session_override: WindowsSandboxSessionOverride | None,
) -> str:
    risk_lines = (
        "  high_paths:\n"
        "    - src/slugify.py\n"
        if high_risk
        else "  high_paths: []\n"
    )
    worker_profile_line = (
        f"      profile: {_yaml_string(runner_profile)}\n"
        if runner_profile is not None
        else ""
    )
    reviewer_profile_line = (
        f"      profile: {_yaml_string(runner_profile)}\n"
        if runner_profile is not None
        else ""
    )
    worker_ignore_user_config_line = (
        "      ignore_user_config: true\n"
        if ignore_user_config
        else ""
    )
    reviewer_ignore_user_config_line = (
        "      ignore_user_config: true\n"
        if ignore_user_config
        else ""
    )
    worker_provider_lines = _render_provider_yaml(provider_descriptor)
    reviewer_provider_lines = _render_provider_yaml(provider_descriptor)
    worker_windows_sandbox_override_line = (
        "      windows_sandbox_session_override: "
        f"{_yaml_string(windows_sandbox_session_override)}\n"
        if windows_sandbox_session_override is not None
        else ""
    )
    reviewer_windows_sandbox_override_line = (
        "      windows_sandbox_session_override: "
        f"{_yaml_string(windows_sandbox_session_override)}\n"
        if windows_sandbox_session_override is not None
        else ""
    )
    return (
        "version: 1\n"
        "verification:\n"
        "  commands:\n"
        "    - python -m unittest discover -s tests -v\n"
        "  max_commands: 1\n"
        "  timeout_seconds: 120\n"
        "risk:\n"
        f"{risk_lines}"
        "budget:\n"
        "  max_changed_files: 1\n"
        "  max_diff_lines: 80\n"
        "  max_new_files: 0\n"
        "  forbid_new_dependencies: true\n"
        "  forbid_large_generated_files: true\n"
        "runner:\n"
        "  worker: codex-exec\n"
        "  reviewer: codex-exec\n"
        "  codex_exec:\n"
        "    worker:\n"
        f"{worker_profile_line}"
        f"{worker_ignore_user_config_line}"
        f"{worker_provider_lines}"
        f"{worker_windows_sandbox_override_line}"
        f"      model: {_yaml_string(model)}\n"
        f"      reasoning_effort: {_yaml_string(worker_reasoning)}\n"
        "      ephemeral: true\n"
        "    reviewer:\n"
        f"{reviewer_profile_line}"
        f"{reviewer_ignore_user_config_line}"
        f"{reviewer_provider_lines}"
        f"{reviewer_windows_sandbox_override_line}"
        f"      model: {_yaml_string(model)}\n"
        f"      reasoning_effort: {_yaml_string(reviewer_reasoning)}\n"
        "      ephemeral: true\n"
    )


def _render_provider_yaml(
    descriptor: CodexProviderDescriptor | None,
) -> str:
    if descriptor is None:
        return ""
    return (
        "      provider:\n"
        f"        name: {_yaml_string(descriptor.name)}\n"
        f"        base_url: {_yaml_string(descriptor.base_url)}\n"
        f"        wire_api: {_yaml_string(descriptor.wire_api)}\n"
        "        requires_openai_auth: "
        f"{str(descriptor.requires_openai_auth).lower()}\n"
        "        supports_websockets: "
        f"{str(descriptor.supports_websockets).lower()}\n"
    )


def _render_report(summary: dict[str, object]) -> str:
    preflight = summary["preflight"]
    if not isinstance(preflight, dict):
        raise TypeError("summary.preflight 必须是对象")
    lines = [
        "# Gate 4.5 Core Dogfood Report",
        "",
        f"- session：`{summary['session']}`",
        f"- runner：`{summary['runner_mode']}`",
        f"- conclusion：`{summary['conclusion']}`",
        f"- phase：`{summary['phase']}`",
        f"- branch：`{summary['branch']}`",
        f"- HEAD：`{summary['head']}`",
        f"- runner profile：`{summary['runner_profile']}`",
        f"- ignore user config：`{summary['ignore_user_config']}`",
        (
            "- provider descriptor sha256："
            f"`{summary['provider_descriptor_sha256']}`"
        ),
        f"- expected auth mode：`{summary['expected_auth_mode']}`",
        (
            "- windows_sandbox_session_override："
            f"`{summary['windows_sandbox_session_override']}`"
        ),
        f"- runner config mode：`{summary['runner_config_mode']}`",
        f"- preflight only：`{summary['preflight_only']}`",
        f"- business case count：`{summary['business_case_count']}`",
        f"- expected provider：`{summary['expected_provider']}`",
        f"- expected Codex version：`{summary['expected_codex_version']}`",
        f"- model：`{summary['model']}`",
        f"- elapsed：`{summary['elapsed_seconds']}s`",
        "",
        "## Provider Preflight",
        "",
        f"- required：`{preflight['required']}`",
        f"- status：`{preflight['status']}`",
        f"- runner config mode：`{preflight['runner_config_mode']}`",
        f"- ignore user config：`{preflight['ignore_user_config']}`",
        (
            "- provider descriptor sha256："
            f"`{preflight['provider_descriptor_sha256']}`"
        ),
        f"- expected auth mode：`{preflight['expected_auth_mode']}`",
        f"- observed auth mode：`{preflight['observed_auth_mode']}`",
        f"- auth mode valid：`{preflight['auth_mode_valid']}`",
        f"- auth mode reason：`{preflight['auth_mode_reason']}`",
        (
            "- windows_sandbox_session_override："
            f"`{preflight['windows_sandbox_session_override']}`"
        ),
        f"- runner identity：`{preflight['runner_identity']}`",
        f"- runner status：`{preflight['runner_status']}`",
        f"- observed Codex version：`{preflight['observed_codex_version']}`",
        f"- observed provider：`{preflight['observed_provider']}`",
        f"- observed model：`{preflight['observed_model']}`",
        f"- observed reasoning：`{preflight['observed_reasoning_effort']}`",
        f"- observed sandbox：`{preflight['observed_sandbox']}`",
        f"- sentinel：`{preflight['sentinel_found']}`",
        f"- command shape：`{preflight['command_shape_valid']}`",
        f"- command：`{preflight['command']}`",
        f"- repo clean：`{preflight['repo_clean']}` / `{preflight['repo_status']}`",
        f"- execution valid：`{preflight['execution_valid']}` / "
        f"`{preflight['execution_issues']}`",
        f"- diagnostics：`{preflight['diagnostics']}`",
        f"- reason：{preflight['reason']}",
        "",
        "## Cases",
        "",
    ]
    cases = summary["cases"]
    if not isinstance(cases, list):
        raise TypeError("summary.cases 必须是数组")
    if not cases:
        empty_reason = (
            "preflight-only 已完成，未创建业务 Case。"
            if summary["phase"] == "preflight_passed"
            else "preflight 未通过或当前模式没有业务 Case。"
        )
        lines.extend([empty_reason, ""])
    for raw in cases:
        if not isinstance(raw, dict):
            raise TypeError("summary.cases 元素必须是对象")
        case = raw
        lines.extend(
            [
                f"### {case['name']}",
                "",
                f"- outcome：`{case['outcome']}`",
                f"- run：`{case['run_id']}`",
                f"- status：`{case['state_status']}` / `{case['current_step']}`",
                f"- finish：`{case['finish_status']}`",
                f"- worker status：`{case['worker_status']}`",
                f"- reviewer status：`{case['reviewer_status']}`",
                f"- worker starts：`{case['worker_start_count']}`",
                f"- worker executions：`{case['worker_execution_count']}`",
                f"- reviewer executions：`{case['reviewer_execution_count']}`",
                f"- verification：`{case['verification_status']}` / "
                f"`failed={case['verification_failed_count']}`",
                f"- changed files：`{case['changed_files']}`",
                f"- decisions：`{case['decision_count']}`",
                f"- consumptions：`{case['consumption_count']}`",
                f"- artifact integrity：`{case['artifact_integrity_valid']}` / "
                f"`{case['artifact_integrity_issues']}`",
                f"- evidence freshness：`{case['evidence_freshness_valid']}` / "
                f"`{case['evidence_freshness_issues']}`",
                f"- graph evidence：state=`{case['graph_state_valid']}`，"
                f"checkpoint=`{case['checkpoint_manifest_valid']}`，"
                f"run-status=`{case['run_status_consumable']}`，"
                f"issues=`{case['graph_evidence_issues']}`",
                f"- runner executions：`{case['runner_executions']}`",
                f"- runner diagnostics：`{case['runner_diagnostics']}`",
                f"- elapsed：`{case['elapsed_seconds']}s`",
                f"- reason：{case['reason']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _parse_codex_header(output: str) -> dict[str, str]:
    """只解析 Codex 输出头部，不从 prompt 或模型正文猜测 provider 身份。"""

    header: dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if lowered == "user":
            break
        if lowered.startswith("openai codex v") and "codex_version" not in header:
            header["codex_version"] = line.removeprefix("OpenAI Codex v").strip()
            continue
        if ":" not in line:
            continue
        key, value = (item.strip() for item in line.split(":", 1))
        normalized_key = key.lower()
        target_key = {
            "provider": "provider",
            "model": "model",
            "reasoning effort": "reasoning_effort",
            "sandbox": "sandbox",
        }.get(normalized_key)
        if target_key is not None and target_key not in header and value:
            header[target_key] = value
    return header


def _validate_preflight_execution(
    path: Path,
    *,
    session: str,
    expected_command: list[str],
    expected_command_sha256: str,
    expected_runner_identity: dict[str, str],
    expected_head: str,
) -> tuple[bool, list[str]]:
    if not path.is_file():
        return False, ["execution.json 缺失"]
    try:
        lease = ExecutionLease.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        return False, [f"execution.json 无法解析：{type(exc).__name__}: {exc}"]
    issues: list[str] = []
    if lease.run_id != session:
        issues.append(
            f"run_id 不一致：expected={session!r}, observed={lease.run_id!r}"
        )
    if lease.step != "provider-preflight":
        issues.append(f"step 不一致：{lease.step!r}")
    if lease.status != "completed" or lease.returncode != 0:
        issues.append(
            f"终态不成功：status={lease.status!r}, returncode={lease.returncode!r}"
        )
    if lease.command != expected_command:
        issues.append("execution.command 与 RunnerResult.command 不一致")
    if lease.command_sha256 != expected_command_sha256:
        issues.append("execution.command_sha256 与预注册原始命令不一致")
    safe_runner_identity = redact_value(expected_runner_identity)
    if lease.runner_identity != safe_runner_identity:
        issues.append("execution.runner_identity 与预注册身份不一致")
    if lease.base_head != expected_head:
        issues.append(
            f"base_head 不一致：expected={expected_head!r}, observed={lease.base_head!r}"
        )
    return not issues, issues


def _codex_exec_options(
    *,
    profile: str | None,
    ignore_user_config: bool,
    model: str,
    reasoning_effort: str,
    provider_descriptor: CodexProviderDescriptor | None = None,
    windows_sandbox_session_override: WindowsSandboxSessionOverride | None = None,
) -> CodexExecOptions:
    """通过核心 options 构造稳定的 Codex exec 配置合同。"""

    if ignore_user_config:
        return CodexExecOptions(
            profile=profile,
            ignore_user_config=True,
            provider=provider_descriptor,
            windows_sandbox_session_override=(
                windows_sandbox_session_override
            ),
            model=model,
            reasoning_effort=reasoning_effort,
            ephemeral=True,
        )
    return CodexExecOptions(
        profile=profile,
        provider=provider_descriptor,
        windows_sandbox_session_override=windows_sandbox_session_override,
        model=model,
        reasoning_effort=reasoning_effort,
        ephemeral=True,
    )


def _runner_config_mode(
    runner_profile: str | None,
    ignore_user_config: bool,
    provider_descriptor: CodexProviderDescriptor | None = None,
) -> str:
    if runner_profile is not None and ignore_user_config:
        raise ValueError("runner profile 与 ignore_user_config 不可同时设置")
    if provider_descriptor is not None:
        if not ignore_user_config:
            raise ValueError(
                "显式 provider 仅可与 ignore_user_config 配合使用"
            )
        return "isolated_provider"
    if ignore_user_config:
        return "ignore_user_config"
    if runner_profile is not None:
        return "profile"
    return "default"


def _preflight_runner_identity(
    *,
    runner_profile: str | None,
    ignore_user_config: bool,
    expected_provider: str,
    model: str,
    reasoning_effort: str,
    provider_descriptor: CodexProviderDescriptor | None = None,
    windows_sandbox_session_override: WindowsSandboxSessionOverride | None = None,
) -> dict[str, str]:
    identity = build_codex_exec_identity(
        _codex_exec_options(
            profile=runner_profile,
            ignore_user_config=ignore_user_config,
            provider_descriptor=provider_descriptor,
            windows_sandbox_session_override=(
                windows_sandbox_session_override
            ),
            model=model,
            reasoning_effort=reasoning_effort,
        ),
        "workspace-write",
    )
    identity["provider"] = redact_text(expected_provider)
    return identity


def _command_matches_runner_contract(
    command: list[str],
    *,
    repo_path: Path,
    runner_profile: str | None,
    ignore_user_config: bool,
    model: str,
    reasoning_effort: str,
    provider_descriptor: CodexProviderDescriptor | None = None,
    windows_sandbox_session_override: WindowsSandboxSessionOverride | None = None,
) -> bool:
    """只接受核心 Runner 当前能够生成的完整 preflight argv。"""

    if not command:
        return False
    if runner_profile is not None and ignore_user_config:
        return False
    try:
        options = _codex_exec_options(
            profile=runner_profile,
            ignore_user_config=ignore_user_config,
            provider_descriptor=provider_descriptor,
            windows_sandbox_session_override=(
                windows_sandbox_session_override
            ),
            model=model,
            reasoning_effort=reasoning_effort,
        )
        expected_command = build_codex_exec_command(
            command[0],
            options,
            repo_path,
            "workspace-write",
        )
    except (TypeError, ValueError):
        return False
    return command == expected_command


def _observe_codex_auth_mode(executable: str) -> AuthModeObservation:
    """通过 Codex 自带 status 读取脱敏认证类型，不读取或持久化 credential value。"""

    try:
        result = subprocess.run(
            [executable, "login", "status"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return AuthModeObservation(
            mode=None,
            valid=False,
            reason=f"Codex 登录状态检查失败：{type(exc).__name__}",
        )
    if result.returncode != 0:
        return AuthModeObservation(
            mode=None,
            valid=False,
            reason=f"Codex 登录状态命令退出码：{result.returncode}",
        )
    normalized = f"{result.stdout}\n{result.stderr}".lower()
    if "api key" in normalized:
        return AuthModeObservation(
            mode="api_key",
            valid=True,
            reason="Codex 登录状态为 API key",
        )
    if "chatgpt" in normalized:
        return AuthModeObservation(
            mode="chatgpt",
            valid=True,
            reason="Codex 登录状态为 ChatGPT",
        )
    return AuthModeObservation(
        mode=None,
        valid=False,
        reason="Codex 登录状态输出无法识别",
    )


def _parse_contract_bool(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError("布尔合同字段只能是 true 或 false")


def _normalize_codex_version(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized.lower().startswith("codex-cli "):
        normalized = normalized.split(maxsplit=1)[1]
    if normalized.lower().startswith("v"):
        normalized = normalized[1:]
    return normalized or None


def _normalize_contract_label(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空")
    if len(normalized) > 200:
        raise ValueError(f"{field_name} 长度不能超过 200")
    if any(character in normalized for character in ("\r", "\n", "\0")):
        raise ValueError(f"{field_name} 不能包含换行或 NUL")
    return normalized


def _codex_assistant_has_sentinel(output: str, sentinel: str) -> bool:
    """只接受模型输出角色后的 sentinel，避免把用户 prompt 回显当成成功。"""

    lines = [line.strip() for line in output.splitlines()]
    assistant_index: int | None = None
    for index, line in enumerate(lines):
        if line.lower() in {"assistant", "assistant/final", "codex"}:
            assistant_index = index
    if assistant_index is None:
        return False
    return sentinel in lines[assistant_index + 1 :]


def _extract_preflight_diagnostics(
    output: str,
    error: str | None,
) -> list[str]:
    diagnostics: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if (
            lowered.startswith("openai codex v")
            or lowered.startswith("provider:")
            or lowered.startswith("model:")
            or lowered.startswith("reasoning effort:")
            or lowered.startswith("sandbox:")
            or lowered.startswith("warning:")
            or lowered.startswith("error:")
        ):
            diagnostics.append(line[:1000])
    if error:
        diagnostics.append(f"runner error: {error}"[:1000])
    return list(dict.fromkeys(diagnostics))[:20]


def _record_has_provider_failure(record: CaseRecord) -> bool:
    """只根据结构化 Runner 终态和明确错误诊断识别外部阻塞。

    正常 Codex header 本来就包含 provider/model，成功的 WebSocket→HTTPS
    fallback 也可能包含 timeout warning；这些文本不能脱离 execution 终态单独判错。
    """

    terminal_statuses = {
        status
        for status in (record.worker_status, record.reviewer_status)
        if status is not None
    }
    if terminal_statuses & {"timed_out", "stopped"}:
        return True

    failed_execution = False
    for execution in record.runner_executions:
        if execution.parse_error or execution.termination_unconfirmed:
            return True
        if execution.status in {"starting", "running", "stop_requested"}:
            return True
        if execution.status in {"timed_out", "stopped"}:
            return True
        if execution.status == "completed" and execution.returncode not in {None, 0}:
            return True
        if execution.status == "failed":
            failed_execution = True
            if execution.returncode is None:
                return True

    runner_reported_failure = bool(
        terminal_statuses & {"failed", "error"}
    ) or failed_execution
    if not runner_reported_failure:
        return False

    text = " ".join(
        [
            record.reason,
            record.current_step or "",
            *(
                execution.reason or ""
                for execution in record.runner_executions
            ),
            *record.runner_diagnostics,
            *record.warnings,
        ]
    )
    return (
        record.current_step
        in {
            "runner_error",
            "worker_error",
            "reviewer_error",
            "timed_out",
            "stopped",
        }
        or _has_explicit_provider_failure_text(text)
    )


def _is_provider_error(error: Exception) -> bool:
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
        return True
    return _has_explicit_provider_failure_text(
        f"{type(error).__name__}: {error}"
    )


def _has_explicit_provider_failure_text(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in (
            "runner error",
            "runner_error",
            "provider error",
            "provider failed",
            "provider unavailable",
            "provider is unavailable",
            "provider not supported",
            "provider is not supported",
            "model unavailable",
            "model is unavailable",
            "model not supported",
            "model is not supported",
            "unknown model",
            "model does not exist",
            "rate limit",
            "too many requests",
            "service unavailable",
            "network error",
            "connection refused",
            "connection reset",
            "dns resolution",
            "authentication failed",
            "unauthorized",
        )
    )


def _require_clean_project() -> None:
    status = _git_output(PROJECT_ROOT, "status", "--porcelain")
    if status:
        raise RuntimeError(
            "真实 Gate 4.5 dogfood 要求干净 Git 工作区；"
            "请先提交 harness 与预注册合同。"
        )


def _new_session_root(root: Path, session: str) -> Path:
    normalized = session.strip()
    if (
        not normalized
        or normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
        or ":" in normalized
    ):
        raise ValueError("session 名称不合法")
    root.mkdir(parents=True, exist_ok=True)
    candidate = root / normalized
    if candidate.exists():
        raise FileExistsError(f"session 已存在：{candidate}")
    candidate.mkdir()
    return candidate


def _validated_session_root(root: Path, option: str) -> Path:
    resolved = root.resolve()
    runs_root = (PROJECT_ROOT / "runs").resolve()
    if resolved == runs_root or runs_root in resolved.parents:
        raise ValueError(
            f"{option} 不得指向项目 runs/ 或其子目录"
        )
    return resolved


def _loop_run_ids() -> set[str]:
    runs = PROJECT_ROOT / "runs"
    if not runs.is_dir():
        return set()
    return {
        path.name
        for path in runs.iterdir()
        if path.is_dir() and path.name.endswith("-loop")
    }


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _read_runner_diagnostics(run_dir: Path) -> list[str]:
    """只提取 runner 头部、warning 和 error，避免把完整 prompt 复制进汇总。"""

    diagnostics: list[str] = []
    for path in sorted(
        run_dir.glob("iterations/*/executions/*/process-output.txt")
    ):
        try:
            lines = path.read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines()
        except OSError:
            continue
        for line in lines:
            normalized = line.strip()
            lowered = normalized.lower()
            if (
                lowered.startswith("provider:")
                or lowered.startswith("model:")
                or lowered.startswith("warning:")
                or lowered.startswith("error:")
            ):
                diagnostics.append(normalized[:1000])
    return list(dict.fromkeys(diagnostics))[:20]


def _read_runner_execution_facts(run_dir: Path) -> list[RunnerExecutionFact]:
    """读取 worker/reviewer 的 execution.json，不用日志字符串猜测进程终态。"""

    facts: list[RunnerExecutionFact] = []
    for path in sorted(
        run_dir.glob("iterations/*/executions/*/execution.json")
    ):
        step = path.parent.name
        if step not in {"worker", "reviewer"}:
            continue
        relative = path.relative_to(run_dir).as_posix()
        try:
            lease = ExecutionLease.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            facts.append(
                RunnerExecutionFact(
                    path=relative,
                    step=step,
                    status=None,
                    returncode=None,
                    reason=None,
                    termination_unconfirmed=True,
                    parse_error=redact_text(
                        f"{type(exc).__name__}: {exc}"
                    )[:1000],
                )
            )
            continue
        facts.append(
            RunnerExecutionFact(
                path=relative,
                step=step,
                status=lease.status,
                returncode=lease.returncode,
                reason=lease.reason,
                termination_unconfirmed=lease.termination_unconfirmed,
            )
        )
    return facts


def _yaml_string(value: str) -> str:
    """JSON 字符串也是合法 YAML scalar，可避免 CLI 参数注入新的配置键。"""

    return json.dumps(value, ensure_ascii=False)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        content,
        encoding="utf-8",
        newline="\n",
    )


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
    )


def _git_output(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
    ).stdout.strip()


def _git_lines(repo: Path, *args: str) -> list[str]:
    output = _git_output(repo, *args)
    return [line for line in output.splitlines() if line.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
