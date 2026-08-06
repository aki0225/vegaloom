from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(scope="module")
def materializer():
    """动态加载实验脚本，避免把 scripts 目录变成运行时 Python 包。"""
    script = Path(__file__).resolve().parents[1] / "scripts" / "rcb01_materializer.py"
    spec = importlib.util.spec_from_file_location("rcb01_materializer_under_test", script)
    if spec is None or spec.loader is None:
        raise AssertionError("无法加载 RCB-01 物化器")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str, allow: set[int] | None = None) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
        },
    )
    allowed = allow or {0}
    if result.returncode not in allowed:
        raise AssertionError(
            f"Git 命令失败：{args!r}\n"
            f"stdout={result.stdout.decode(errors='replace')}\n"
            f"stderr={result.stderr.decode(errors='replace')}"
        )
    return result.stdout


def _init_repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.name", "RCB-01 Test")
    _git(path, "config", "user.email", "rcb01@example.invalid")
    return path


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD").decode().strip()


def _write(repo: Path, relative: str, content: str | bytes) -> None:
    path = repo / Path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def _case_for_diff(materializer: Any, repo: Path, base: str, candidate: str) -> Any:
    diff = materializer._git_bytes(
        repo,
        "diff",
        "--no-ext-diff",
        "--no-renames",
        "--binary",
        "--full-index",
        base,
        candidate,
        "--",
    )
    changed = materializer._git_text(
        repo,
        "diff",
        "--name-status",
        "--no-renames",
        base,
        candidate,
        "--",
    ).splitlines()
    return materializer.CaseSpec(
        case_id="C1",
        base_revision=base,
        candidate_revision=candidate,
        expected_changed_file_count=len(changed),
        expected_diff_size=len(diff),
        expected_diff_sha256=hashlib.sha256(diff).hexdigest(),
        task="验证 RCB-01 物化器的确定性边界。",
        verification_commands=(
            "python -m compileall src",
            "python -m pytest -q",
            "ruff check src tests",
            "git diff --check",
        ),
    )


def _verification_payload(materializer: Any, case: Any) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "commands": [
            {
                "command": command,
                "duration_seconds": 0.1,
                "exit_code": 0,
                "status": "passed",
                "stderr_sha256": "0" * 64,
                "stdout_sha256": "0" * 64,
            }
            for command in case.verification_commands
        ],
        "schema_version": materializer.SCHEMA_VERSION,
        "source_revision": case.candidate_revision,
    }


def _fake_control(materializer: Any, case: Any, *, generator: str) -> Any:
    return materializer.ControlSpec(
        runtime_commit=generator,
        runtime_src_tree="b" * 40,
        budgets={
            "context_appendix_max_chars": 20_000,
            "core_pack_max_chars": 100_000,
            "reviewer_diff_max_chars": 50_000,
            "timeout_seconds": 900,
            "total_prompt_max_chars": 120_000,
        },
        cases={"C1": case},
        raw_bytes=b"{}",
    )


def _fake_artifacts(materializer: Any, verification: dict[str, Any]) -> tuple[dict[str, bytes], dict[str, Any]]:
    artifacts = {
        name: f"RCB-01 {name}\n".encode("utf-8")
        for name in (
            "arm-a-prompt.md",
            "arm-b-prompt.md",
            "changed-files.json",
            "context-appendix.md",
            "core-review-prompt.md",
            "diff-summary.md",
            "full-diff.patch",
            "impact-candidates.json",
            "project-context.md",
            "task.md",
            "test-summary.md",
        )
    }
    artifacts["verification-result.json"] = materializer._canonical_json_bytes(verification)
    metadata = {
        "candidate_tree": "c" * 40,
        "changed_files_sha256": "d" * 64,
        "context_appendix_chars": 12,
        "core_pack_chars": 10,
        "impact_candidate_count": 0,
        "total_prompt_chars": 23,
    }
    return artifacts, metadata


