from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from vega.execution_control import is_process_alive
from vega.project_config import load_project_config


def _has_langgraph_extra() -> bool:
    return all(
        importlib.util.find_spec(module_name) is not None
        for module_name in (
            "langgraph",
            "langgraph.checkpoint",
            "langgraph.checkpoint.sqlite",
        )
    )


pytestmark = [
    pytest.mark.requires_langgraph,
    pytest.mark.skipif(
        not _has_langgraph_extra(),
        reason="需要安装 `vegaloom[langgraph]` 可选依赖",
    ),
]

SCRIPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "langgraph_core_dogfood.py"
)


def _load_harness():
    importlib.import_module("langgraph")
    importlib.import_module("langgraph.checkpoint.sqlite")
    spec = importlib.util.spec_from_file_location(
        "langgraph_core_dogfood",
        SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _preflight_output(
    harness,
    *,
    provider: str = "sandbox-provider",
    sentinel: bool = True,
) -> str:
    lines = [
        "OpenAI Codex v0.144.5",
        "--------",
        "model: gpt-5.6",
        f"provider: {provider}",
        "sandbox: workspace-write [workdir, /tmp, $TMPDIR]",
        "reasoning effort: high",
        "--------",
        "user",
        f"请输出 {harness.PREFLIGHT_SENTINEL}",
        "assistant",
    ]
    if sentinel:
        lines.append(harness.PREFLIGHT_SENTINEL)
    return "\n".join(lines) + "\n"


class _StubPreflightRunner:
    def __init__(
        self,
        harness,
        output: str,
        *,
        runner_profile: str | None = "sandbox-provider",
        ignore_user_config: bool = False,
        provider_descriptor=None,
        windows_sandbox_session_override: str | None = None,
        modify_repo: bool = False,
        write_execution: bool = True,
    ) -> None:
        self.harness = harness
        self.output = output
        self.runner_profile = runner_profile
        self.ignore_user_config = ignore_user_config
        self.provider_descriptor = provider_descriptor
        self.windows_sandbox_session_override = (
            windows_sandbox_session_override
        )
        self.modify_repo = modify_repo
        self.write_execution = write_execution
        self.run_count = 0

    def build_command(self, repo_path: Path, sandbox: str) -> list[str]:
        options = self.harness._codex_exec_options(
            profile=self.runner_profile,
            ignore_user_config=self.ignore_user_config,
            provider_descriptor=self.provider_descriptor,
            windows_sandbox_session_override=(
                self.windows_sandbox_session_override
            ),
            model="gpt-5.6",
            reasoning_effort="high",
        )
        return self.harness.build_codex_exec_command(
            "codex",
            options,
            repo_path,
            sandbox,
        )

    def run(
        self,
        prompt: str,
        repo_path: Path,
        *,
        sandbox: str,
        timeout_seconds: int,
        execution_context=None,
    ):
        self.run_count += 1
        command = self.build_command(repo_path, sandbox)
        controller = None
        if self.write_execution:
            assert execution_context is not None
            controller = self.harness.ExecutionController(execution_context)
            controller.prepare(command, timeout_seconds)
        if self.modify_repo:
            repo_path.joinpath("README.md").write_text(
                "意外修改\n",
                encoding="utf-8",
                newline="\n",
            )
        if controller is not None:
            controller.finish("success", reason=None, returncode=0)
        return self.harness.RunnerResult(
            status="success",
            output=self.output,
            command=command,
        )


def _run_stub_preflight(
    harness,
    tmp_path: Path,
    *,
    output: str,
    runner_profile: str | None = "sandbox-provider",
    ignore_user_config: bool = False,
    expected_provider: str = "sandbox-provider",
    expected_auth_mode=None,
    provider_descriptor=None,
    windows_sandbox_session_override: str | None = None,
    modify_repo: bool = False,
    write_execution: bool = True,
    auth_mode_observer=None,
):
    session_root = tmp_path / "fixture-session"
    output_dir = tmp_path / "output-session"
    session_root.mkdir()
    output_dir.mkdir()
    return harness.run_provider_preflight(
        session_root=session_root,
        output_dir=output_dir,
        session="test-session",
        runner_profile=runner_profile,
        ignore_user_config=ignore_user_config,
        expected_auth_mode=expected_auth_mode,
        provider_descriptor=provider_descriptor,
        windows_sandbox_session_override=windows_sandbox_session_override,
        expected_provider=expected_provider,
        expected_codex_version="0.144.5",
        model="gpt-5.6",
        reasoning_effort="high",
        timeout_seconds=30,
        runner=_StubPreflightRunner(
            harness,
            output,
            runner_profile=runner_profile,
            ignore_user_config=ignore_user_config,
            provider_descriptor=provider_descriptor,
            windows_sandbox_session_override=(
                windows_sandbox_session_override
            ),
            modify_repo=modify_repo,
            write_execution=write_execution,
        ),
        auth_mode_observer=auth_mode_observer,
    )


def test_fixture_pair_has_same_low_risk_head_and_explicit_high_risk_policy(
    tmp_path: Path,
) -> None:
    harness = _load_harness()

    fixtures = harness.prepare_fixtures(
        tmp_path / "fixtures",
        "contract",
        model="gpt-test",
        worker_reasoning="medium",
        reviewer_reasoning="high",
        runner_profile="sandbox-provider",
    )

    assert fixtures["linear-low"].head == fixtures["graph-low"].head
    assert fixtures["graph-crash-hitl"].head != fixtures["linear-low"].head
    low_config = (
        Path(fixtures["linear-low"].repo_path)
        / ".vega.yaml"
    ).read_text(encoding="utf-8")
    high_config = (
        Path(fixtures["graph-crash-hitl"].repo_path)
        / ".vega.yaml"
    ).read_text(encoding="utf-8")
    assert "high_paths: []" in low_config
    assert "    - src/slugify.py" in high_config
    assert "max_changed_files: 1" in low_config
    assert "forbid_new_dependencies: true" in low_config
    assert low_config.count('profile: "sandbox-provider"') == 2
    assert high_config.count('profile: "sandbox-provider"') == 2
    assert "windows_sandbox_session_override" not in low_config
    assert "windows_sandbox_session_override" not in high_config


def test_fixture_rejects_r5_unicode_separator_regression(
    tmp_path: Path,
) -> None:
    harness = _load_harness()
    fixtures = harness.prepare_fixtures(
        tmp_path / "fixtures",
        "unicode-separator-regression",
        model="gpt-test",
        worker_reasoning="medium",
        reviewer_reasoning="high",
        runner_profile="sandbox-provider",
    )
    repo = Path(fixtures["linear-low"].repo_path)
    test_source = repo.joinpath(
        "tests",
        "test_slugify.py",
    ).read_text(encoding="utf-8")
    r5_regression = '''from __future__ import annotations

import re
import unicodedata


def normalize_slug(value: str) -> str:
    """把任意标题规范化为 ASCII 小写 slug。"""

    if not isinstance(value, str):
        raise TypeError("value 必须是字符串")
    ascii_value = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")
'''

    assert "test_preserves_non_ascii_separator_boundaries" in test_source
    repo.joinpath("src", "slugify.py").write_text(
        r5_regression,
        encoding="utf-8",
        newline="\n",
    )
    failed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=repo,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        check=False,
    )

    assert failed.returncode != 0
    assert "test_preserves_non_ascii_separator_boundaries" in (
        failed.stdout + failed.stderr
    )

    repo.joinpath("src", "slugify.py").write_text(
        harness.SOLVED_SLUGIFY,
        encoding="utf-8",
        newline="\n",
    )
    passed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=repo,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        check=False,
    )

    assert passed.returncode == 0, passed.stdout + passed.stderr


