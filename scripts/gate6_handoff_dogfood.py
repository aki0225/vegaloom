from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from vega.execution_control import (  # noqa: E402
    ExecutionController,
    ExecutionLease,
    RunnerExecutionContext,
)
from vega.goal_handoff import GoalHandoffArtifactInput  # noqa: E402
from vega.goal_handoff import GoalHandoffInput  # noqa: E402
from vega.goal_runtime import GoalRuntime  # noqa: E402
from vega.project_config import (  # noqa: E402
    CodexExecOptions,
    CodexProviderDescriptor,
)
from vega.redaction import redact_text, write_redacted_json, write_redacted_text  # noqa: E402
from vega.runner import CodexExecRunner, RunnerResult  # noqa: E402
from vega.loop_step_result import hash_command  # noqa: E402


RunnerMode = Literal["fake", "real"]

GATE_NAME = "gate-6"
FROZEN_CASE_PATH = PROJECT_ROOT / "eval" / "gate-6" / "handoff-case.json"
FROZEN_CASE_SHA256 = "84bbdadb73eb85a088c597f9fafe76e525729a99e7007d861b6a3236921e7270"
BASELINE_TAG = "gate-6-r4-pre-run-v1"
CONSUMED_TAG = "gate-6-r4-consumed-v1"
PROVIDER = "sandboxproxy"
PROVIDER_BASE_URL = "http://127.0.0.1:18080/v1"
PROVIDER_WIRE_API = "responses"
MODEL = "sandbox-model"
REASONING_EFFORT = "high"
AUTH_MODE = "chatgpt"
CODEX_VERSION = "0.144.5"
WORKER_EPOCH_A = "gate6-epoch-a"
WORKER_EPOCH_B = "gate6-epoch-b"
SESSION_A = "gate6-session-a"
SESSION_B = "gate6-session-b"
HANDOFF_VERSION = "v0001"
CONTEXT_MAX_CHARS = 12_000
PROVIDER_SESSION_HARD_LIMIT = 3
AUTOMATIC_RETRIES = 0
TIMEOUT_SECONDS = 300
PREFLIGHT_SENTINEL = "VEGA_GATE_6_PREFLIGHT_OK"
SESSION_A_SENTINEL = "VEGA_GATE_6_SESSION_A_OK"
SESSION_B_SENTINEL = "VEGA_GATE_6_SESSION_B_OK"
IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,80}$")
TOKEN_COUNT_PATTERN = re.compile(r"tokens used\s*[:\r\n]+\s*([0-9][0-9,]*)", re.IGNORECASE)
SOURCE_CHAT_CANARY = "GATE6_SOURCE_CHAT_PRIVATE_CANARY_7f2a9d4c"

INITIAL_SOURCE = """def normalize_label(value: str) -> str:
    return value.strip().lower().replace(" ", "-")
"""

SESSION_A_SOURCE = """from __future__ import annotations

import re


def normalize_label(value: str) -> str:
    \"\"\"将单个标签规范化为稳定的小写连字符形式。\"\"\"

    if not isinstance(value, str):
        raise TypeError("value 必须是字符串")
    normalized = re.sub(r"[ -]+", "-", value.strip().lower())
    if not normalized:
        raise ValueError("标签不能为空")
    return normalized
"""

SESSION_B_SOURCE = """from __future__ import annotations

import re


def normalize_label(value: str) -> str:
    \"\"\"将单个标签规范化为稳定的小写连字符形式。\"\"\"

    if not isinstance(value, str):
        raise TypeError("value 必须是字符串")
    normalized = re.sub(r"[ -]+", "-", value.strip().lower())
    if not normalized:
        raise ValueError("标签不能为空")
    return normalized


def normalize_labels(values: list[str]) -> list[str]:
    \"\"\"按首次出现顺序返回去重后的规范化标签。\"\"\"

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_label(value)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
"""

PHASE_A_TESTS = """from src.labels import normalize_label


def test_normalize_label_basic() -> None:
    assert normalize_label(" Release Candidate ") == "release-candidate"
"""

CHECKPOINT_01_SPEC = """# Synthetic checkpoint 01

完善 `normalize_label`：

- 去除首尾空白并转为小写；
- 连续空格或连字符折叠为单个连字符；
- 空字符串或全空白字符串抛出 `ValueError`；
- 不新增第三方依赖；
- 只修改 `src/labels.py`。
"""

CHECKPOINT_02_SPEC = """# Synthetic checkpoint 02

在 `src/labels.py` 中新增 `normalize_labels(values)`：

- 逐项复用 `normalize_label`，不能复制第二套规范化逻辑；
- 规范化后的重复标签只保留第一次；
- 输出顺序与规范化后首次出现顺序一致；
- 任一非法空标签沿用 `normalize_label` 的 `ValueError`；
- 不新增第三方依赖；
- 只修改 `src/labels.py`；
- 不自动 commit、push 或发布。
"""

SESSION_A_PROMPT = """你正在执行 synthetic Gate 6 的 Session A。

目标：分两个 checkpoint 完成稳定的标签规范化与批量去重能力。

本 Session 只执行 checkpoint 01，只允许修改 `src/labels.py`：

- 去除首尾空白并转为小写；
- 连续空格或连字符折叠为单个连字符；
- 空字符串或全空白字符串抛出 `ValueError`；
- 运行 `python -m pytest -q`；
- 不修改测试、策略、README 或 Git 配置；
- 不新增依赖、提交、push、release 或 accepted memory；
- 不读取任何 source chat、canary、memory 或当前 synthetic fixture 之外的数据。

完成后只输出固定标记：VEGA_GATE_6_SESSION_A_OK
"""


@dataclass(frozen=True)
class Fixture:
    root: Path
    repo: Path
    workspace: Path
    source_chat: Path
    memory_ledger: Path
    source_chat_text: str
    memory_ledger_text: str
    canary_sha256: str
    initial_head: str


@dataclass
class CallRecord:
    role: str
    session_id: str
    status: str
    elapsed_seconds: float
    prompt_chars: int
    provider_session: bool
    execution_ref: str
    tokens_used: int | None = None
    command: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class HarnessState:
    runner_mode: RunnerMode
    output_dir: Path
    fixture: Fixture | None = None
    goal_run: Path | None = None
    handoff_sha256: str | None = None
    context_sha256: str | None = None
    context_status: str | None = None
    calls: list[CallRecord] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    phase: str = "created"
    decision: str = "blocked"
    baseline_sha: str | None = None
    consumed_tag: str | None = None
    sensitive_guard: "SensitiveFileGuard | None" = None
    sensitive_guard_mode: str = "not-active"


class Gate6Failure(RuntimeError):
    """Gate 6 合同失败，必须 fail-closed 且不得重试。"""


class Gate6Blocked(Gate6Failure):
    """外部身份、provider 或证据不足，保留现场并停止后续 session。"""