def _fake_manifest(
    materializer: Any,
    case: Any,
    control: Any,
    generator: str,
    artifacts: dict[str, bytes],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "artifacts": {
            name: {
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
            for name, content in sorted(artifacts.items())
        },
        "base_revision": case.base_revision,
        "budgets": dict(sorted(control.budgets.items())),
        "candidate_revision": case.candidate_revision,
        "candidate_tree": metadata["candidate_tree"],
        "case_id": case.case_id,
        "changed_files_sha256": metadata["changed_files_sha256"],
        "context_appendix_chars": metadata["context_appendix_chars"],
        "core_pack_chars": metadata["core_pack_chars"],
        "diff_sha256": case.expected_diff_sha256,
        "diff_size": case.expected_diff_size,
        "experiment_id": materializer.EXPERIMENT_ID,
        "generator_revision": generator,
        "impact_candidate_count": metadata["impact_candidate_count"],
        "runtime_commit": control.runtime_commit,
        "runtime_src_tree": control.runtime_src_tree,
        "schema_version": materializer.SCHEMA_VERSION,
        "total_prompt_chars": metadata["total_prompt_chars"],
    }


def _write_fake_materialization(
    materializer: Any,
    repo: Path,
    case: Any,
    *,
    generator: str = "a" * 40,
) -> tuple[Path, Any, dict[str, bytes], dict[str, Any]]:
    control = _fake_control(materializer, case, generator=generator)
    verification = _verification_payload(materializer, case)
    artifacts, metadata = _fake_artifacts(materializer, verification)
    output = repo / ".local-validation" / "rcb-01" / "C1"
    output.mkdir(parents=True)
    for name, content in artifacts.items():
        (output / name).write_bytes(content)
    (output / "materialization.json").write_bytes(
        materializer._canonical_json_bytes(
            _fake_manifest(materializer, case, control, generator, artifacts, metadata)
        )
    )
    return output, control, artifacts, metadata


def test_control_manifest_binds_all_five_real_diffs(materializer: Any) -> None:
    """五个预注册案例必须与真实历史 Diff 的字节数、哈希和文件数一致。"""
    repo = Path(__file__).resolve().parents[1]
    control = materializer.load_control_spec(repo)
    assert list(control.cases) == ["C1", "C2", "C3", "C4", "C5"]
    for case in control.cases.values():
        diff = materializer._case_diff(repo, case)
        changed = materializer._changed_status(repo, case)
        assert len(diff) == case.expected_diff_size
        assert hashlib.sha256(diff).hexdigest() == case.expected_diff_sha256
        assert len(changed) == case.expected_changed_file_count
        assert case.base_revision != case.candidate_revision


def test_impact_candidates_are_ranked_bounded_and_deterministic(
    materializer: Any,
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path / "candidate-repo")
    _write(repo, "src/vega/changed.py", "from vega.dep import helper\n\ndef changed_symbol():\n    return helper()\n")
    _write(repo, "src/vega/dep.py", "def helper():\n    return 1\n")
    _write(repo, "tests/test_changed.py", "from vega.changed import changed_symbol\n")
    for index in range(5):
        _write(
            repo,
            f"src/vega/caller_{index:02d}.py",
            "from vega.changed import changed_symbol\n",
        )
    for index in range(15):
        _write(
            repo,
            f"docs/reference_{index:02d}.md",
            "changed_symbol is referenced here.\n",
        )
    base = _commit(repo, "建立候选仓库")
    _write(
        repo,
        "src/vega/changed.py",
        "from vega.dep import helper\n\ndef changed_symbol():\n    return helper() + 1\n",
    )
    candidate = _commit(repo, "修改变更模块")
    case = _case_for_diff(materializer, repo, base, candidate)
    first = materializer.generate_impact_candidates(repo, case)
    second = materializer.generate_impact_candidates(repo, case)
    assert first == second
    assert len(first) <= materializer.MAX_CANDIDATES
    assert first == sorted(first, key=lambda item: (item.rank, item.role, item.path))
    assert first[0].path == "tests/test_changed.py"
    assert first[0].role == "test"
    assert any(item.path == "src/vega/dep.py" for item in first)


def test_eligible_texts_exclude_unsafe_tree_entries(
    materializer: Any,
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path / "tree-repo")
    _write(repo, "src/vega/normal.py", "def normal_function():\n    return 1\n")
    _write(repo, "binary.dat", b"text\x00binary")
    _write(repo, "too-large.py", b"x" * (materializer.MAX_CANDIDATE_BYTES + 1))
    _write(repo, "generated/auto.py", "def generated_function():\n    return 1\n")
    _write(repo, "vendor/third_party.py", "def vendor_function():\n    return 1\n")
    _write(repo, "generated_auto.py", "def generated_function():\n    return 1\n")
    _write(repo, "credentials.json", '{"password": "not-for-review"}\n')
    _write(repo, "secrets.yml", "name: fixture-only\n")
    _write(repo, "credentials.yml", "name: fixture-only\n")
    _write(repo, "credentials.yaml", "name: fixture-only\n")
    _write(repo, ".secrets/runtime.txt", "name: fixture-only\n")
    symlink = repo / "linked.py"
    try:
        symlink.symlink_to("src/vega/normal.py")
    except OSError:
        pytest.skip("当前环境不允许创建 symlink")
    submodule = _init_repo(tmp_path / "submodule")
    _write(submodule, "module.txt", "submodule\n")
    submodule_commit = _commit(submodule, "子模块内容")
    base = _commit(repo, "建立不安全候选项")
    _git(
        repo,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{submodule_commit},submodule/component",
    )
    candidate = _git(repo, "commit", "-qm", "登记子模块")
    candidate = _git(repo, "rev-parse", "HEAD").decode().strip()
    entries = materializer._tree_entries(repo, candidate)
    texts = materializer._eligible_texts(repo, entries)
    assert texts["src/vega/normal.py"].startswith("def normal_function")
    excluded = {
        "binary.dat",
        "too-large.py",
        "generated/auto.py",
        "vendor/third_party.py",
        "generated_auto.py",
        "credentials.json",
        "secrets.yml",
        "credentials.yml",
        "credentials.yaml",
        ".secrets/runtime.txt",
        "linked.py",
        "submodule/component",
    }
    assert excluded.isdisjoint(texts)
    assert base != candidate


@pytest.mark.parametrize("payload", ["null", "1", "[]"])
def test_control_manifest_non_object_fails_closed(
    materializer: Any,
    tmp_path: Path,
    payload: str,
) -> None:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "rcb01_cases.json").write_text(payload, encoding="utf-8")
    with pytest.raises(materializer.MaterializationError):
        materializer.load_control_spec(repo)