def test_fixture_config_records_ignore_user_config_and_windows_override(
    tmp_path: Path,
) -> None:
    harness = _load_harness()

    fixtures = harness.prepare_fixtures(
        tmp_path / "fixtures",
        "ignore-user-config",
        model="gpt-test",
        worker_reasoning="medium",
        reviewer_reasoning="high",
        ignore_user_config=True,
        windows_sandbox_session_override="elevated",
    )

    for fixture in fixtures.values():
        config = Path(fixture.repo_path).joinpath(
            ".vega.yaml"
        ).read_text(encoding="utf-8")
        assert config.count("ignore_user_config: true") == 2
        assert (
            config.count(
                'windows_sandbox_session_override: "elevated"'
            )
            == 2
        )
        assert "profile:" not in config


def test_fixture_config_records_explicit_provider_for_both_roles(
    tmp_path: Path,
) -> None:
    harness = _load_harness()
    provider = harness.CodexProviderDescriptor(
        name="sandboxproxy",
        base_url="http://127.0.0.1:18080/v1/",
        wire_api="responses",
        requires_openai_auth=True,
        supports_websockets=False,
    )

    fixtures = harness.prepare_fixtures(
        tmp_path / "fixtures",
        "explicit-provider",
        model="sandbox-model",
        worker_reasoning="high",
        reviewer_reasoning="high",
        ignore_user_config=True,
        provider_descriptor=provider,
        windows_sandbox_session_override="elevated",
    )

    for fixture in fixtures.values():
        config_path = Path(fixture.repo_path) / ".vega.yaml"
        config_text = config_path.read_text(encoding="utf-8")
        parsed = load_project_config(Path(fixture.repo_path))
        assert config_text.count("      provider:") == 2
        assert config_text.count('        name: "sandboxproxy"') == 2
        assert (
            config_text.count(
                '        base_url: "http://127.0.0.1:18080/v1"'
            )
            == 2
        )
        assert config_text.count("        requires_openai_auth: true") == 2
        assert config_text.count("        supports_websockets: false") == 2
        assert parsed.runner.codex_exec.worker.provider == provider
        assert parsed.runner.codex_exec.reviewer.provider == provider


def test_codex_options_receive_ignore_user_config_contract(monkeypatch) -> None:
    harness = _load_harness()
    observed: dict[str, object] = {}

    class StubOptions:
        def __init__(self, **kwargs) -> None:
            observed.update(kwargs)

    monkeypatch.setattr(harness, "CodexExecOptions", StubOptions)

    harness._codex_exec_options(
        profile=None,
        ignore_user_config=True,
        windows_sandbox_session_override="elevated",
        model="gpt-5.6",
        reasoning_effort="high",
    )

    assert observed == {
        "profile": None,
        "ignore_user_config": True,
        "provider": None,
        "windows_sandbox_session_override": "elevated",
        "model": "gpt-5.6",
        "reasoning_effort": "high",
        "ephemeral": True,
    }


def test_preflight_header_parser_only_reads_codex_header() -> None:
    harness = _load_harness()
    output = (
        _preflight_output(harness)
        + "provider: forged-provider\n"
        + "model: forged-model\n"
    )

    assert harness._parse_codex_header(output) == {
        "codex_version": "0.144.5",
        "model": "gpt-5.6",
        "provider": "sandbox-provider",
        "sandbox": "workspace-write [workdir, /tmp, $TMPDIR]",
        "reasoning_effort": "high",
    }


def test_preflight_sentinel_parser_accepts_codex_role_without_prompt_echo() -> None:
    harness = _load_harness()
    prompt_echo_only = "\n".join(
        [
            "user",
            harness.PREFLIGHT_SENTINEL,
            "",
        ]
    )
    codex_output = "\n".join(
        [
            prompt_echo_only,
            "codex",
            harness.PREFLIGHT_SENTINEL,
            "tokens used",
            "4,618",
            "",
        ]
    )

    assert not harness._codex_assistant_has_sentinel(
        prompt_echo_only,
        harness.PREFLIGHT_SENTINEL,
    )
    assert harness._codex_assistant_has_sentinel(
        codex_output,
        harness.PREFLIGHT_SENTINEL,
    )