class SensitiveFileGuard:
    """在真实 provider 调用期间阻止其他进程打开敏感 fixture 文件。"""

    def __init__(self, fixture: Fixture) -> None:
        self.paths = (fixture.source_chat, fixture.memory_ledger)
        self.handles: list[int] = []
        self.active = False

    def acquire(self) -> None:
        if os.name != "nt":
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        create_file.restype = ctypes.c_void_p
        invalid_handle = ctypes.c_void_p(-1).value
        for path in self.paths:
            handle = create_file(
                str(path),
                0x80000000,
                0,
                None,
                3,
                0x80,
                None,
            )
            if handle == invalid_handle:
                self.close()
                error = ctypes.get_last_error()
                raise Gate6Blocked(
                    f"无法建立敏感 fixture 独占读取保护：{path.name} "
                    f"(winerror={error})"
                )
            self.handles.append(int(handle))
        self.active = True

    def close(self) -> None:
        if not self.handles:
            self.active = False
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        for handle in self.handles:
            close_handle(ctypes.c_void_p(handle))
        self.handles.clear()
        self.active = False


class SessionBudget:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0

    def reserve(self, role: str) -> int:
        if self.used >= self.limit:
            raise Gate6Failure(
                f"provider session budget exceeded before {role}: "
                f"{self.used}/{self.limit}"
            )
        self.used += 1
        return self.used


class DeterministicFakeRunner:
    """用同一 execution contract 模拟三次 fresh runner 调用。"""

    def __init__(self, role: str) -> None:
        self.role = role

    def build_command(self, repo_path: Path, sandbox: str) -> list[str]:
        del repo_path, sandbox
        return [f"gate-6-fake-{self.role}"]

    def execution_identity(self, sandbox: str) -> dict[str, str]:
        return {
            "kind": "DeterministicFakeRunner",
            "role": self.role,
            "sandbox": sandbox,
            "ephemeral": "true",
        }

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context: RunnerExecutionContext | None = None,
    ) -> RunnerResult:
        if execution_context is None:
            raise Gate6Failure(f"fake {self.role} 缺少 execution context")
        controller = ExecutionController(execution_context)
        command = self.build_command(repo_path, sandbox)
        controller.prepare(command, timeout_seconds)
        output = ""
        try:
            if self.role == "preflight":
                output = f"assistant\n{PREFLIGHT_SENTINEL}\n"
            elif self.role == "session-a":
                _assert_prompt_safe(prompt, repo_path)
                repo_path.joinpath("src", "labels.py").write_text(
                    SESSION_A_SOURCE,
                    encoding="utf-8",
                    newline="\n",
                )
                output = f"assistant\n{SESSION_A_SENTINEL}\n"
            elif self.role == "session-b":
                _assert_prompt_safe(prompt, repo_path)
                current = repo_path.joinpath("src", "labels.py").read_text(
                    encoding="utf-8"
                )
                if "def normalize_label" not in current:
                    raise Gate6Failure("Session B 未从当前 workspace 看到 checkpoint 01 实现")
                repo_path.joinpath("src", "labels.py").write_text(
                    SESSION_B_SOURCE,
                    encoding="utf-8",
                    newline="\n",
                )
                output = f"assistant\n{SESSION_B_SENTINEL}\n"
            else:
                raise Gate6Failure(f"未知 fake role: {self.role}")
            controller.output_path.parent.mkdir(parents=True, exist_ok=True)
            controller.output_path.write_text(output, encoding="utf-8", newline="\n")
            controller.finish("success", reason=None, returncode=0)
            return RunnerResult(status="success", output=output, command=command)
        except Exception as exc:
            controller.output_path.parent.mkdir(parents=True, exist_ok=True)
            controller.output_path.write_text(output, encoding="utf-8", newline="\n")
            controller.finish(
                "error",
                reason=f"{type(exc).__name__}: {redact_text(str(exc))}",
                returncode=1,
            )
            raise


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output_root = _project_path(args.output_root, "output root")
    fixture_root = _project_path(args.fixture_root, "fixture root")
    session_name = args.session or f"gate6-{args.runner}-{time.strftime('%Y%m%d-%H%M%S')}"
    if not IDENTIFIER_PATTERN.fullmatch(session_name):
        raise SystemExit("--session 只能包含小写字母、数字、点、下划线和连字符")
    output_dir = output_root / session_name
    if output_dir.exists():
        raise SystemExit(f"输出目录已存在，为避免覆盖证据而停止：{output_dir}")
    output_dir.mkdir(parents=True, exist_ok=False)
    state = HarnessState(runner_mode=args.runner, output_dir=output_dir)
    try:
        if args.runner == "real" and not args.confirm_real:
            raise Gate6Blocked("real mode 需要显式提供 --confirm-real，避免误触 provider")
        if args.runner == "real":
            state.baseline_sha = _assert_real_baseline()
            state.consumed_tag = _claim_real_execution(state.baseline_sha)
        _run_harness(state, fixture_root=fixture_root, confirm_real=args.confirm_real)
    except Exception as exc:  # noqa: BLE001 - CLI 终态必须固化
        state.failures.append(f"{type(exc).__name__}: {redact_text(str(exc))}")
        state.phase = f"failed:{state.phase}"
        state.decision = (
            "blocked"
            if isinstance(exc, Gate6Blocked) or not isinstance(exc, Gate6Failure)
            else "fail"
        )
        _persist_evidence(state)
        print(f"Gate 6：{state.decision}")
        print(f"脱敏证据：{_display_path(output_dir)}")
        return 1
    finally:
        if state.sensitive_guard is not None:
            state.sensitive_guard.close()
    state.decision = (
        "reuse-independent-of-langgraph"
        if args.runner == "real"
        else "fake-passed"
    )
    _persist_evidence(state)
    print(f"Gate 6：{state.decision}")
    print(f"脱敏证据：{_display_path(output_dir)}")
    return 0


