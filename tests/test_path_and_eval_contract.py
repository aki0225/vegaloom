from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from vega import git_read as git_read_module
from vega.experimental.inspection.context_loader import load_target_context, parse_target_files
from vega.run_status import latest_run_dir
from vega.run_utils import create_run_dir, resolve_run_dir
from vega.experimental.inspection.tool_broker import ToolBroker
from vega.tools import file_tools
from vega.tools import git_tools


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_resolve_run_dir_accepts_existing_run_under_workspace_runs(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "safe-run"
    run_dir.mkdir(parents=True)

    assert resolve_run_dir(tmp_path, "safe-run") == run_dir.resolve()
    assert resolve_run_dir(tmp_path, str(Path("runs") / "safe-run")) == run_dir.resolve()


def test_create_run_dir_creates_missing_workspace_and_runs_root(tmp_path: Path) -> None:
    workspace = tmp_path / "new-workspace"

    run_id, run_dir = create_run_dir(workspace, "new-run")

    assert run_id == "new-run"
    assert run_dir == (workspace / "runs" / run_id).resolve()
    assert run_dir.is_dir()


@pytest.mark.parametrize(
    "run",
    [
        "../outside-run",
        str(Path("runs") / ".." / "outside-run"),
        str(Path("runs") / "safe-run" / "nested"),
    ],
)
def test_resolve_run_dir_rejects_relative_path_escape_or_nested_path(
    tmp_path: Path,
    run: str,
) -> None:
    (tmp_path / "runs" / "safe-run").mkdir(parents=True)
    (tmp_path / "outside-run").mkdir()

    with pytest.raises(FileNotFoundError):
        resolve_run_dir(tmp_path, run)


def test_resolve_run_dir_rejects_absolute_path_even_when_directory_exists(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "safe-run"
    run_dir.mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="绝对路径"):
        resolve_run_dir(tmp_path, str(run_dir))


def test_resolve_run_dir_rejects_symlink_escape(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    outside = tmp_path / "outside-run"
    outside.mkdir()
    link = runs_dir / "escaped-run"
    _create_directory_link_or_skip(outside, link)

    with pytest.raises(FileNotFoundError, match="边界"):
        resolve_run_dir(tmp_path, "escaped-run")


def test_runs_root_link_escape_is_rejected_by_create_resolve_and_latest(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside-runs"
    outside.mkdir()
    _create_directory_link_or_skip(outside, tmp_path / "runs")

    with pytest.raises(ValueError, match="runs 根目录不能是"):
        create_run_dir(tmp_path, "new-run")
    with pytest.raises(ValueError, match="runs 根目录不能是"):
        resolve_run_dir(tmp_path, "existing-run")
    with pytest.raises(ValueError, match="runs 根目录不能是"):
        latest_run_dir(tmp_path)


def _create_directory_link_or_skip(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        if sys.platform != "win32":
            pytest.skip(f"当前平台不能创建目录 symlink：{exc}")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            text=True,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip(f"当前平台不能创建目录 symlink 或 junction：{exc}; {result.stderr}")


def test_file_search_places_double_dash_before_literal_query(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="README.md:1:--files\n", stderr="")

    monkeypatch.setattr(file_tools.subprocess, "run", fake_run)

    assert file_tools.search(tmp_path, "--files") == ["README.md:1:--files"]
    args = captured["args"]
    assert isinstance(args, list)
    separator = args.index("--")
    assert args[:3] == ["rg", "-n", "--fixed-strings"]
    assert args[separator + 1 :] == ["--files", str(tmp_path.resolve())]
    assert "!**/.env" in args
    assert "!**/*.pem" in args
    assert captured["kwargs"] == {
        "capture_output": True,
        "encoding": "utf-8",
        "errors": "replace",
        "text": True,
        "timeout": 10,
        "check": False,
    }


@pytest.mark.parametrize("query", ["", "   ", "line\nbreak", "nul\x00byte"])
def test_file_search_rejects_invalid_queries(query: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        file_tools.search(tmp_path, query)


def test_file_search_distinguishes_no_matches_from_execution_errors(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        file_tools.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    assert file_tools.search(tmp_path, "missing") == []

    monkeypatch.setattr(
        file_tools.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=2,
            stdout="",
            stderr="rg failed",
        ),
    )
    result = ToolBroker(tmp_path).file_search("broken")

    assert result.status == "error"
    assert "rg failed" in result.error


def test_file_search_timeout_error_does_not_leak_query_or_command(
    monkeypatch,
    tmp_path: Path,
) -> None:
    secret_query = "api_key=sk-timeout-secret-123456"

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        raise subprocess.TimeoutExpired(cmd=args, timeout=10)

    monkeypatch.setattr(file_tools.subprocess, "run", fake_run)

    result = ToolBroker(tmp_path).file_search(secret_query)
    payload = result.model_dump(mode="json")

    assert result.status == "error"
    assert "超时" in (result.error or "")
    assert secret_query not in str(payload)
    assert "rg" not in (result.error or "")
    assert "--fixed-strings" not in (result.error or "")


def test_file_search_filters_sensitive_path_matches(monkeypatch, tmp_path: Path) -> None:
    sensitive = tmp_path / ".env"
    safe = tmp_path / "src" / "app.py"
    stdout = "\n".join(
        [
            f"{sensitive}:1:API_KEY=fake-secret",
            f"{safe}:2:needle",
        ]
    )
    monkeypatch.setattr(
        file_tools.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=stdout, stderr=""),
    )

    assert file_tools.search(tmp_path, "needle") == [f"{safe}:2:needle"]


def test_file_search_parses_windows_absolute_paths_without_losing_drive(
    monkeypatch,
    tmp_path: Path,
) -> None:
    safe = r"C:\repo\src\app.py"  # repo-path-policy: allow-test-fixture
    sensitive = r"C:\repo\.env"  # repo-path-policy: allow-test-fixture
    monkeypatch.setattr(
        file_tools.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=f"{sensitive}:1:needle\n{safe}:2:needle\n",
            stderr="",
        ),
    )

    assert file_tools.search(tmp_path, "needle") == [f"{safe}:2:needle"]


def test_parse_target_files_requires_exact_section_title() -> None:
    task_text = """
# Task

Non-target files:
- `ignored.py`

Target files:
- `src/app.py`
"""

    assert parse_target_files(task_text) == ["src/app.py"]


def test_directory_context_includes_source_files_and_skips_linked_tree(tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    source_dir.joinpath("app.py").write_text("print('ok')\n", encoding="utf-8")
    source_dir.joinpath("view.tsx").write_text("export const View = () => null;\n", encoding="utf-8")
    source_dir.joinpath("asset.bin").write_bytes(b"\x00\x01")

    outside = tmp_path / "outside"
    outside.mkdir()
    outside.joinpath("escaped.rs").write_text("fn main() {}\n", encoding="utf-8")
    _create_directory_link_or_skip(outside, source_dir / "linked")

    result = load_target_context(tmp_path, ["src"])[0]
    files = set(result.output["files"])

    assert result.status == "ok"
    assert files == {"src/app.py", "src/view.tsx"}
    assert all("escaped.rs" not in summary["path"] for summary in result.output["summaries"])


def test_directory_context_orders_selected_files_deterministically(tmp_path: Path) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    for name in ["z.py", "A.py", "m.py"]:
        source_dir.joinpath(name).write_text(f"# {name}\n", encoding="utf-8")

    result = load_target_context(tmp_path, ["src"])[0]

    assert result.output["files"] == ["src/A.py", "src/m.py", "src/z.py"]


def test_repo_run_check_allowlist_matches_public_contract(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(git_read_module.subprocess, "run", fake_run)
    broker = ToolBroker(tmp_path)

    for check_id in ["git.status", "git.diff", "git.diff_check"]:
        assert broker.run_check(check_id).status == "ok"

    rejected = broker.run_check("git.diff_name_only")

    assert rejected.status == "error"
    assert calls == [
        git_read_module.harden_git_read_command(
            git_tools.ALLOWED_CHECKS[check_id]
        )
        for check_id in ["git.status", "git.diff", "git.diff_check"]
    ]


def test_eval_cases_only_require_memory_proposals_for_explicit_contracts() -> None:
    cases = [
        json.loads(line)
        for line in (PROJECT_ROOT / "eval" / "cases.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    default_no_memory_cases = {
        "engineering_change_atg_docs",
        "engineering_change_vega_docs",
        "brief_bug_export_button",
        "brief_feature_bulk_import_users",
        "reflect_vega_diff",
    }

    for case in cases:
        if case["case_id"] in default_no_memory_cases:
            assert "memory-proposals.jsonl" not in case["must_create"]