def test_verification_commands_are_exactly_bound(materializer: Any) -> None:
    repo = Path(__file__).resolve().parents[1]
    case = materializer.load_control_spec(repo).cases["C1"]
    payload = _verification_payload(materializer, case)
    assert materializer._validate_verification_payload(case, payload) == payload
    changed = json.loads(json.dumps(payload))
    changed["commands"][0]["command"] = "python -m pytest"
    with pytest.raises(materializer.MaterializationError):
        materializer._validate_verification_payload(case, changed)
    slow = json.loads(json.dumps(payload))
    slow["commands"][0]["duration_seconds"] = 61
    with pytest.raises(materializer.MaterializationError):
        materializer._validate_verification_payload(case, slow)


def test_repeated_artifact_build_is_byte_identical(
    materializer: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path / "repeat-repo")
    _write(repo, "src/vega/changed.py", "def changed_symbol():\n    return 1\n")
    base = _commit(repo, "建立重复物化基线")
    _write(repo, "src/vega/changed.py", "def changed_symbol():\n    return 2\n")
    candidate = _commit(repo, "建立重复物化候选")
    case = _case_for_diff(materializer, repo, base, candidate)
    control = materializer.ControlSpec(
        runtime_commit="a" * 40,
        runtime_src_tree="b" * 40,
        budgets={
            "context_appendix_max_chars": 20_000,
            "core_pack_max_chars": 100_000,
            "reviewer_diff_max_chars": 50_000,
            "timeout_seconds": 900,
            "total_prompt_max_chars": 120_000,
        },
        cases={"C1": case},
        raw_bytes=b"{}",
    )
    monkeypatch.setattr(
        materializer,
        "_build_project_context",
        lambda *_args: "固定项目上下文\n",
    )
    monkeypatch.setattr(
        materializer,
        "render_review_prompt",
        lambda inputs: json.dumps(
            inputs,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    verification = _verification_payload(materializer, case)
    first, first_metadata = materializer._build_case_artifacts(
        repo,
        control,
        case,
        verification,
        "a" * 40,
    )
    second, second_metadata = materializer._build_case_artifacts(
        repo,
        control,
        case,
        verification,
        "a" * 40,
    )
    assert first == second
    assert first_metadata == second_metadata


def test_prompt_arms_and_project_context_are_separated_without_local_paths(
    materializer: Any,
) -> None:
    """A 组仅使用 Core Pack，B 组只能追加 Appendix，项目上下文不带本机路径。"""
    repo = Path(__file__).resolve().parents[1]
    control = materializer.load_control_spec(repo)
    case = control.cases["C1"]
    verification = _verification_payload(materializer, case)
    artifacts, _ = materializer._build_case_artifacts(
        repo,
        control,
        case,
        verification,
        materializer._resolve_commit(repo, "HEAD"),
    )
    core = artifacts["core-review-prompt.md"]
    appendix = artifacts["context-appendix.md"]
    assert artifacts["arm-a-prompt.md"] == core
    assert artifacts["arm-b-prompt.md"] == core + b"\n" + appendix
    project_context = artifacts["project-context.md"].decode("utf-8")
    assert str(repo).replace("\\", "/") not in project_context.replace("\\", "/")
    assert "F:\\" not in project_context  # repo-path-policy: allow-test-fixture
    assert "C:\\" not in project_context  # repo-path-policy: allow-test-fixture
    all_text = b"\n".join(artifacts.values()).lower()
    assert b"golden" not in all_text
    assert b"oracle_sha" not in all_text
    assert b"worker_transcript" not in all_text
    assert b"conversation_history" not in all_text


def test_validator_fails_closed_when_artifact_is_tampered(
    materializer: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    case = materializer.CaseSpec(
        case_id="C1",
        base_revision="c" * 40,
        candidate_revision="d" * 40,
        expected_changed_file_count=1,
        expected_diff_size=1,
        expected_diff_sha256="e" * 64,
        task="测试",
        verification_commands=("one", "two", "three", "four"),
    )
    output, control, artifacts, metadata = _write_fake_materialization(
        materializer,
        repo,
        case,
    )
    monkeypatch.setattr(
        materializer,
        "load_control_spec",
        lambda _repo, revision=None: control,
    )
    monkeypatch.setattr(materializer, "_assert_runtime_binding", lambda *_args: None)
    monkeypatch.setattr(
        materializer,
        "_build_case_artifacts",
        lambda *_args: (artifacts, metadata),
    )
    assert materializer.validate_materialization(
        repo,
        output,
        require_formal_state=False,
    )["case_id"] == "C1"
    (output / "core-review-prompt.md").write_text("被篡改\n", encoding="utf-8")
    with pytest.raises(materializer.MaterializationError):
        materializer.validate_materialization(
            repo,
            output,
            require_formal_state=False,
        )


def test_validator_rejects_extra_manifest_fields_and_extra_directories(
    materializer: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    case = materializer.CaseSpec(
        case_id="C1",
        base_revision="c" * 40,
        candidate_revision="d" * 40,
        expected_changed_file_count=1,
        expected_diff_size=1,
        expected_diff_sha256="e" * 64,
        task="测试",
        verification_commands=("one", "two", "three", "four"),
    )
    output, control, artifacts, metadata = _write_fake_materialization(
        materializer,
        repo,
        case,
    )
    monkeypatch.setattr(
        materializer,
        "load_control_spec",
        lambda _repo, revision=None: control,
    )
    monkeypatch.setattr(materializer, "_assert_runtime_binding", lambda *_args: None)
    monkeypatch.setattr(
        materializer,
        "_build_case_artifacts",
        lambda *_args: (artifacts, metadata),
    )
    manifest = json.loads((output / "materialization.json").read_text(encoding="utf-8"))
    manifest["unexpected"] = "must fail closed"
    (output / "materialization.json").write_bytes(
        materializer._canonical_json_bytes(manifest)
    )
    with pytest.raises(materializer.MaterializationError):
        materializer.validate_materialization(
            repo,
            output,
            require_formal_state=False,
        )

    # 恢复合法 manifest 后，额外目录也不能静默忽略。
    (output / "materialization.json").write_bytes(
        materializer._canonical_json_bytes(
            _fake_manifest(materializer, case, control, "a" * 40, artifacts, metadata)
        )
    )
    (output / "unexpected-directory").mkdir()
    with pytest.raises(materializer.MaterializationError):
        materializer.validate_materialization(
            repo,
            output,
            require_formal_state=False,
        )


def test_validator_rejects_artifact_symlink(
    materializer: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    case = materializer.CaseSpec(
        case_id="C1",
        base_revision="c" * 40,
        candidate_revision="d" * 40,
        expected_changed_file_count=1,
        expected_diff_size=1,
        expected_diff_sha256="e" * 64,
        task="测试",
        verification_commands=("one", "two", "three", "four"),
    )
    output, control, artifacts, metadata = _write_fake_materialization(
        materializer,
        repo,
        case,
    )
    monkeypatch.setattr(
        materializer,
        "load_control_spec",
        lambda _repo, revision=None: control,
    )
    monkeypatch.setattr(materializer, "_assert_runtime_binding", lambda *_args: None)
    monkeypatch.setattr(
        materializer,
        "_build_case_artifacts",
        lambda *_args: (artifacts, metadata),
    )
    target = output / "core-review-prompt.md"
    target.unlink()
    try:
        target.symlink_to(output / "arm-a-prompt.md")
    except OSError:
        pytest.skip("当前环境不允许创建 symlink")
    with pytest.raises(materializer.MaterializationError):
        materializer.validate_materialization(
            repo,
            output,
            require_formal_state=False,
        )


def test_output_path_must_stay_inside_non_linked_local_root(
    materializer: Any,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    inside = repo / ".local-validation" / "rcb-01" / "C1"
    assert materializer._validate_output_dir(repo, inside) == inside.absolute()
    with pytest.raises(materializer.MaterializationError):
        materializer._validate_output_dir(repo, tmp_path / "outside")
    linked = repo / ".local-validation" / "rcb-01" / "linked"
    linked.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前环境不允许创建 symlink")
    with pytest.raises(materializer.MaterializationError):
        materializer._validate_output_dir(repo, linked / "C1")


def test_runtime_binding_rejects_source_tree_drift_and_unknown_commit(
    materializer: Any,
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path / "runtime-repo")
    _write(repo, "src/vega/runtime.py", "RUNTIME = 1\n")
    runtime_commit = _commit(repo, "冻结 Runtime")
    runtime_tree = _git(repo, "rev-parse", f"{runtime_commit}:src").decode().strip()
    _write(repo, "src/vega/runtime.py", "RUNTIME = 2\n")
    generator_revision = _commit(repo, "漂移 Runtime")
    control = materializer.ControlSpec(
        runtime_commit=runtime_commit,
        runtime_src_tree=runtime_tree,
        budgets={},
        cases={},
        raw_bytes=b"{}",
    )
    with pytest.raises(materializer.MaterializationError):
        materializer._assert_runtime_binding(repo, control, generator_revision)

    control_with_unknown_runtime = materializer.ControlSpec(
        runtime_commit="f" * 40,
        runtime_src_tree=runtime_tree,
        budgets={},
        cases={},
        raw_bytes=b"{}",
    )
    with pytest.raises(materializer.MaterializationError):
        materializer._assert_runtime_binding(repo, control_with_unknown_runtime, generator_revision)


def test_loaded_runtime_sources_must_resolve_inside_current_repo(
    materializer: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[1]
    external = tmp_path / "external" / "review_runtime.py"
    external.parent.mkdir(parents=True)
    external.write_text("# 外部伪造 Runtime\n", encoding="utf-8")
    runtime_module = sys.modules["vega.review_runtime"]
    monkeypatch.setattr(runtime_module, "__file__", str(external))
    with pytest.raises(materializer.MaterializationError):
        materializer._assert_loaded_runtime_sources(
            repo,
            materializer._resolve_commit(repo, "HEAD"),
        )


def test_validator_rejects_stale_generator_revision(
    materializer: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "scripts/rcb01_cases.json", "{}\n")
    _write(repo, "scripts/rcb01_materializer.py", "# fixture generator\n")
    _write(repo, "marker.txt", "旧版本\n")
    old_revision = _commit(repo, "旧物化器状态")
    _write(repo, "marker.txt", "当前版本\n")
    _commit(repo, "当前 checkout")
    case = materializer.CaseSpec(
        case_id="C1",
        base_revision="c" * 40,
        candidate_revision="d" * 40,
        expected_changed_file_count=1,
        expected_diff_size=1,
        expected_diff_sha256="e" * 64,
        task="测试",
        verification_commands=("one", "two", "three", "four"),
    )
    output, control, artifacts, metadata = _write_fake_materialization(
        materializer,
        repo,
        case,
        generator=old_revision,
    )
    monkeypatch.setattr(
        materializer,
        "load_control_spec",
        lambda _repo, revision=None: control,
    )
    monkeypatch.setattr(materializer, "_assert_runtime_binding", lambda *_args: None)
    monkeypatch.setattr(
        materializer,
        "_build_case_artifacts",
        lambda *_args: (artifacts, metadata),
    )
    with pytest.raises(materializer.MaterializationError):
        materializer.validate_materialization(repo, output)


def test_repeated_canonical_json_is_byte_identical(materializer: Any) -> None:
    payload = {"z": 1, "a": ["中文", 2], "nested": {"b": True, "a": None}}
    assert materializer._canonical_json_bytes(payload) == materializer._canonical_json_bytes(
        payload
    )