def test_preflight_success_requires_expected_identity_and_sentinel(
    tmp_path: Path,
) -> None:
    harness = _load_harness()

    record = _run_stub_preflight(
        harness,
        tmp_path,
        output=_preflight_output(harness),
    )

    assert record.status == "passed"
    assert record.observed_provider == "sandbox-provider"
    assert record.observed_model == "gpt-5.6"
    assert record.observed_codex_version == "0.144.5"
    assert record.sentinel_found is True
    assert record.command_shape_valid is True
    assert record.repo_clean is True
    assert record.execution_valid is True
    assert record.runner_config_mode == "profile"
    assert record.ignore_user_config is False
    assert record.runner_identity["profile"] == "sandbox-provider"
    assert record.runner_identity["ignore_user_config"] == "false"


def test_preflight_ignore_user_config_records_identity_and_command(
    tmp_path: Path,
) -> None:
    harness = _load_harness()

    record = _run_stub_preflight(
        harness,
        tmp_path,
        output=_preflight_output(harness),
        runner_profile=None,
        ignore_user_config=True,
        windows_sandbox_session_override="elevated",
    )

    assert record.status == "passed"
    assert record.runner_profile is None
    assert record.ignore_user_config is True
    assert record.windows_sandbox_session_override == "elevated"
    assert record.runner_config_mode == "ignore_user_config"
    assert record.runner_identity["config_mode"] == "ignore_user_config"
    assert record.runner_identity["ignore_user_config"] == "true"
    assert (
        record.runner_identity["windows_sandbox_session_override"]
        == "elevated"
    )
    assert "profile" not in record.runner_identity
    assert record.command.count("--ignore-user-config") == 1
    ignore_index = record.command.index("--ignore-user-config")
    assert record.command[ignore_index + 1 : ignore_index + 3] == [
        "--config",
        'windows.sandbox="elevated"',
    ]
    assert "--profile" not in record.command
    assert record.command_shape_valid is True
    assert record.execution_valid is True


def test_preflight_explicit_provider_records_identity_and_command(
    tmp_path: Path,
) -> None:
    harness = _load_harness()
    provider = harness.CodexProviderDescriptor(
        name="sandboxproxy",
        base_url="http://127.0.0.1:18080/v1",
        wire_api="responses",
        requires_openai_auth=True,
        supports_websockets=False,
    )

    record = _run_stub_preflight(
        harness,
        tmp_path,
        output=_preflight_output(harness, provider="sandboxproxy"),
        runner_profile=None,
        ignore_user_config=True,
        expected_provider="sandboxproxy",
        expected_auth_mode="api_key",
        provider_descriptor=provider,
        windows_sandbox_session_override="elevated",
        auth_mode_observer=lambda _: harness.AuthModeObservation(
            mode="api_key",
            valid=True,
            reason="模拟 API key 登录",
        ),
    )

    assert record.status == "passed"
    assert record.expected_auth_mode == "api_key"
    assert record.observed_auth_mode == "api_key"
    assert record.auth_mode_valid is True
    assert record.runner_config_mode == "isolated_provider"
    assert record.provider_descriptor_sha256 == (
        harness.codex_provider_descriptor_sha256(provider)
    )
    assert record.runner_identity["provider"] == "sandboxproxy"
    assert (
        record.runner_identity["provider_base_url"]
        == "http://127.0.0.1:18080/v1"
    )
    assert 'model_provider="sandboxproxy"' in record.command
    assert (
        'model_providers.sandboxproxy.base_url="http://127.0.0.1:18080/v1"'
        in record.command
    )
    assert record.command_shape_valid is True
    assert record.execution_valid is True


def test_preflight_auth_mismatch_stops_before_provider_call(
    tmp_path: Path,
) -> None:
    harness = _load_harness()
    session_root = tmp_path / "fixture-session"
    output_dir = tmp_path / "output-session"
    session_root.mkdir()
    output_dir.mkdir()
    provider = harness.CodexProviderDescriptor(
        name="sandboxproxy",
        base_url="http://127.0.0.1:18080/v1",
    )
    runner = _StubPreflightRunner(
        harness,
        _preflight_output(harness, provider="sandboxproxy"),
        runner_profile=None,
        ignore_user_config=True,
        provider_descriptor=provider,
    )

    record = harness.run_provider_preflight(
        session_root=session_root,
        output_dir=output_dir,
        session="auth-mismatch",
        runner_profile=None,
        ignore_user_config=True,
        expected_auth_mode="api_key",
        provider_descriptor=provider,
        expected_provider="sandboxproxy",
        expected_codex_version="0.144.5",
        model="gpt-5.6",
        reasoning_effort="high",
        timeout_seconds=30,
        runner=runner,
        auth_mode_observer=lambda _: harness.AuthModeObservation(
            mode="chatgpt",
            valid=True,
            reason="模拟 ChatGPT 登录",
        ),
    )

    assert record.status == "blocked"
    assert record.runner_status == "not_started_auth_mismatch"
    assert record.observed_auth_mode == "chatgpt"
    assert record.auth_mode_valid is False
    assert "认证模式不一致" in record.reason
    assert runner.run_count == 0
    assert record.diagnostics == ["provider 调用未启动"]
    assert not output_dir.joinpath(
        "preflight",
        "execution",
        "execution.json",
    ).exists()


