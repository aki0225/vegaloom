from __future__ import annotations

from dataclasses import dataclass

from .workspace_inventory import ignored_coverage_level


@dataclass(frozen=True)
class ReviewWorkspaceSnapshot:
    fingerprint: str
    head_sha: str
    status_sha256: str
    staged_diff_sha256: str
    unstaged_diff_sha256: str
    untracked_manifest_sha256: str
    ignored_manifest_sha256: str
    index_flags_sha256: str
    full_diff: str
    staged_diff: str
    unstaged_diff: str
    changed_files: tuple[str, ...]
    untracked_files: tuple[str, ...]
    unsafe_index_paths: tuple[str, ...] = ()
    untracked_content_complete: bool = False
    ignored_manifest_complete: bool = False
    ignored_content_complete: bool = False
    git_control_sha256: str = ""
    git_control_complete: bool = False
    comparison_base_sha: str | None = None
    comparison_paths: tuple[str, ...] = ()
    committed_diff_sha256: str = ""
    committed_diff: str = ""
    committed_files: tuple[str, ...] = ()

    @property
    def ignored_coverage_level(self) -> str:
        return ignored_coverage_level(
            self.ignored_manifest_complete,
            self.ignored_content_complete,
        )