def _run_harness(
    state: HarnessState,
    *,
    fixture_root: Path,
    confirm_real: bool,
) -> None:
    _assert_frozen_case()
    fixture = _create_fixture(fixture_root, state.output_dir.name)
    state.fixture = fixture
    memory_before = fixture.memory_ledger.read_bytes()
    source_chat_before = fixture.source_chat.read_bytes()
    if state.runner_mode == "real":
        state.sensitive_guard = SensitiveFileGuard(fixture)
        state.sensitive_guard.acquire()
        state.sensitive_guard_mode = (
            "windows-share-deny" if os.name == "nt" else "not-supported"
        )
    budget = SessionBudget(PROVIDER_SESSION_HARD_LIMIT)
    goal_run = _create_goal(fixture)
    state.goal_run = goal_run
    state.phase = "preflight"

    if state.runner_mode == "real":
        runner = _build_real_runner()
        _run_preflight(state, runner, budget, fixture)
    else:
        _run_fake_call(
            state,
            DeterministicFakeRunner("preflight"),
            budget,
            fixture,
            prompt=f"只输出固定标记 {PREFLIGHT_SENTINEL}，不得修改文件。",
            role="preflight",
            session_id="gate6-preflight",
        )
        _assert_clean_repo(fixture.repo, fixture.initial_head)

    if not confirm_real and state.runner_mode == "real":
        raise Gate6Blocked("real mode 未确认")

    state.phase = "session-a"
    runner_a = (
        _build_real_runner()
        if state.runner_mode == "real"
        else DeterministicFakeRunner("session-a")
    )
    prompt_a = SESSION_A_PROMPT
    baseline_a = _git_worktree_snapshot(fixture.repo)
    git_guard_a = _git_guard_snapshot(fixture.repo)
    result_a = _run_worker_call(
        state,
        runner_a,
        budget,
        fixture,
        prompt=prompt_a,
        role="session-a",
        session_id=SESSION_A,
        sentinel=SESSION_A_SENTINEL,
    )
    if result_a.status != "success":
        raise Gate6Failure("Session A Runner 未成功")
    _assert_worker_scope(
        fixture.repo,
        baseline_paths=baseline_a,
        baseline_git_guard=git_guard_a,
        initial_head=fixture.initial_head,
    )
    _verify_phase_a(fixture.repo)
    evidence_a = _write_checkpoint_evidence(
        fixture,
        checkpoint="01",
        verification="python -m pytest -q + deterministic checkpoint 01 assertions",
        note="synthetic checkpoint 01：normalize_label 已通过确定性验收。",
    )
    _complete_checkpoint_01(state, fixture, evidence_a)
    _create_handoff(state, fixture)
    context_text = _compile_context(state, fixture)
    state.context_sha256 = _sha256_text(context_text)

    state.phase = "session-b"
    runner_b = (
        _build_real_runner()
        if state.runner_mode == "real"
        else DeterministicFakeRunner("session-b")
    )
    prompt_b = _build_session_b_prompt(context_text)
    _assert_prompt_safe(prompt_b, fixture.repo, fixture)
    baseline_b = _git_worktree_snapshot(fixture.repo)
    git_guard_b = _git_guard_snapshot(fixture.repo)
    result_b = _run_worker_call(
        state,
        runner_b,
        budget,
        fixture,
        prompt=prompt_b,
        role="session-b",
        session_id=SESSION_B,
        sentinel=SESSION_B_SENTINEL,
    )
    if result_b.status != "success":
        raise Gate6Failure("Session B Runner 未成功")
    _assert_worker_scope(
        fixture.repo,
        baseline_paths=baseline_b,
        baseline_git_guard=git_guard_b,
        initial_head=fixture.initial_head,
    )
    _verify_phase_b(fixture.repo)
    evidence_b = _write_checkpoint_evidence(
        fixture,
        checkpoint="02",
        verification="python -m pytest -q + deterministic checkpoint 02 assertions",
        note="synthetic checkpoint 02：fresh Session B 已完成批量接口验收。",
    )
    _complete_checkpoint_02(state, fixture, evidence_b)
    if budget.used != PROVIDER_SESSION_HARD_LIMIT:
        raise Gate6Failure(
            f"session slot 使用数不等于固定上限：{budget.used}/{PROVIDER_SESSION_HARD_LIMIT}"
        )
    if len({SESSION_A, SESSION_B}) != 2:
        raise Gate6Failure("Session A/B identity 不唯一")
    if state.sensitive_guard is None:
        if fixture.memory_ledger.read_bytes() != memory_before:
            raise Gate6Failure("Session A/B 修改了 accepted memory ledger")
        if fixture.source_chat.read_bytes() != source_chat_before:
            raise Gate6Failure("Session A/B 修改了 source chat fixture")
    elif not state.sensitive_guard.active:
        raise Gate6Failure("sensitive fixture 独占读取保护未保持到终态")
    state.phase = "completed"


def _create_fixture(fixture_root: Path, session_name: str) -> Fixture:
    root = fixture_root / session_name
    if root.exists():
        raise Gate6Blocked(f"fixture 目录已存在：{root}")
    repo = root / "repo"
    workspace = root / "workspace"
    repo.mkdir(parents=True)
    workspace.mkdir(parents=True)
    _run_git(repo, "init")
    _run_git(repo, "config", "core.autocrlf", "false")
    _write(repo / ".gitignore", "__pycache__/\n.pytest_cache/\n")
    _write(repo / "AGENTS.md", "# Synthetic policy\n\n- 只修改 src/labels.py。\n")
    _write(
        repo / ".vega.yaml",
        "version: 1\nverification:\n  commands:\n    - python -m pytest -q\n",
    )
    _write(
        repo / "docs" / "PRODUCT-CONTRACT.md",
        "# Synthetic product contract\n\n- 不自动提交、推送或发布。\n",
    )
    _write(repo / "README.md", "# Synthetic Gate 6 Fixture\n")
    _write(repo / "src" / "__init__.py", "")
    _write(repo / "src" / "labels.py", INITIAL_SOURCE)
    _write(repo / "tests" / "test_labels.py", PHASE_A_TESTS)
    _write(repo / "CHECKPOINT-01.md", CHECKPOINT_01_SPEC)
    _write(repo / "CHECKPOINT-02.md", CHECKPOINT_02_SPEC)
    _run_git(
        repo,
        "add",
        "--",
        ".gitignore",
        ".vega.yaml",
        "AGENTS.md",
        "README.md",
        "docs/PRODUCT-CONTRACT.md",
        "CHECKPOINT-01.md",
        "CHECKPOINT-02.md",
        "src",
        "tests",
    )
    _run_git(
        repo,
        "-c",
        "user.email=gate6@example.invalid",
        "-c",
        "user.name=Gate 6 Synthetic",
        "commit",
        "-m",
        "create synthetic gate 6 fixture",
    )
    source_chat = workspace / "source-session-a.chat"
    _write(source_chat, f"这是不可出站的 source chat canary：{SOURCE_CHAT_CANARY}\n")
    memory_ledger = workspace / "memory" / "accepted-memory.json"
    _write(memory_ledger, '{"accepted":["synthetic pre-existing memory"]}\n')
    return Fixture(
        root=root,
        repo=repo,
        workspace=workspace,
        source_chat=source_chat,
        memory_ledger=memory_ledger,
        source_chat_text=source_chat.read_text(encoding="utf-8"),
        memory_ledger_text=memory_ledger.read_text(encoding="utf-8"),
        canary_sha256=_sha256_text(SOURCE_CHAT_CANARY),
        initial_head=_git_output(repo, "rev-parse", "HEAD"),
    )