def test_command_contract_requires_exact_allowlisted_argv(
    tmp_path: Path,
) -> None:
    harness = _load_harness()
    command = [
        "codex",
        "exec",
        "--cd",
        str(tmp_path.resolve()),
        "--sandbox",
        "workspace-write",
        "--ignore-user-config",
        "--config",
        'windows.sandbox="elevated"',
        "--model",
        "gpt-5.6",
        "--config",
        'model_reasoning_effort="high"',
        "--ephemeral",
        "-",
    ]
    contract = {
        "repo_path": tmp_path,
        "runner_profile": None,
        "ignore_user_config": True,
        "windows_sandbox_session_override": "elevated",
        "model": "gpt-5.6",
        "reasoning_effort": "high",
    }

    assert harness._command_matches_runner_contract(command, **contract)
    assert not harness._command_matches_runner_contract(
        command,
        **{
            **contract,
            "windows_sandbox_session_override": None,
        },
    )
    invalid_commands = [
        command[:-1] + ["--config", 'windows.sandbox="elevated"', "-"],
        (
            command[:7]
            + ['--config=windows.sandbox="elevated"']
            + command[9:]
        ),
        command[:-1]
        + ["--config", 'model_reasoning_effort="medium"', "-"],
        command[:7] + command[9:],
        command[:-1] + ["--config", 'web_search="live"', "-"],
        command[:7]
        + ["-c", 'windows.sandbox="elevated"']
        + command[9:],
        command[:7] + ['windows.sandbox="elevated"'] + command[9:],
        command[:-1] + ["--full-auto", "-"],
        command[:-1]
        + ["--dangerously-bypass-approvals-and-sandbox", "-"],
        command[:5] + ["read-only"] + command[6:],
        command[:3] + [str(tmp_path.joinpath("other").resolve())] + command[4:],
    ]
    for invalid_command in invalid_commands:
        assert not harness._command_matches_runner_contract(
            invalid_command,
            **contract,
        )


def test_command_contract_binds_explicit_provider_descriptor(
    tmp_path: Path,
) -> None:
    harness = _load_harness()
    provider = harness.CodexProviderDescriptor(
        name="sandboxproxy",
        base_url="http://127.0.0.1:18080/v1",
    )
    options = harness._codex_exec_options(
        profile=None,
        ignore_user_config=True,
        provider_descriptor=provider,
        model="gpt-5.6",
        reasoning_effort="high",
    )
    command = harness.build_codex_exec_command(
        "codex",
        options,
        tmp_path,
        "workspace-write",
    )
    contract = {
        "repo_path": tmp_path,
        "runner_profile": None,
        "ignore_user_config": True,
        "provider_descriptor": provider,
        "model": "gpt-5.6",
        "reasoning_effort": "high",
    }

    assert harness._command_matches_runner_contract(command, **contract)
    assert not harness._command_matches_runner_contract(
        command,
        **{
            **contract,
            "provider_descriptor": None,
        },
    )
    tampered = list(command)
    base_url_index = tampered.index(
        'model_providers.sandboxproxy.base_url="http://127.0.0.1:18080/v1"'
    )
    tampered[base_url_index] = (
        'model_providers.sandboxproxy.base_url="http://127.0.0.1:18081/v1"'
    )
    assert not harness._command_matches_runner_contract(
        tampered,
        **contract,
    )


def test_preflight_compares_redacted_command_and_raw_command_hash(
    tmp_path: Path,
) -> None:
    harness = _load_harness()
    fake_secret = "sk-preflight-fake-secret-123456"
    secret_root = tmp_path / fake_secret
    secret_root.mkdir()

    record = _run_stub_preflight(
        harness,
        secret_root,
        output=_preflight_output(harness),
        runner_profile=f"profile-{fake_secret}",
    )

    execution_path = secret_root.joinpath(
        "output-session",
        "preflight",
        "execution",
        "execution.json",
    )
    execution_text = execution_path.read_text(encoding="utf-8")
    assert record.status == "passed"
    assert record.command_shape_valid is True
    assert record.execution_valid is True
    assert fake_secret not in execution_text
    assert "[REDACTED]" in execution_text


def test_preflight_missing_sentinel_is_blocked(tmp_path: Path) -> None:
    harness = _load_harness()

    record = _run_stub_preflight(
        harness,
        tmp_path,
        output=_preflight_output(harness, sentinel=False),
    )

    assert record.status == "blocked"
    assert "sentinel 缺失" in record.reason


def test_preflight_provider_mismatch_is_blocked(tmp_path: Path) -> None:
    harness = _load_harness()

    record = _run_stub_preflight(
        harness,
        tmp_path,
        output=_preflight_output(harness, provider="sandboxproxy"),
    )

    assert record.status == "blocked"
    assert record.observed_provider == "sandboxproxy"
    assert "provider identity 不一致" in record.reason


def test_preflight_workspace_change_is_blocked(tmp_path: Path) -> None:
    harness = _load_harness()

    record = _run_stub_preflight(
        harness,
        tmp_path,
        output=_preflight_output(harness),
        modify_repo=True,
    )

    assert record.status == "blocked"
    assert record.repo_clean is False
    assert record.repo_status == ["M README.md"]
    assert "工作区副作用" in record.reason


