from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .agent_contract import canonical_digest


def retry_source_finish_ref(operation_id: str) -> str:
    digest = canonical_digest({"operation_id": operation_id})
    return f"verification-retries/{digest}/source-finish-summary.json"


def archive_retry_source_finish(
    run_dir: Path,
    child_dir: Path,
    operation_id: str,
    expected_sha256: str,
) -> str:
    """在 Core 覆盖 Finish 前，原样保存已脱敏的失败摘要。"""

    source = child_dir / "finish-summary.json"
    content = source.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError("原始 child 的 Finish 摘要在绑定前发生变化")
    relative = retry_source_finish_ref(operation_id)
    target = run_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != content:
            raise ValueError("验证恢复的原始 Finish 归档发生身份冲突")
        return relative
    with target.open("xb") as stream:
        stream.write(content)
    return relative


def retry_source_finish_archive_issue(
    run_dir: Path,
    child_payload: dict[str, object],
    worker: dict[str, object],
) -> str | None:
    operation_id = child_payload.get("operation_id")
    relative = worker.get("source_finish_ref")
    expected_sha256 = worker.get("source_finish_sha256")
    if (
        not isinstance(operation_id, str)
        or relative != retry_source_finish_ref(operation_id)
        or not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        return "验证恢复缺少原始失败 Finish 归档"
    try:
        path = (run_dir / relative).resolve(strict=True)
        if not path.is_relative_to(run_dir.resolve(strict=True)):
            return "原始失败 Finish 归档越过 Agent run 边界"
        content = path.read_bytes()
        finish = json.loads(content.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "原始失败 Finish 归档缺失或无法解析"
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        return "原始失败 Finish 归档哈希不匹配"
    if (
        not isinstance(finish, dict)
        or finish.get("run_id") != child_payload.get("child_run")
        or finish.get("finish_status") != "needs_fix"
        or finish.get("latest_verification_failed") is not True
    ):
        return "原始失败 Finish 归档身份或状态不一致"
    return None