def _assert_frozen_case() -> None:
    if not FROZEN_CASE_PATH.is_file():
        raise Gate6Failure(f"冻结 fixture 缺失：{FROZEN_CASE_PATH}")
    if _sha256_file(FROZEN_CASE_PATH) != FROZEN_CASE_SHA256:
        raise Gate6Failure("Gate 6 handoff-case.json SHA-256 与冻结值不一致")
    try:
        payload = json.loads(FROZEN_CASE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Gate6Failure("Gate 6 handoff-case.json 无法解析") from exc
    expected = {
        "case_id": "gate6-goal-handoff-labels-v1",
        "goal": {
            "objective": "分两个 checkpoint 完成稳定的标签规范化与批量去重能力。",
            "non_goals": [
                "不新增第三方依赖。",
                "不修改 Python 之外的项目配置。",
                "不自动 commit、push 或发布。",
            ],
            "success_conditions": [
                "单值标签规范化满足 checkpoint 01 的全部测试。",
                "批量标签规范化与去重满足 checkpoint 02 的全部测试。",
                "Session B 不依赖 Session A 完整聊天。",
                "handoff、workspace、policy 和 evidence identity 全部一致。",
                "不写 accepted memory。",
            ],
        },
        "initial_files": {
            "src/labels.py": 'def normalize_label(value: str) -> str:\n'
            '    return value.strip().lower().replace(" ", "-")\n',
            "tests/test_labels.py": (
                "from src.labels import normalize_label\n\n\n"
                "def test_normalize_label_basic() -> None:\n"
                '    assert normalize_label(" Release Candidate ") == '
                '"release-candidate"\n'
            ),
        },
        "checkpoint_01": {
            "task": (
                "完善 normalize_label：连续空格或连字符折叠为单个连字符，"
                "空字符串抛出 ValueError。"
            ),
            "acceptance": [
                "去除首尾空白并转为小写。",
                "连续空格或连字符折叠为单个连字符。",
                "空字符串或全空白字符串抛出 ValueError。",
                "不新增第三方依赖。",
            ],
            "verification_command": "python -m pytest -q",
        },
        "handoff": {
            "source_session_id": SESSION_A,
            "consumer_session_id": SESSION_B,
            "next_action": (
                "新增 normalize_labels(values)，复用 normalize_label，"
                "按首次出现顺序去重。"
            ),
            "hard_constraints": [
                "必须复用 normalize_label，不能复制第二套规范化逻辑。",
                "批量结果必须保留规范化后首次出现的顺序。",
                "不新增第三方依赖。",
                "不自动 commit、push 或发布。",
            ],
            "verified_facts": [
                "checkpoint 01 的单值标签规范化测试已通过。",
                "normalize_label 已拒绝空字符串并折叠连续空格或连字符。",
            ],
            "failed_approaches": [
                '直接使用 str.replace(" ", "-") 不能处理连续空格和连续连字符。'
            ],
            "open_questions": [
                "输入中出现多个等价标签时，输出应保留哪一个位置？验收固定为首次出现。"
            ],
            "source_chat_canary": SOURCE_CHAT_CANARY,
        },
        "checkpoint_02": {
            "task": (
                "新增 normalize_labels(values)：逐项调用 normalize_label，"
                "并按首次出现顺序去重。"
            ),
            "acceptance": [
                "逐项复用 normalize_label。",
                "规范化后的重复标签只保留第一次。",
                "输出顺序与规范化后首次出现顺序一致。",
                "任一非法空标签沿用 normalize_label 的 ValueError。",
                "不新增第三方依赖。",
            ],
            "verification_command": "python -m pytest -q",
        },
        "final_expected_api": [
            "src.labels.normalize_label",
            "src.labels.normalize_labels",
        ],
        "provider_budget": {
            "preflight": 1,
            "session_a": 1,
            "session_b": 1,
            "hard_limit": 3,
            "automatic_retries": 0,
        },
    }
    observed = {
        key: payload.get(key)
        for key in (
            "case_id",
            "goal",
            "initial_files",
            "checkpoint_01",
            "handoff",
            "checkpoint_02",
            "final_expected_api",
            "provider_budget",
        )
    }
    if observed != expected:
        raise Gate6Failure("Gate 6 harness 与冻结 fixture 字段不一致")
    if INITIAL_SOURCE != expected["initial_files"]["src/labels.py"]:
        raise Gate6Failure("Gate 6 synthetic initial source 与 fixture 不一致")
    if PHASE_A_TESTS != expected["initial_files"]["tests/test_labels.py"]:
        raise Gate6Failure("Gate 6 synthetic initial tests 与 fixture 不一致")


def _create_goal(fixture: Fixture) -> Path:
    runtime = GoalRuntime(fixture.workspace)
    goal = runtime.start(
        fixture.repo,
        "\n".join(
            [
                "# Synthetic Gate 6 Goal",
                "",
                "Objective: 分两个 checkpoint 完成稳定的标签规范化与批量去重能力。",
                "",
                "Non-goals:",
                "- 不新增第三方依赖。",
                "- 不修改 Python 之外的项目配置。",
                "- 不自动 commit、push 或发布。",
                "",
                "Success conditions:",
                "- 单值标签规范化满足 checkpoint 01 的全部测试。",
                "- 批量标签规范化与去重满足 checkpoint 02 的全部测试。",
                "- Session B 不依赖 Session A 完整聊天。",
                "- handoff、workspace、policy 和 evidence identity 全部一致。",
                "- 不写 accepted memory。",
            ]
        ),
        "synthetic-gate-6",
        "default",
    )
    runtime.step(goal.name)
    return goal


def _complete_checkpoint_01(state: HarnessState, fixture: Fixture, evidence: Path) -> None:
    if state.goal_run is None:
        raise Gate6Failure("goal run 缺失")
    runtime = GoalRuntime(fixture.workspace)
    runtime.attach(
        state.goal_run.name,
        "01",
        str(evidence),
        "manual",
        "synthetic checkpoint 01 verification evidence",
    )
    runtime.checkpoint_done(
        state.goal_run.name,
        "01",
        note="synthetic checkpoint 01 已由 harness 验证。",
        allow_manual_evidence=True,
    )


def _complete_checkpoint_02(state: HarnessState, fixture: Fixture, evidence: Path) -> None:
    if state.goal_run is None:
        raise Gate6Failure("goal run 缺失")
    runtime = GoalRuntime(fixture.workspace)
    runtime.step(state.goal_run.name)
    runtime.attach(
        state.goal_run.name,
        "02",
        str(evidence),
        "manual",
        "synthetic checkpoint 02 verification evidence",
    )
    runtime.checkpoint_done(
        state.goal_run.name,
        "02",
        note="synthetic checkpoint 02 已由 fresh Session B 验证。",
        allow_manual_evidence=True,
    )


def _create_handoff(state: HarnessState, fixture: Fixture) -> None:
    if state.goal_run is None:
        raise Gate6Failure("goal run 缺失")
    input_path = fixture.workspace / "handoff-input.json"
    payload = GoalHandoffInput(
        handoff_version=1,
        source_worker_epoch=WORKER_EPOCH_A,
        target_worker_epoch=WORKER_EPOCH_B,
        source_session_id=SESSION_A,
        next_action="新增 normalize_labels(values)，复用 normalize_label，按首次出现顺序去重。",
        hard_constraints=[
            "必须复用 normalize_label，不能复制第二套规范化逻辑。",
            "批量结果必须保留规范化后首次出现的顺序。",
            "不新增第三方依赖。",
            "不自动 commit、push 或发布。",
        ],
        verified_facts=[
            "checkpoint 01 的单值标签规范化测试已通过。",
            "normalize_label 已拒绝空字符串并折叠连续空格或连字符。",
        ],
        failed_approaches=[
            "直接使用 str.replace(\" \", \"-\") 不能处理连续空格和连续连字符。",
        ],
        open_questions=[
            "输入中出现多个等价标签时，输出应保留哪一个位置？验收固定为首次出现。",
        ],
        authoritative_artifacts=[
            GoalHandoffArtifactInput(scope="repo", path="checkpoint-01-evidence.json"),
            GoalHandoffArtifactInput(scope="repo", path="CHECKPOINT-02.md"),
        ],
        memory_mode="off",
        source_chat_included=False,
    )
    write_redacted_json(input_path, payload.model_dump(mode="json"))
    runtime = GoalRuntime(fixture.workspace)
    runtime.handoff(state.goal_run.name, "01", str(input_path))
    handoff_path = (
        state.goal_run
        / "checkpoints"
        / "01"
        / "handoffs"
        / HANDOFF_VERSION
        / "checkpoint-handoff.json"
    )
    if not handoff_path.is_file():
        raise Gate6Failure("checkpoint handoff artifact 缺失")
    state.handoff_sha256 = _json_field(handoff_path, "handoff_sha256")


def _compile_context(state: HarnessState, fixture: Fixture) -> str:
    if state.goal_run is None:
        raise Gate6Failure("goal run 缺失")
    runtime = GoalRuntime(fixture.workspace)
    runtime.handoff_context(
        state.goal_run.name,
        "01",
        HANDOFF_VERSION,
        SESSION_B,
        WORKER_EPOCH_B,
        CONTEXT_MAX_CHARS,
    )
    context_path = (
        state.goal_run
        / "checkpoints"
        / "01"
        / "handoffs"
        / HANDOFF_VERSION
        / "consumers"
        / SESSION_B
        / "checkpoint-context.md"
    )
    context = context_path.read_text(encoding="utf-8")
    if "source_chat_included=false" not in context or "memory_mode=off" not in context:
        raise Gate6Failure("compiled context 缺少 source chat/memory 安全边界")
    if (
        state.fixture
        and state.fixture.source_chat_text.split("：", 1)[-1].strip() in context
    ):
        raise Gate6Failure("compiled context 泄露 source chat canary")
    state.context_status = "ready"
    return context


def _run_preflight(
    state: HarnessState,
    runner: CodexExecRunner,
    budget: SessionBudget,
    fixture: Fixture,
) -> None:
    executable = runner.executable
    version = _codex_version(executable)
    if version != CODEX_VERSION:
        raise Gate6Blocked(
            f"Codex CLI 版本不匹配：expected={CODEX_VERSION}, observed={version}"
        )
    auth = _codex_auth_mode(executable)
    if auth != AUTH_MODE:
        raise Gate6Blocked(
            f"Codex 认证模式不匹配：expected={AUTH_MODE}, observed={auth}"
        )
    prompt = f"这是只读 preflight，只输出固定标记：{PREFLIGHT_SENTINEL}"
    result = _run_fake_or_real_call(
        state,
        runner,
        budget,
        fixture,
        prompt=prompt,
        role="preflight",
        session_id="gate6-preflight",
        provider_session=True,
    )
    if result.status != "success" or PREFLIGHT_SENTINEL not in result.output:
        raise Gate6Blocked("provider preflight sentinel 或 Runner status 不可信")
    _assert_real_runner_identity(result.output)
    _assert_clean_repo(fixture.repo, fixture.initial_head)


def _run_worker_call(
    state: HarnessState,
    runner: object,
    budget: SessionBudget,
    fixture: Fixture,
    *,
    prompt: str,
    role: str,
    session_id: str,
    sentinel: str,
) -> RunnerResult:
    result = _run_fake_or_real_call(
        state,
        runner,
        budget,
        fixture,
        prompt=prompt,
        role=role,
        session_id=session_id,
        provider_session=state.runner_mode == "real",
    )
    if result.status == "success" and state.runner_mode == "fake" and sentinel not in result.output:
        raise Gate6Failure(f"{role} fake sentinel 缺失")
    if result.status == "success" and state.runner_mode == "real":
        _assert_real_runner_identity(result.output)
    return result


def _run_fake_call(
    state: HarnessState,
    runner: DeterministicFakeRunner,
    budget: SessionBudget,
    fixture: Fixture,
    *,
    prompt: str,
    role: str,
    session_id: str,
) -> RunnerResult:
    return _run_fake_or_real_call(
        state,
        runner,
        budget,
        fixture,
        prompt=prompt,
        role=role,
        session_id=session_id,
        provider_session=False,
    )


def _run_fake_or_real_call(
    state: HarnessState,
    runner: object,
    budget: SessionBudget,
    fixture: Fixture,
    *,
    prompt: str,
    role: str,
    session_id: str,
    provider_session: bool,
) -> RunnerResult:
    _assert_prompt_safe(prompt, fixture.repo, fixture)
    slot = budget.reserve(role)
    del slot
    execution_dir = state.output_dir / "executions" / role
    command = (
        runner.build_command(fixture.repo, "workspace-write")
        if hasattr(runner, "build_command")
        else []
    )
    command_sha256 = hash_command(command)
    identity = (
        runner.execution_identity("workspace-write")
        if hasattr(runner, "execution_identity")
        else {}
    )
    context = RunnerExecutionContext(
        execution_dir=execution_dir,
        run_id=session_id,
        step=role,
        replay_class="external_non_replayable",
        runner_identity=identity,
        base_head=_git_output(fixture.repo, "rev-parse", "HEAD"),
        command_sha256=command_sha256,
        exclusive_create=True,
    )
    started = time.monotonic()
    error: str | None = None
    try:
        result = runner.run(
            prompt,
            fixture.repo,
            sandbox="workspace-write",
            timeout_seconds=TIMEOUT_SECONDS,
            execution_context=context,
        )
    except Exception as exc:  # noqa: BLE001 - 单次调用失败必须停止
        error = f"{type(exc).__name__}: {redact_text(str(exc))}"
        result = RunnerResult(status="error", output="", error=error, command=command)
    dlp_error: str | None = None
    try:
        _assert_output_safe(result.output, result.error, fixture)
    except Gate6Failure as exc:
        dlp_error = redact_text(str(exc))
        error = dlp_error
    tokens_used = _parse_tokens_used(result.output)
    evidence_issues = _validate_execution(
        execution_dir / "execution.json",
        expected_run_id=session_id,
        expected_step=role,
    )
    if provider_session and tokens_used is None:
        evidence_issues.append("真实 provider token 计数缺失")
    if evidence_issues:
        error = "execution artifact 不可信：" + "；".join(evidence_issues)
    state.calls.append(
        CallRecord(
            role=role,
            session_id=session_id,
            status="error" if dlp_error else result.status,
            elapsed_seconds=round(time.monotonic() - started, 3),
            prompt_chars=len(prompt),
            provider_session=provider_session,
            execution_ref=f"executions/{role}/execution.json",
            tokens_used=tokens_used,
            command=_safe_command(result.command or command),
            error=error or result.error,
        )
    )
    if dlp_error:
        raise Gate6Failure(dlp_error)
    if evidence_issues:
        if state.runner_mode == "real":
            raise Gate6Blocked(error)
        raise Gate6Failure(error)
    if result.status != "success":
        message = f"{role} Runner failed: {redact_text(result.error or result.status)}"
        if state.runner_mode == "real":
            raise Gate6Blocked(message)
        raise Gate6Failure(message)
    return result


def _build_real_runner() -> CodexExecRunner:
    provider = CodexProviderDescriptor(
        name=PROVIDER,
        base_url=PROVIDER_BASE_URL,
        wire_api=PROVIDER_WIRE_API,
        requires_openai_auth=True,
        supports_websockets=False,
    )
    options = CodexExecOptions(
        ignore_user_config=True,
        provider=provider,
        windows_sandbox_session_override="elevated",
        model=MODEL,
        reasoning_effort=REASONING_EFFORT,
        ephemeral=True,
    )
    return CodexExecRunner(
        executable=_resolve_codex_executable("codex"),
        options=options,
    )


def _verify_phase_a(repo: Path) -> None:
    _run_verification(repo)
    namespace = _load_module_namespace(repo)
    normalize_label = namespace["normalize_label"]
    if not callable(normalize_label):
        raise Gate6Failure("checkpoint 01 normalize_label 不可调用")
    if normalize_label("  Release   Candidate---Beta  ") != "release-candidate-beta":
        raise Gate6Failure("checkpoint 01 未折叠连续空格或连字符")
    for value in ("", "   "):
        try:
            normalize_label(value)
        except ValueError:
            continue
        raise Gate6Failure("checkpoint 01 未对空标签抛出 ValueError")


def _verify_phase_b(repo: Path) -> None:
    _run_verification(repo)
    namespace = _load_module_namespace(repo)
    normalize_label = namespace["normalize_label"]
    normalize_labels = namespace["normalize_labels"]
    if not callable(normalize_label) or not callable(normalize_labels):
        raise Gate6Failure("checkpoint 02 最终 API 不可调用")
    values = [
        " Release  Candidate ",
        "release-candidate",
        " Beta--Label ",
        "beta label",
    ]
    original = list(values)
    calls: list[str] = []

    def tracked_normalize_label(value: str) -> str:
        calls.append(value)
        return normalize_label(value)

    namespace["normalize_label"] = tracked_normalize_label
    if normalize_labels(values) != ["release-candidate", "beta-label"]:
        raise Gate6Failure("checkpoint 02 未按首次出现顺序去重")
    if calls != values:
        raise Gate6Failure("checkpoint 02 未逐项复用 normalize_label")
    if values != original:
        raise Gate6Failure("checkpoint 02 修改了调用方 list")
    try:
        normalize_labels(["valid", "   "])
    except ValueError:
        pass
    else:
        raise Gate6Failure("checkpoint 02 未沿用 normalize_label 的 ValueError")


def _run_verification(repo: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=repo,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise Gate6Failure(
            "synthetic verification 失败："
            f"{redact_text((result.stdout + result.stderr)[-1200:])}"
        )


def _load_module_namespace(repo: Path) -> dict[str, object]:
    source = repo.joinpath("src", "labels.py").read_text(encoding="utf-8")
    namespace: dict[str, object] = {}
    exec(compile(source, "src/labels.py", "exec"), namespace, namespace)
    return namespace


def _write_checkpoint_evidence(
    fixture: Fixture,
    *,
    checkpoint: str,
    verification: str,
    note: str,
) -> Path:
    path = fixture.repo / f"checkpoint-{checkpoint}-evidence.json"
    write_redacted_json(
        path,
        {
            "schema_version": 1,
            "checkpoint": checkpoint,
            "status": "passed",
            "verification": verification,
            "note": note,
            "repo_head": _git_output(fixture.repo, "rev-parse", "HEAD"),
            "source_sha256": _sha256_file(fixture.repo / "src" / "labels.py"),
        },
    )
    return path


def _build_session_b_prompt(context: str) -> str:
    return "\n".join(
        [
            "你正在执行 synthetic Gate 6 的 fresh Session B。",
            "下面的 checkpoint context 是唯一的跨 session handoff 输入。",
            "请先读取 context 中列出的权威 artifact 和当前 workspace，再完成下一步。",
            "",
            context,
            "",
            "除上述 compiled context、其列出的权威 artifact 和当前 workspace 外，"
            "不得使用其他业务结论或 source session 内容。",
            f"完成后只输出固定标记：{SESSION_B_SENTINEL}",
        ]
    )


def _assert_worker_scope(
    repo: Path,
    *,
    baseline_paths: dict[str, str],
    baseline_git_guard: dict[str, str],
    initial_head: str,
) -> None:
    if _git_output(repo, "rev-parse", "HEAD") != initial_head:
        raise Gate6Failure("worker 不得在 synthetic fixture 中创建 commit")
    current_paths = _git_worktree_snapshot(repo)
    unexpected = sorted(
        set(current_paths) - set(baseline_paths) - {"src/labels.py"}
    )
    if unexpected:
        raise Gate6Failure(
            "worker 修改了未授权 fixture 路径：" + ", ".join(unexpected)
        )
    changed_protected = sorted(
        path
        for path, digest in baseline_paths.items()
        if path != "src/labels.py" and current_paths.get(path) != digest
    )
    if changed_protected:
        raise Gate6Failure(
            "worker 篡改了已有权威 artifact：" + ", ".join(changed_protected)
        )
    current_git_guard = _git_guard_snapshot(repo)
    changed_git_metadata = sorted(
        key
        for key, digest in baseline_git_guard.items()
        if current_git_guard.get(key) != digest
    )
    if changed_git_metadata:
        raise Gate6Failure(
            "worker 修改了 Git 元数据或 ignored 状态："
            + ", ".join(changed_git_metadata)
        )


def _git_worktree_snapshot(repo: Path) -> dict[str, str]:
    paths: dict[str, str] = {}
    for line in _git_output(repo, "status", "--short").splitlines():
        if len(line) >= 3:
            relative = line[2:].strip()
            path = repo / relative
            paths[relative] = (
                _sha256_file(path)
                if path.is_file()
                else "[missing-or-non-file]"
            )
    return paths


def _git_guard_snapshot(repo: Path) -> dict[str, str]:
    git_dir_value = Path(_git_output(repo, "rev-parse", "--git-dir"))
    git_dir = (
        git_dir_value
        if git_dir_value.is_absolute()
        else repo / git_dir_value
    ).resolve()
    metadata_entries: list[str] = []
    if git_dir.is_dir():
        for path in sorted(git_dir.rglob("*")):
            if path.is_symlink():
                try:
                    relative = path.relative_to(git_dir).as_posix()
                    metadata_entries.append(
                        f"{relative}\0link:{path.readlink()}"
                    )
                except (OSError, ValueError):
                    metadata_entries.append("<unreadable-link>")
            elif path.is_file():
                try:
                    relative = path.relative_to(git_dir).as_posix()
                    metadata_entries.append(
                        f"{relative}\0{_sha256_file(path)}"
                    )
                except (OSError, ValueError):
                    metadata_entries.append("<unreadable-metadata>")
    elif git_dir.is_file():
        metadata_entries.append(f"gitdir-file\0{_sha256_file(git_dir)}")
    return {
        "git_metadata": _sha256_text("\n".join(metadata_entries)),
        "status_ignored": _git_ignored_digest(repo),
        "refs": _git_binary_digest(repo, "show-ref"),
        "remote": _git_binary_digest(repo, "remote", "-v"),
    }


def _git_ignored_digest(repo: Path) -> str:
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "-z",
        ],
        cwd=repo,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise Gate6Failure("Git ignored 文件清单读取失败")
    filtered_paths: list[bytes] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = PurePosixPath(
            raw_path.decode("utf-8", errors="surrogateescape")
        )
        if "__pycache__" in path.parts or ".pytest_cache" in path.parts:
            continue
        filtered_paths.append(raw_path)
    return _sha256_bytes(b"\0".join(filtered_paths) + result.stderr)


def _git_binary_digest(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise Gate6Failure(
            f"Git guard 命令失败：git {' '.join(args)}"
        )
    return _sha256_bytes(result.stdout + result.stderr)


def _assert_prompt_safe(prompt: str, repo: Path, fixture: Fixture | None = None) -> None:
    forbidden = [
        str(PROJECT_ROOT.resolve()),
        ".env",
        "Authorization:",
        "Bearer ",
        "api_key",
        "cookie",
        "source-session-a.chat",
        "accepted-memory",
    ]
    if fixture is not None:
        forbidden.append(fixture.source_chat_text.strip())
        forbidden.append(fixture.memory_ledger_text.strip())
    for marker in forbidden:
        if marker and marker.lower() in prompt.lower():
            raise Gate6Failure(f"prompt 出站边界失败：发现禁止标记 {redact_text(marker[:40])}")
    if str(repo.resolve()) in prompt:
        raise Gate6Failure("prompt 不得包含 fixture 绝对路径")


def _assert_output_safe(
    output: str,
    error: str | None,
    fixture: Fixture,
) -> None:
    combined = f"{output}\n{error or ''}"
    lowered_combined = combined.lower()
    forbidden_content = (
        fixture.source_chat_text.strip(),
        fixture.memory_ledger_text.strip(),
    )
    if any(
        marker and marker.lower() in lowered_combined
        for marker in forbidden_content
    ):
        raise Gate6Failure("Runner 输出包含禁止的 source chat、memory 或真实项目路径")
    normalized_combined = combined.replace("\\", "/").casefold()
    for allowed_path in sorted(
        (fixture.root.resolve(), fixture.repo.resolve()),
        key=lambda item: len(item.as_posix()),
        reverse=True,
    ):
        normalized_combined = normalized_combined.replace(
            allowed_path.as_posix().casefold(),
            "",
        )
    project_root = PROJECT_ROOT.resolve().as_posix().casefold()
    if project_root in normalized_combined:
        raise Gate6Failure("Runner 输出包含禁止的 source chat、memory 或真实项目路径")


def _validate_execution(
    path: Path,
    *,
    expected_run_id: str,
    expected_step: str,
) -> list[str]:
    if not path.is_file():
        return ["execution.json 缺失"]
    try:
        lease = ExecutionLease.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"execution.json 无法解析：{type(exc).__name__}"]
    issues: list[str] = []
    if lease.run_id != expected_run_id:
        issues.append("run_id 不一致")
    if lease.step != expected_step:
        issues.append("step 不一致")
    if lease.status != "completed" or lease.returncode != 0:
        issues.append(
            f"终态不成功：status={lease.status!r}, returncode={lease.returncode!r}"
        )
    if lease.termination_unconfirmed:
        issues.append("外部进程终止状态未确认")
    if lease.process_output_sha256 is None:
        issues.append("process output hash 缺失")
    return issues


def _assert_real_runner_identity(output: str) -> None:
    header = _parse_codex_header(output)
    expected = {
        "codex_version": CODEX_VERSION,
        "provider": PROVIDER,
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
    }
    mismatches = [
        f"{key}: expected={value!r}, observed={header.get(key)!r}"
        for key, value in expected.items()
        if header.get(key) != value
    ]
    sandbox = header.get("sandbox")
    if sandbox is None or not sandbox.startswith("workspace-write"):
        mismatches.append(
            f"sandbox: expected='workspace-write', observed={sandbox!r}"
        )
    if mismatches:
        raise Gate6Blocked("Codex live identity 不一致：" + "；".join(mismatches))


def _assert_clean_repo(repo: Path, expected_head: str) -> None:
    if _git_output(repo, "rev-parse", "HEAD") != expected_head:
        raise Gate6Failure("preflight 改变了 synthetic repo HEAD")
    status = _git_output(repo, "status", "--short")
    if status:
        raise Gate6Failure(f"preflight 改变了 synthetic repo：{redact_text(status)}")


def _persist_evidence(state: HarnessState) -> None:
    fixture = state.fixture
    calls = [
        {
            "role": item.role,
            "session_id": item.session_id,
            "status": item.status,
            "elapsed_seconds": item.elapsed_seconds,
            "prompt_chars": item.prompt_chars,
            "provider_session": item.provider_session,
            "execution_ref": item.execution_ref,
            "tokens_used": item.tokens_used,
            "command": item.command,
            "error": item.error,
        }
        for item in state.calls
    ]
    summary = {
        "schema_version": 1,
        "gate": GATE_NAME,
        "runner_mode": state.runner_mode,
        "phase": state.phase,
        "decision": state.decision,
        "baseline_tag": BASELINE_TAG if state.baseline_sha else None,
        "baseline_sha": state.baseline_sha,
        "consumed_tag": state.consumed_tag,
        "provider": PROVIDER,
        "model": MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "auth_mode": AUTH_MODE,
        "ephemeral": True,
        "automatic_retries": AUTOMATIC_RETRIES,
        "frozen_case_path": "eval/gate-6/handoff-case.json",
        "frozen_case_sha256": FROZEN_CASE_SHA256,
        "provider_session_hard_limit": PROVIDER_SESSION_HARD_LIMIT,
        "provider_sessions_used": sum(item["provider_session"] for item in calls),
        "execution_slots_used": len(calls),
        "tokens_used_total": (
            sum(int(item["tokens_used"]) for item in calls if item["tokens_used"] is not None)
            if any(item["tokens_used"] is not None for item in calls)
            else None
        ),
        "runner_invocations": len(calls),
        "session_ids": [SESSION_A, SESSION_B],
        "session_ids_distinct": SESSION_A != SESSION_B,
        "handoff_version": HANDOFF_VERSION,
        "handoff_sha256": state.handoff_sha256,
        "context_status": state.context_status,
        "context_sha256": state.context_sha256,
        "source_chat_included": False,
        "sensitive_fixture_lock": state.sensitive_guard_mode,
        "memory_mode": "off",
        "accepted_memory_writes": 0,
        "canary_sent": False,
        "real_project_data_sent": False,
        "retry_count": 0,
        "calls": calls,
        "fixture_root": _relative_path(fixture.root if fixture else None),
        "goal_run": _relative_path(state.goal_run),
        "canary_sha256": fixture.canary_sha256 if fixture else None,
        "failures": state.failures,
    }
    write_redacted_json(state.output_dir / "summary.json", summary)
    lines = [
        "# Gate 6 Handoff Dogfood Report",
        "",
        f"- mode：`{state.runner_mode}`",
        f"- decision：`{state.decision}`",
        f"- phase：`{state.phase}`",
        f"- baseline：`{BASELINE_TAG}` / `{state.baseline_sha or 'not-checked'}`",
        f"- consumed tag：`{state.consumed_tag or 'not-created'}`",
        f"- provider：`{PROVIDER}`",
        f"- model：`{MODEL}`",
        f"- reasoning：`{REASONING_EFFORT}`",
        f"- auth：`{AUTH_MODE}`",
        "- ephemeral：`true`",
        f"- automatic retries：`{AUTOMATIC_RETRIES}`",
        f"- frozen case SHA-256：`{FROZEN_CASE_SHA256}`",
        f"- provider hard limit：`{PROVIDER_SESSION_HARD_LIMIT}`",
        f"- provider sessions used：`{summary['provider_sessions_used']}`",
        f"- execution slots used：`{summary['execution_slots_used']}`",
        f"- tokens used total：`{summary['tokens_used_total']}`",
        f"- runner invocations：`{summary['runner_invocations']}`",
        f"- Session A/B distinct：`{summary['session_ids_distinct']}`",
        f"- handoff：`{HANDOFF_VERSION}` / `{state.handoff_sha256 or 'not-created'}`",
        f"- context：`{state.context_status or 'not-created'}` / `{state.context_sha256 or 'not-created'}`",
        "- source chat included：`false`",
        (
            "- sensitive fixture lock：`"
            f"{summary['sensitive_fixture_lock']}`"
        ),
        "- memory mode：`off`",
        "- accepted memory writes：`0`",
        "- canary sent：`false`",
        "- real project data sent：`false`",
        "- retries：`0`",
        "",
        "## Calls",
        "",
    ]
    for call in calls:
        lines.append(
            f"- `{call['role']}` status=`{call['status']}` "
            f"session=`{call['session_id']}` tokens=`{call['tokens_used']}` "
            f"execution=`{call['execution_ref']}`"
        )
    if state.failures:
        lines.extend(["", "## Failures", ""])
        lines.extend(f"- {item}" for item in state.failures)
    write_redacted_text(state.output_dir / "report.md", "\n".join(lines) + "\n")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gate 6 synthetic handoff double-session dogfood")
    parser.add_argument(
        "--runner",
        choices=("fake", "real"),
        default="fake",
        help="fake 为确定性本地模式；real 需要 --confirm-real。",
    )
    parser.add_argument("--confirm-real", action="store_true")
    parser.add_argument("--session", help="输出和 fixture 的小写中性 session 名称")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / ".local-validation" / "gate-6",
    )
    parser.add_argument(
        "--fixture-root",
        type=Path,
        default=PROJECT_ROOT / ".tmp" / "gate-6",
    )
    return parser.parse_args(argv)