def test_preflight_missing_execution_artifact_is_blocked(
    tmp_path: Path,
) -> None:
    harness = _load_harness()

    record = _run_stub_preflight(
        harness,
        tmp_path,
        output=_preflight_output(harness),
        write_execution=False,
    )

    assert record.status == "blocked"
    assert record.execution_valid is False
    assert record.execution_issues == ["execution.json 缺失"]
    assert "execution artifact 不可信" in record.reason


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (
            ["--runner", "real"],
            "必须在 --runner-profile 与 --ignore-user-config 中二选一",
        ),
        (
            [
                "--runner",
                "real",
                "--runner-profile",
                "sandbox-provider",
                "--ignore-user-config",
            ],
            "且不可同时设置",
        ),
        (
            ["--runner", "fake", "--ignore-user-config"],
            "--ignore-user-config 仅支持 --runner real",
        ),
        (
            [
                "--runner",
                "fake",
                "--windows-sandbox-session-override",
                "elevated",
            ],
            "--windows-sandbox-session-override 仅支持 --runner real",
        ),
        (
            [
                "--runner",
                "real",
                "--runner-profile",
                "sandbox-provider",
                "--windows-sandbox-session-override",
                "elevated",
            ],
            "仅可与 --ignore-user-config 配合使用",
        ),
        (
            [
                "--runner",
                "real",
                "--windows-sandbox-session-override",
                "elevated",
            ],
            "仅可与 --ignore-user-config 配合使用",
        ),
        (
            ["--runner", "fake", "--preflight-only"],
            "--preflight-only 仅支持 --runner real",
        ),
        (
            [
                "--runner",
                "fake",
                "--provider-base-url",
                "http://127.0.0.1:18080/v1",
            ],
            "显式 provider 参数仅支持 --runner real",
        ),
        (
            [
                "--runner",
                "real",
                "--ignore-user-config",
                "--expected-provider",
                "sandboxproxy",
                "--expected-auth-mode",
                "api_key",
                "--expected-codex-version",
                "0.144.5",
            ],
            "API key + --ignore-user-config 必须显式绑定 loopback provider",
        ),
        (
            [
                "--runner",
                "real",
                "--ignore-user-config",
                "--expected-provider",
                "sandboxproxy",
                "--expected-auth-mode",
                "chatgpt",
                "--expected-codex-version",
                "0.144.5",
                "--provider-base-url",
                "http://127.0.0.1:18080/v1",
            ],
            "显式 provider 参数必须完整提供",
        ),
        (
            [
                "--runner",
                "real",
                "--runner-profile",
                "sandbox-provider",
                "--expected-provider",
                "sandboxproxy",
                "--expected-auth-mode",
                "api_key",
                "--expected-codex-version",
                "0.144.5",
                "--provider-base-url",
                "http://127.0.0.1:18080/v1",
                "--provider-wire-api",
                "responses",
                "--provider-requires-openai-auth",
                "true",
                "--provider-supports-websockets",
                "false",
            ],
            "显式 provider 仅可与 --ignore-user-config 配合使用",
        ),
        (
            [
                "--runner",
                "real",
                "--ignore-user-config",
                "--expected-provider",
                "sandboxproxy",
                "--expected-auth-mode",
                "api_key",
                "--expected-codex-version",
                "0.144.5",
                "--provider-base-url",
                "https://example.com/v1",
                "--provider-wire-api",
                "responses",
                "--provider-requires-openai-auth",
                "true",
                "--provider-supports-websockets",
                "false",
            ],
            "显式 provider 参数不合法",
        ),
    ],
)
def test_cli_rejects_invalid_real_runner_config_contract(
    argv: list[str],
    message: str,
    capsys,
) -> None:
    harness = _load_harness()

    with pytest.raises(SystemExit) as exc_info:
        harness.main(argv)

    assert exc_info.value.code == 2
    assert message in capsys.readouterr().err


def test_cli_windows_override_choices_only_allow_elevated(capsys) -> None:
    harness = _load_harness()

    with pytest.raises(SystemExit) as exc_info:
        harness.main(
            [
                "--windows-sandbox-session-override",
                "administrator",
            ]
        )

    assert exc_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


@pytest.mark.parametrize("root_option", ["--fixture-root", "--output-root"])
def test_cli_rejects_session_roots_inside_runs(
    root_option: str,
    capsys,
) -> None:
    harness = _load_harness()

    with pytest.raises(SystemExit) as exc_info:
        harness.main(
            [
                "--runner",
                "real",
                "--ignore-user-config",
                "--preflight-only",
                "--session",
                "invalid-root-session",
                "--expected-provider",
                "openai",
                "--expected-auth-mode",
                "chatgpt",
                "--expected-codex-version",
                "0.144.4",
                root_option,
                str(harness.PROJECT_ROOT / "runs"),
            ]
        )

    assert exc_info.value.code == 2
    assert f"{root_option} 不得指向项目 runs/" in capsys.readouterr().err


def test_preflight_failure_stops_before_business_runs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    harness = _load_harness()
    run_dogfood_called = False

    def blocked_preflight(**kwargs):
        return harness.PreflightRecord(
            required=True,
            status="blocked",
            reason="模拟 provider mismatch",
            runner_profile="sandbox-provider",
            expected_provider="sandbox-provider",
            expected_codex_version="0.144.5",
            expected_model="gpt-5.6",
            expected_reasoning_effort="high",
        )

    def fail_if_business_runs(**kwargs):
        nonlocal run_dogfood_called
        run_dogfood_called = True
        raise AssertionError("preflight 失败后不应调用 run_dogfood")

    monkeypatch.setattr(harness, "_require_clean_project", lambda: None)
    monkeypatch.setattr(
        harness,
        "run_provider_preflight",
        blocked_preflight,
    )
    monkeypatch.setattr(harness, "run_dogfood", fail_if_business_runs)
    fixture_root = tmp_path / "fixtures"
    output_root = tmp_path / "output"

    exit_code = harness.main(
        [
            "--runner",
            "real",
            "--preflight-only",
            "--session",
            "blocked-session",
            "--runner-profile",
            "sandbox-provider",
            "--expected-provider",
            "sandbox-provider",
            "--expected-auth-mode",
            "chatgpt",
            "--expected-codex-version",
            "0.144.5",
            "--fixture-root",
            str(fixture_root),
            "--output-root",
            str(output_root),
        ]
    )

    summary = json.loads(
        output_root.joinpath(
            "blocked-session",
            "summary.json",
        ).read_text(encoding="utf-8")
    )
    assert exit_code == 1
    assert run_dogfood_called is False
    assert summary["conclusion"] == "blocked"
    assert summary["phase"] == "preflight"
    assert summary["preflight_only"] is True
    assert summary["business_case_count"] == 0
    assert summary["cases"] == []
    assert not fixture_root.joinpath(
        "blocked-session",
        "linear-low",
    ).exists()


