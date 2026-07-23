from __future__ import annotations

from pathlib import Path

from ...models import ToolResult
from ...redaction import assert_not_sensitive_path, redact_value
from ...tools import file_tools, git_tools


class ToolBroker:
    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path

    def file_search(self, query: str) -> ToolResult:
        try:
            return _redact_result(ToolResult(
                tool="file.search",
                output={"query": query, "matches": file_tools.search(self.repo_path, query)},
            ))
        except Exception as exc:  # pragma: no cover - 防御性边界，避免工具异常打断主流程。
            return _redact_result(ToolResult(tool="file.search", status="error", error=str(exc)))

    def file_read(self, relative_path: str) -> ToolResult:
        try:
            assert_not_sensitive_path(relative_path)
            return _redact_result(
                ToolResult(tool="file.read", output=file_tools.read_file(self.repo_path, relative_path))
            )
        except Exception as exc:  # pragma: no cover - 防御性边界，避免工具异常打断主流程。
            return _redact_result(ToolResult(tool="file.read", status="error", error=str(exc)))

    def run_check(self, check_id: str) -> ToolResult:
        try:
            code, stdout, stderr = git_tools.run_git(self.repo_path, check_id)
            return _redact_result(ToolResult(
                tool="repo.run_check",
                output={"check_id": check_id, "exit_code": code, "stdout": stdout, "stderr": stderr},
            ))
        except Exception as exc:  # pragma: no cover - 防御性边界，避免工具异常打断主流程。
            return _redact_result(ToolResult(tool="repo.run_check", status="error", error=str(exc)))


def _redact_result(result: ToolResult) -> ToolResult:
    return ToolResult.model_validate(redact_value(result.model_dump(mode="json")))