def _project_path(path: Path, label: str) -> Path:
    resolved = path.resolve()
    root = PROJECT_ROOT.resolve()
    if resolved != root and root not in resolved.parents:
        raise SystemExit(f"{label} 必须位于 Vega 仓库内：{resolved}")
    return resolved


def _assert_real_baseline() -> str:
    try:
        tag_type = _git_output(PROJECT_ROOT, "cat-file", "-t", BASELINE_TAG)
        expected = _git_output(
            PROJECT_ROOT,
            "rev-parse",
            f"{BASELINE_TAG}^{{commit}}",
        )
    except subprocess.CalledProcessError as exc:
        raise Gate6Blocked(
            f"真实执行冻结 tag 不可解析：{BASELINE_TAG}"
        ) from exc
    if tag_type != "tag":
        raise Gate6Blocked(f"{BASELINE_TAG} 必须是 annotated tag")
    head = _git_output(PROJECT_ROOT, "rev-parse", "HEAD")
    if head != expected:
        raise Gate6Blocked(
            f"HEAD 与 {BASELINE_TAG} 不一致：expected={expected}, observed={head}"
        )
    status = _git_output(
        PROJECT_ROOT,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    if status:
        raise Gate6Blocked("真实 Gate 6 只能从 clean checkout 启动")
    try:
        autocrlf = _git_output(
            PROJECT_ROOT,
            "config",
            "--local",
            "--get",
            "core.autocrlf",
        ).lower()
    except subprocess.CalledProcessError as exc:
        raise Gate6Blocked("clean checkout 未显式设置 core.autocrlf=false") from exc
    if autocrlf != "false":
        raise Gate6Blocked(
            f"clean checkout core.autocrlf 必须为 false，observed={autocrlf!r}"
        )
    consumed = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/tags/{CONSUMED_TAG}"],
        cwd=PROJECT_ROOT,
        timeout=30,
        check=False,
    )
    if consumed.returncode == 0:
        raise Gate6Blocked(f"真实 Gate 6 已被 consumed tag 锁定：{CONSUMED_TAG}")
    if consumed.returncode not in {0, 1}:
        raise Gate6Blocked("无法检查 Gate 6 consumed tag")
    return head