def test_preflight_only_persists_passed_evidence_without_business_cases(
    tmp_path: Path,
    monkeypatch,
) -> None:
    harness = _load_harness()
    options_calls: list[dict[str, object]] = []
    preflight_calls: list[dict[str, object]] = []

    def record_options(**kwargs):
        options_calls.append(kwargs)
        return object()

    def passed_preflight(**kwargs):
        preflight_calls.append(kwargs)
        provider = kwargs["provider_descriptor"]
        assert provider is not None
        return harness.PreflightRecord(
            required=True,
            status="passed",
            reason="模拟 preflight 通过",
            runner_profile=None,
            expected_provider="sandboxproxy",
            expected_codex_version="0.144.5",
            expected_model="gpt-5.6",
            expected_reasoning_effort="high",
            expected_auth_mode="api_key",
            observed_auth_mode="api_key",
            auth_mode_valid=True,
            auth_mode_reason="模拟 API key 登录",
            ignore_user_config=True,
            provider_descriptor_sha256=(
                harness.codex_provider_descriptor_sha256(provider)
            ),
            windows_sandbox_session_override="elevated",
            runner_config_mode="isolated_provider",
            runner_identity={
                "runner": "codex-exec",
                "config_mode": "isolated_provider",
                "ignore_user_config": "true",
                "provider": "sandboxproxy",
                "provider_base_url": "http://127.0.0.1:18080/v1",
                "windows_sandbox_session_override": "elevated",
            },
            command=[
                "codex",
                "exec",
                "--ignore-user-config",
                "--config",
                'windows.sandbox="elevated"',
                "-",
            ],
            command_shape_valid=True,
        )

    def reject_business_fixtures(*args, **kwargs):
        raise AssertionError("preflight-only 不得创建业务 fixture")

    def reject_business_runs(**kwargs):
        raise AssertionError("preflight-only 不得调用 worker/reviewer")

    monkeypatch.setattr(harness, "_require_clean_project", lambda: None)
    monkeypatch.setattr(harness, "_codex_exec_options", record_options)
    monkeypatch.setattr(
        harness,
        "run_provider_preflight",
        passed_preflight,
    )
    monkeypatch.setattr(
        harness,
        "_prepare_fixtures_in_session",
        reject_business_fixtures,
    )
    monkeypatch.setattr(harness, "run_dogfood", reject_business_runs)
    fixture_root = tmp_path / "fixtures"
    output_root = tmp_path / "output"

    exit_code = harness.main(
        [
            "--runner",
            "real",
            "--ignore-user-config",
            "--windows-sandbox-session-override",
            "elevated",
            "--preflight-only",
            "--session",
            "preflight-only-session",
            "--expected-provider",
            "sandboxproxy",
            "--expected-auth-mode",
            "api_key",
            "--provider-base-url",
            "http://127.0.0.1:18080/v1",
            "--provider-wire-api",
            "responses",
            "--provider-requires-openai-auth",
            "true",
            "--provider-supports-websockets",
            "false",
            "--expected-codex-version",
            "0.144.5",
            "--fixture-root",
            str(fixture_root),
            "--output-root",
            str(output_root),
        ]
    )

    output_dir = output_root / "preflight-only-session"
    summary = json.loads(
        output_dir.joinpath("summary.json").read_text(encoding="utf-8")
    )
    report = output_dir.joinpath("REPORT.md").read_text(encoding="utf-8")
    assert exit_code == 0
    assert summary["schema_version"] == 5
    assert len(options_calls) == 2
    assert all(
        call["ignore_user_config"] is True
        and call["provider_descriptor"].name == "sandboxproxy"
        and (
            call["provider_descriptor"].base_url
            == "http://127.0.0.1:18080/v1"
        )
        and call["windows_sandbox_session_override"] == "elevated"
        for call in options_calls
    )
    assert len(preflight_calls) == 1
    assert preflight_calls[0]["ignore_user_config"] is True
    assert preflight_calls[0]["expected_auth_mode"] == "api_key"
    assert preflight_calls[0]["provider_descriptor"].name == "sandboxproxy"
    assert (
        preflight_calls[0]["windows_sandbox_session_override"]
        == "elevated"
    )
    assert summary["conclusion"] == "preflight-passed"
    assert summary["phase"] == "preflight_passed"
    assert summary["preflight_only"] is True
    assert summary["ignore_user_config"] is True
    assert summary["expected_auth_mode"] == "api_key"
    assert summary["provider_descriptor"] == {
        "name": "sandboxproxy",
        "base_url": "http://127.0.0.1:18080/v1",
        "wire_api": "responses",
        "requires_openai_auth": True,
        "supports_websockets": False,
    }
    assert summary["windows_sandbox_session_override"] == "elevated"
    assert summary["runner_config_mode"] == "isolated_provider"
    assert summary["business_case_count"] == 0
    assert summary["fixtures"] == {}
    assert summary["cases"] == []
    assert "conclusion：`preflight-passed`" in report
    assert "windows_sandbox_session_override：`elevated`" in report
    assert "preflight-only 已完成，未创建业务 Case。" in report
    assert not fixture_root.joinpath(
        "preflight-only-session",
        "linear-low",
    ).exists()


