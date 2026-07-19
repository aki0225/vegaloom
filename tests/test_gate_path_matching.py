from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vega.gate_runtime import (
    HIGH_RISK_PATH_KEYWORDS,
    MEDIUM_RISK_PATH_KEYWORDS,
    GateRuntime,
    _matched_paths,
)
from vega.reflect_runtime import ReflectRuntime


@pytest.mark.parametrize(
    ("path", "keywords"),
    [
        ("pricing.py", MEDIUM_RISK_PATH_KEYWORDS),
        ("src/cipher.py", MEDIUM_RISK_PATH_KEYWORDS),
        ("src/incident.py", MEDIUM_RISK_PATH_KEYWORDS),
        ("src/author.py", HIGH_RISK_PATH_KEYWORDS),
        ("src/tokenizer.py", HIGH_RISK_PATH_KEYWORDS),
        ("src/roleplay.py", HIGH_RISK_PATH_KEYWORDS),
        ("src/authorship.py", HIGH_RISK_PATH_KEYWORDS),
        ("src/authserviceable.py", HIGH_RISK_PATH_KEYWORDS),
        ("src/authserviceable/handler.py", HIGH_RISK_PATH_KEYWORDS),
        ("src/authserverless.ts", HIGH_RISK_PATH_KEYWORDS),
        ("src/authapical.ts", HIGH_RISK_PATH_KEYWORDS),
        ("db/databasebackupable.sql", HIGH_RISK_PATH_KEYWORDS),
        ("src/tokenization.py", HIGH_RISK_PATH_KEYWORDS),
        ("src/cicada.py", MEDIUM_RISK_PATH_KEYWORDS),
        (r"src\AuthorService.py", HIGH_RISK_PATH_KEYWORDS),
        (r"src\TokenizerService.py", HIGH_RISK_PATH_KEYWORDS),
        (r"src\RoleplayEngine.py", HIGH_RISK_PATH_KEYWORDS),
    ],
)
def test_builtin_risk_paths_ignore_plain_substrings(
    path: str,
    keywords: list[str],
) -> None:
    assert _matched_paths([path], keywords) == []


@pytest.mark.parametrize(
    ("path", "keywords"),
    [
        ("ci/check.ps1", MEDIUM_RISK_PATH_KEYWORDS),
        (".github/workflows/build.yml", MEDIUM_RISK_PATH_KEYWORDS),
        ("config/app.yaml", MEDIUM_RISK_PATH_KEYWORDS),
        ("docker-compose.yml", MEDIUM_RISK_PATH_KEYWORDS),
        ("src/auth/service.py", HIGH_RISK_PATH_KEYWORDS),
        ("src/auth_service.py", HIGH_RISK_PATH_KEYWORDS),
        ("src/authorization/policy.py", HIGH_RISK_PATH_KEYWORDS),
        ("src/token_store.py", HIGH_RISK_PATH_KEYWORDS),
        ("src/roles/model.py", HIGH_RISK_PATH_KEYWORDS),
        ("src/payment/charge.py", HIGH_RISK_PATH_KEYWORDS),
        (r"src\AuthService.java", HIGH_RISK_PATH_KEYWORDS),
        (r"src\TokenManager.cs", HIGH_RISK_PATH_KEYWORDS),
        (r"src\PaymentController.java", HIGH_RISK_PATH_KEYWORDS),
        (r".github\workflows\build.yml", MEDIUM_RISK_PATH_KEYWORDS),
        ("CIConfig.yml", MEDIUM_RISK_PATH_KEYWORDS),
        ("src/oauth2_client.py", HIGH_RISK_PATH_KEYWORDS),
        (r"src\OAuth2Client.java", HIGH_RISK_PATH_KEYWORDS),
        ("src/authservice.py", HIGH_RISK_PATH_KEYWORDS),
        ("db/databasebackup.sql", HIGH_RISK_PATH_KEYWORDS),
        ("src/auth2.py", HIGH_RISK_PATH_KEYWORDS),
        ("src/auth123service.py", HIGH_RISK_PATH_KEYWORDS),
        ("src/authservice2.py", HIGH_RISK_PATH_KEYWORDS),
        ("src/tokenstore2.py", HIGH_RISK_PATH_KEYWORDS),
    ],
)
def test_builtin_risk_paths_match_semantic_tokens(
    path: str,
    keywords: list[str],
) -> None:
    assert _matched_paths([path], keywords) == [path]


@pytest.mark.parametrize(
    "relative_path",
    [
        "pricing.py",
        "src/author.py",
        "src/authserviceable.py",
        "db/databasebackupable.sql",
    ],
)
def test_gate_keeps_plain_substring_paths_low_risk(
    tmp_path: Path,
    relative_path: str,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    changed_path = _init_repo_with_tracked_file(repo, relative_path)
    changed_path.write_text("VALUE = 2\n", encoding="utf-8", newline="\n")
    workspace.mkdir()
    test_log = workspace / "tests.log"
    test_log.write_text("1 passed\n", encoding="utf-8", newline="\n")

    reflect_run = ReflectRuntime(workspace).run(repo, test_log=test_log)
    gate_run = GateRuntime(workspace).run(repo, reflect_run.name)
    result = json.loads(gate_run.joinpath("gate-result.json").read_text(encoding="utf-8"))

    assert result["risk"] == "low"
    assert result["recommendation"] == "self-check"
    assert {reason["code"] for reason in result["reasons"]} == {"no_risk_signal"}


@pytest.mark.parametrize(
    "relative_path",
    [
        "src/oauth2_client.py",
        "src/OAuth2Client.java",
        "src/authservice.py",
        "db/databasebackup.sql",
    ],
)
def test_gate_flags_compound_risk_paths_as_high_risk(
    tmp_path: Path,
    relative_path: str,
) -> None:
    workspace = tmp_path / "workspace"
    repo = tmp_path / "repo"
    changed_path = _init_repo_with_tracked_file(repo, relative_path)
    changed_path.write_text("VALUE = 2\n", encoding="utf-8", newline="\n")
    workspace.mkdir()
    test_log = workspace / "tests.log"
    test_log.write_text("1 passed\n", encoding="utf-8", newline="\n")

    reflect_run = ReflectRuntime(workspace).run(repo, test_log=test_log)
    gate_run = GateRuntime(workspace).run(repo, reflect_run.name)
    result = json.loads(gate_run.joinpath("gate-result.json").read_text(encoding="utf-8"))

    high_risk_reason = next(
        reason for reason in result["reasons"] if reason["code"] == "high_risk_paths"
    )
    assert result["risk"] == "high"
    assert result["recommendation"] == "human-review"
    assert relative_path in high_risk_reason["evidence"]


def _init_repo_with_tracked_file(repo: Path, relative_path: str) -> Path:
    repo.mkdir(parents=True)
    subprocess.run(
        ["git", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    repo.joinpath("AGENTS.md").write_text(
        "# Rules\n\n- Run tests.\n",
        encoding="utf-8",
        newline="\n",
    )
    changed_path = repo / relative_path
    changed_path.parent.mkdir(parents=True, exist_ok=True)
    changed_path.write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    subprocess.run(
        ["git", "add", "--", "AGENTS.md", relative_path],
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
    return changed_path