def _claim_real_execution(baseline_sha: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Vega Gate 6",
            "-c",
            "user.email=gate6@example.invalid",
            "tag",
            "-a",
            CONSUMED_TAG,
            "-m",
            (
                "Consume Gate 6 real provider budget for "
                f"{BASELINE_TAG} ({baseline_sha})"
            ),
            baseline_sha,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise Gate6Blocked(
            "无法原子创建 Gate 6 consumed tag："
            f"{redact_text(result.stderr.strip())}"
        )
    observed = _git_output(
        PROJECT_ROOT,
        "rev-parse",
        f"{CONSUMED_TAG}^{{commit}}",
    )
    if observed != baseline_sha:
        raise Gate6Failure("Gate 6 consumed tag 未绑定 execution baseline")
    return CONSUMED_TAG


def _run_git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
        check=True,
    )


def _git_output(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
        check=True,
    )
    return result.stdout.strip()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _json_field(path: Path, field_name: str) -> str:
    value = json.loads(path.read_text(encoding="utf-8")).get(field_name)
    if not isinstance(value, str) or not value:
        raise Gate6Failure(f"{path.name} 缺少 {field_name}")
    return value


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _resolve_codex_executable(executable: str) -> str:
    resolved = shutil.which(executable)
    if not resolved:
        raise Gate6Blocked(f"未找到 {executable}")
    return resolved