def test_persist_summary_redacts_new_identity_and_path_fields(
    tmp_path: Path,
) -> None:
    harness = _load_harness()
    fake_secret = "sk-summary-fake-secret-123456"
    output_dir = tmp_path / "summary-output"
    output_dir.mkdir()
    preflight = harness.PreflightRecord(
        required=True,
        status="passed",
        reason=f"provider={fake_secret}",
        runner_profile=None,
        expected_provider=fake_secret,
        expected_codex_version="0.144.4",
        expected_model=f"model-{fake_secret}",
        expected_reasoning_effort="high",
        ignore_user_config=True,
        windows_sandbox_session_override="elevated",
        runner_identity={
            "provider": fake_secret,
            "windows_sandbox_session_override": "elevated",
        },
        fixture_repo=str(tmp_path / fake_secret),
        command=[
            "codex",
            "--ignore-user-config",
            "--config",
            'windows.sandbox="elevated"',
            "--model",
            fake_secret,
        ],
    )
    summary = harness._build_summary(
        session=f"session-{fake_secret}",
        runner_mode="real",
        conclusion="preflight-passed",
        phase="preflight_passed",
        model=f"model-{fake_secret}",
        worker_reasoning="high",
        reviewer_reasoning="high",
        runner_profile=None,
        ignore_user_config=True,
        windows_sandbox_session_override="elevated",
        preflight_only=True,
        expected_provider=fake_secret,
        expected_codex_version="0.144.4",
        timeout_seconds=900,
        preflight_timeout_seconds=180,
        started_at=harness.time.monotonic(),
        preflight=preflight,
        fixtures={},
        cases=[],
    )

    harness._persist_summary(output_dir, summary)

    summary_json = json.loads(
        output_dir.joinpath("summary.json").read_text(encoding="utf-8")
    )
    preflight_json = json.loads(
        output_dir.joinpath("preflight-result.json").read_text(
            encoding="utf-8"
        )
    )
    report = output_dir.joinpath("REPORT.md").read_text(encoding="utf-8")
    persisted = "\n".join(
        (
            json.dumps(summary_json, ensure_ascii=False),
            json.dumps(preflight_json, ensure_ascii=False),
            report,
        )
    )
    assert fake_secret not in persisted
    assert "[REDACTED]" in persisted
    assert summary_json["windows_sandbox_session_override"] == "elevated"
    assert (
        preflight_json["windows_sandbox_session_override"]
        == "elevated"
    )
    assert "windows_sandbox_session_override：`elevated`" in report


def test_fake_mode_never_calls_real_provider(
    tmp_path: Path,
    monkeypatch,
) -> None:
    harness = _load_harness()
    fixture = harness.FixtureRecord(
        name="linear-low",
        repo_path=str(tmp_path / "repo"),
        high_risk=False,
        head="fixture-head",
    )
    passed = harness.CaseRecord(
        name="fake-pass",
        engine="linear",
        runner_mode="fake",
        fixture_head="fixture-head",
        outcome="passed",
    )

    def reject_real_preflight(**kwargs):
        raise AssertionError("fake mode 不得调用真实 provider preflight")

    monkeypatch.setattr(
        harness,
        "run_provider_preflight",
        reject_real_preflight,
    )
    monkeypatch.setattr(
        harness,
        "_prepare_fixtures_in_session",
        lambda *args, **kwargs: {"linear-low": fixture},
    )
    monkeypatch.setattr(
        harness,
        "run_dogfood",
        lambda **kwargs: [passed],
    )
    output_root = tmp_path / "output"

    exit_code = harness.main(
        [
            "--runner",
            "fake",
            "--allow-dirty",
            "--session",
            "fake-session",
            "--fixture-root",
            str(tmp_path / "fixtures"),
            "--output-root",
            str(output_root),
        ]
    )

    summary = json.loads(
        output_root.joinpath(
            "fake-session",
            "summary.json",
        ).read_text(encoding="utf-8")
    )
    assert exit_code == 0
    assert summary["preflight"]["status"] == "not_required_fake"


def test_crash_hitl_recover_uses_exited_process_before_resume(
    tmp_path: Path,
    monkeypatch,
) -> None:
    harness = _load_harness()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(harness, "PROJECT_ROOT", workspace)
    fixture = harness._init_fixture(
        tmp_path / "fixture" / "repo",
        name="graph-crash-hitl",
        high_risk=True,
        model="gpt-test",
        worker_reasoning="medium",
        reviewer_reasoning="high",
        runner_profile=None,
        ignore_user_config=False,
        provider_descriptor=None,
        windows_sandbox_session_override=None,
    )

    record = harness._run_crash_hitl_case(
        "fake",
        fixture,
        timeout_seconds=60,
        actor="test-owner",
    )

    assert record.outcome == "passed", record.reason
    assert record.run_id is not None
    run_dir = workspace / "runs" / record.run_id
    verification = json.loads(
        run_dir.joinpath(
            "iterations",
            "01",
            "executions",
            "verification-01",
            "execution.json",
        ).read_text(encoding="utf-8")
    )
    assert verification["owner_pid"] != os.getpid()
    assert is_process_alive(verification["owner_pid"]) is False
    assert record.worker_start_count == 1
    assert record.decision_count == 1
    assert record.consumption_count == 1


def test_conclusion_prioritizes_safety_then_blocked_then_quality() -> None:
    harness = _load_harness()

    passed = harness.CaseRecord(
        name="passed",
        engine="linear",
        runner_mode="fake",
        fixture_head="a",
        outcome="passed",
    )
    quality = harness.CaseRecord(
        name="quality",
        engine="langgraph",
        runner_mode="fake",
        fixture_head="a",
        outcome="quality_failed",
    )
    blocked = harness.CaseRecord(
        name="blocked",
        engine="langgraph",
        runner_mode="real",
        fixture_head="a",
        outcome="blocked",
    )
    unsafe = harness.CaseRecord(
        name="unsafe",
        engine="langgraph",
        runner_mode="real",
        fixture_head="a",
        outcome="safety_failed",
    )

    assert harness.decide_conclusion([passed]) == "pass"
    assert harness.decide_conclusion([passed, quality]) == "partial-pass"
    assert harness.decide_conclusion([passed, blocked]) == "blocked"
    assert (
        harness.decide_conclusion([passed, blocked, unsafe])
        == "fail"
    )


def test_finish_status_matches_each_engine_boundary() -> None:
    harness = _load_harness()

    assert harness._expected_finish_status("linear") == "ready_to_commit"
    assert (
        harness._expected_finish_status("langgraph")
        == "not_applicable_langgraph"
    )


def _runner_execution(
    harness,
    *,
    step: str,
    status: str,
    returncode: int | None,
    reason: str | None = None,
    termination_unconfirmed: bool = False,
):
    return harness.RunnerExecutionFact(
        path=f"iterations/01/executions/{step}/execution.json",
        step=step,
        status=status,
        returncode=returncode,
        reason=reason,
        termination_unconfirmed=termination_unconfirmed,
    )


def test_provider_failure_is_blocked_instead_of_treated_as_scope_escape() -> None:
    harness = _load_harness()
    record = harness.CaseRecord(
        name="provider-blocked",
        engine="linear",
        runner_mode="real",
        fixture_head="a",
        state_status="needs_human",
        current_step="worker_error",
        worker_status="failed",
        worker_start_count=1,
        worker_execution_count=1,
        reviewer_execution_count=1,
        runner_executions=[
            _runner_execution(
                harness,
                step="worker",
                status="failed",
                returncode=1,
                reason="provider model is not supported",
            ),
        ],
        runner_diagnostics=[
            "ERROR: provider model is not supported",
        ],
    )

    assert harness._safety_violations(record, require_hitl=False) == []
    assert harness._record_has_provider_failure(record) is True


def test_normal_provider_headers_and_needs_human_are_not_provider_failure() -> None:
    harness = _load_harness()
    record = harness.CaseRecord(
        name="review-needs-human",
        engine="langgraph",
        runner_mode="real",
        fixture_head="a",
        state_status="needs_human",
        current_step="review_run_failed",
        worker_status="success",
        reviewer_status="success",
        runner_executions=[
            _runner_execution(
                harness,
                step="worker",
                status="completed",
                returncode=0,
            ),
            _runner_execution(
                harness,
                step="reviewer",
                status="completed",
                returncode=0,
            ),
        ],
        runner_diagnostics=[
            "provider: openai",
            "model: sandbox-model",
        ],
    )

    assert harness._record_has_provider_failure(record) is False


def test_successful_transport_fallback_is_not_provider_failure() -> None:
    harness = _load_harness()
    record = harness.CaseRecord(
        name="successful-fallback",
        engine="linear",
        runner_mode="real",
        fixture_head="a",
        state_status="needs_human",
        current_step="done",
        worker_status="success",
        reviewer_status="success",
        runner_executions=[
            _runner_execution(
                harness,
                step="worker",
                status="completed",
                returncode=0,
            ),
            _runner_execution(
                harness,
                step="reviewer",
                status="completed",
                returncode=0,
            ),
        ],
        runner_diagnostics=[
            "warning: Falling back from WebSockets to HTTPS transport. request timed out",
        ],
    )

    assert harness._record_has_provider_failure(record) is False


def test_timed_out_execution_is_provider_failure() -> None:
    harness = _load_harness()
    record = harness.CaseRecord(
        name="runner-timeout",
        engine="linear",
        runner_mode="real",
        fixture_head="a",
        worker_status="timed_out",
        runner_executions=[
            _runner_execution(
                harness,
                step="worker",
                status="timed_out",
                returncode=1,
                reason="外部 runner 超时",
            ),
        ],
    )

    assert harness._record_has_provider_failure(record) is True


def test_termination_unknown_fails_closed_as_provider_failure() -> None:
    harness = _load_harness()
    record = harness.CaseRecord(
        name="termination-unknown",
        engine="linear",
        runner_mode="real",
        fixture_head="a",
        worker_status="failed",
        runner_executions=[
            _runner_execution(
                harness,
                step="worker",
                status="stop_requested",
                returncode=None,
                reason="owned process tree 终止未确认",
                termination_unconfirmed=True,
            ),
        ],
    )

    assert harness._record_has_provider_failure(record) is True


@pytest.mark.parametrize("reviewer_execution_count", [0, 2])
def test_reviewer_execution_count_must_be_exactly_one(
    reviewer_execution_count: int,
) -> None:
    harness = _load_harness()
    record = harness.CaseRecord(
        name="reviewer-count",
        engine="linear",
        runner_mode="real",
        fixture_head="a",
        worker_start_count=1,
        worker_execution_count=1,
        reviewer_execution_count=reviewer_execution_count,
    )

    assert harness._safety_violations(
        record,
        require_hitl=False,
    ) == [
        "reviewer execution artifact 数量不是 1："
        f"{reviewer_execution_count}"
    ]


def test_success_without_expected_fixture_change_remains_safety_failure() -> None:
    harness = _load_harness()
    record = harness.CaseRecord(
        name="invalid-success",
        engine="linear",
        runner_mode="fake",
        fixture_head="a",
        state_status="success",
        worker_start_count=1,
        worker_execution_count=1,
        reviewer_execution_count=1,
        artifact_integrity_valid=True,
        evidence_freshness_valid=True,
    )

    violations = harness._safety_violations(
        record,
        require_hitl=False,
    )
    assert "changed_files 越界：[]" in violations


def test_runner_diagnostics_excludes_prompt_body(tmp_path: Path) -> None:
    harness = _load_harness()
    run_dir = tmp_path / "run"
    output_path = (
        run_dir
        / "iterations"
        / "01"
        / "executions"
        / "worker"
        / "process-output.txt"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "\n".join(
            [
                "provider: sandboxproxy",
                "model: gpt-5.6",
                "普通 prompt 正文不应进入汇总",
                "warning: fallback metadata",
                "ERROR: model is not supported",
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    assert harness._read_runner_diagnostics(run_dir) == [
        "provider: sandboxproxy",
        "model: gpt-5.6",
        "warning: fallback metadata",
        "ERROR: model is not supported",
    ]