def _codex_version(executable: str) -> str:
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
            timeout=30,
            check=False,
        )
    except OSError as exc:
        raise Gate6Blocked(
            f"Codex 版本检查启动失败：{type(exc).__name__}: {exc}"
        ) from exc
    if result.returncode != 0:
        raise Gate6Blocked("Codex 版本检查失败")
    normalized = (result.stdout or result.stderr).strip().lower()
    for prefix in ("openai codex v", "openai codex ", "codex-cli ", "codex "):
        if normalized.startswith(prefix):
            normalized = normalized.removeprefix(prefix)
            break
    return normalized.removeprefix("v").strip()


def _codex_auth_mode(executable: str) -> str | None:
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
    except OSError as exc:
        raise Gate6Blocked(
            f"Codex 登录状态检查启动失败：{type(exc).__name__}: {exc}"
        ) from exc
    if result.returncode != 0:
        raise Gate6Blocked("Codex login status 检查失败")
    output = f"{result.stdout}\n{result.stderr}".lower()
    if "api key" in output:
        return "api_key"
    if "chatgpt" in output:
        return "chatgpt"
    return None


def _parse_codex_header(output: str) -> dict[str, str]:
    header: dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if lowered == "user":
            break
        if lowered.startswith("openai codex v"):
            header["codex_version"] = line.removeprefix("OpenAI Codex v").strip()
            continue
        if ":" not in line:
            continue
        key, value = (item.strip() for item in line.split(":", 1))
        target = {
            "provider": "provider",
            "model": "model",
            "reasoning effort": "reasoning_effort",
            "sandbox": "sandbox",
        }.get(key.lower())
        if target is not None:
            header[target] = value
    return header


def _parse_tokens_used(output: str) -> int | None:
    matches = TOKEN_COUNT_PATTERN.findall(output)
    if not matches:
        return None
    return int(matches[-1].replace(",", ""))


def _safe_command(command: list[str]) -> list[str]:
    return [redact_text(item) for item in command if item not in {"-", ""}]


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return "[outside-project]"


def _relative_path(path: Path | None) -> str | None:
    return _display_path(path) if path is not None else None


if __name__ == "__main__":
    raise SystemExit(main())
