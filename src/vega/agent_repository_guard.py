from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .agent_contract import AgentObservation, AgentState, ObservationAuthority
from .agent_persistence import AgentArtifactError, load_agent_state
from .agent_repository_change_lock import (
    AgentRepositoryGuardError,
    _is_link_or_reparse,
    _prepare_control_dir,
    _prepare_plain_directory,
)
from .repository_identity import repository_scope


def acquire_writer_claim(
    repo: Path,
    *,
    run_dir: Path,
    task_id: str,
    child_run: str,
    operation_id: str, operation_kind: str = "worker",
) -> None:
    """登记仓库级互斥操作；默认操作仍是真实 Writer。"""
    path = _writer_claim_path(repo)
    payload = {
        "schema_version": 1,
        "repository_root": str(repo.resolve(strict=True)),
        "run_id": run_dir.name,
        "run_dir": str(run_dir.resolve(strict=True)),
        "task_id": task_id,
        "child_run": child_run,
        "operation_id": operation_id,
        "operation_kind": operation_kind,
        "status": "active",
        "acquired_at": datetime.now(UTC).isoformat(),
    }
    try:
        _write_exclusive_json(path, payload)
    except FileExistsError as exc:
        if _remove_released_writer_claim(path):
            try:
                _write_exclusive_json(path, payload)
                return
            except FileExistsError:
                pass
        owner = _read_claim(path, "Writer")
        owner.setdefault("operation_kind", "worker")
        if owner.get("status", "active") in {"active", "releasing"} and all(
            owner.get(key) == payload[key]
            for key in ("repository_root", "run_dir", "run_id", "task_id", "child_run", "operation_id", "operation_kind")
        ):
            return
        raise AgentRepositoryGuardError(
            "当前 Git 仓库已由另一个 Agent Writer 占用："
            f"run={owner.get('run_id', 'unknown')}，"
            f"child={owner.get('child_run', 'unknown')}；"
            "请先完成原 run 的观察或 recovery，禁止启动第二 Writer"
        ) from exc


def mark_writer_claim_releasing(
    repo: Path,
    *,
    run_id: str,
    operation_id: str,
) -> None:
    """在父 State 清除 Writer 前，把 claim 标记为可对账释放。"""

    path = _writer_claim_path(repo)
    payload, identity = _read_claim_with_identity(path, "Writer")
    if (
        payload.get("run_id") != run_id
        or payload.get("operation_id") != operation_id
    ):
        raise AgentRepositoryGuardError(
            "仓库 Writer claim owner 与当前 run 不一致，拒绝进入释放阶段"
        )
    if payload.get("status", "active") == "releasing":
        return
    payload["status"] = "releasing"
    payload["release_marked_at"] = datetime.now(UTC).isoformat()
    _replace_claim_json(path, payload, identity)


def release_writer_claim(
    repo: Path,
    *,
    run_id: str,
    operation_id: str,
) -> None:
    """仅允许登记的 owner 释放仓库 Writer。"""

    path = _writer_claim_path(repo)
    if not os.path.lexists(path):
        raise AgentRepositoryGuardError(
            "仓库 Writer claim 缺失；无法证明当前 run 已正确释放所有权"
        )
    payload, identity = _read_claim_with_identity(path, "Writer")
    if (
        payload.get("run_id") != run_id
        or payload.get("operation_id") != operation_id
    ):
        raise AgentRepositoryGuardError(
            "仓库 Writer claim owner 与当前 run 不一致，拒绝释放他人所有权"
        )
    _unlink_regular_file(path, "Writer claim", expected_identity=identity)


def prepare_terminal_writer_claim_release(
    repo: Path,
    previous_state: AgentState,
    current_state: AgentState,
    observation: AgentObservation,
    authority: ObservationAuthority,
) -> None:
    """在发布解除 Writer 的父 State 前，先冻结 claim 的释放意图。"""

    if not _should_release_terminal_writer(
        previous_state,
        current_state,
        observation,
        authority,
    ):
        return
    assert previous_state.active_operation_id is not None
    mark_writer_claim_releasing(
        repo,
        run_id=current_state.run_id,
        operation_id=previous_state.active_operation_id,
    )


def release_terminal_writer_claim(
    repo: Path,
    previous_state: AgentState,
    current_state: AgentState,
    observation: AgentObservation,
    authority: ObservationAuthority,
) -> None:
    """仅在受信 Worker 已终态且父 State 清除绑定后释放 Writer。"""

    if not _should_release_terminal_writer(
        previous_state,
        current_state,
        observation,
        authority,
    ):
        return
    assert previous_state.active_operation_id is not None
    release_writer_claim(
        repo,
        run_id=current_state.run_id,
        operation_id=previous_state.active_operation_id,
    )


def _should_release_terminal_writer(
    previous_state: AgentState,
    current_state: AgentState,
    observation: AgentObservation,
    authority: ObservationAuthority,
) -> bool:
    return not (
        authority == "external_claim"
        or observation.worker_alive
        or previous_state.active_child_run is None
        or previous_state.active_operation_id is None
        or current_state.active_child_run is not None
    )


def acquire_task_card_resume_claim(
    repo: Path,
    *,
    task_card_sha256: str,
    run_dir: Path,
    task_card: str,
) -> None:
    """同一物理 Git 仓库内，一张 Task Card 只能建立一个本机恢复 run。"""

    if len(task_card_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in task_card_sha256
    ):
        raise AgentRepositoryGuardError("Task Card digest 不是有效 SHA-256")
    path = _task_card_claim_path(repo, task_card_sha256)
    payload = {
        "schema_version": 1,
        "repository_root": str(repo.resolve(strict=True)),
        "task_card_sha256": task_card_sha256,
        "task_card": task_card,
        "run_id": run_dir.name,
        "run_dir": str(run_dir.resolve(strict=True)),
        "acquired_at": datetime.now(UTC).isoformat(),
    }
    try:
        _write_exclusive_json(path, payload)
    except FileExistsError as exc:
        owner = _read_claim(path, "Task Card resume")
        raise AgentRepositoryGuardError(
            "当前 Task Card 已在本机 Git 仓库建立恢复 run："
            f"run={owner.get('run_id', 'unknown')}；"
            "请继续原 run，不能从同一卡重复启动"
        ) from exc


def release_task_card_resume_claim(
    repo: Path,
    *,
    task_card_sha256: str,
    run_id: str,
) -> None:
    """恢复 run 尚未发布时，允许同一 owner 撤销临时占用。"""

    path = _task_card_claim_path(repo, task_card_sha256)
    if not path.exists():
        return
    payload = _read_claim(path, "Task Card resume")
    if (
        payload.get("run_id") != run_id
        or payload.get("task_card_sha256") != task_card_sha256
    ):
        raise AgentRepositoryGuardError(
            "Task Card resume claim owner 不一致，拒绝撤销他人占用"
        )
    _unlink_regular_file(path, "Task Card resume claim")


def _writer_claim_path(repo: Path) -> Path:
    return _prepare_control_dir(repo) / "writer-claim.json"


def _task_card_claim_path(repo: Path, digest: str) -> Path:
    root = _prepare_control_dir(repo) / "task-card-claims"
    _prepare_plain_directory(root, root.parent)
    return root / f"{digest}.json"


def _write_exclusive_json(path: Path, payload: dict[str, object]) -> None:
    if os.path.lexists(path):
        if _is_link_or_reparse(path):
            raise AgentRepositoryGuardError(
                f"Agent claim 不能是链接或 reparse point：{path.name}"
            )
        raise FileExistsError(path)
    flags = (
        os.O_CREAT
        | os.O_EXCL
        | os.O_WRONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = -1
    created_identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        os.set_inheritable(descriptor, False)
        metadata = os.fstat(descriptor)
        created_identity = (metadata.st_dev, metadata.st_ino)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise AgentRepositoryGuardError("Agent claim 必须是单链接普通文件")
        data = (
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        with os.fdopen(descriptor, "wb", buffering=0) as stream:
            descriptor = -1
            stream.write(data)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        # O_EXCL 失败意味着其他进程已经取得 claim。此时绝不能清理该路径，
        # 否则竞争失败的一方会误删真正 owner 的所有权记录。
        if created_identity is not None:
            _unlink_if_same_file(path, created_identity)
        raise


def _read_claim(path: Path, label: str) -> dict[str, object]:
    payload, _ = _read_claim_with_identity(path, label)
    return payload


def _read_claim_with_identity(
    path: Path,
    label: str,
) -> tuple[dict[str, object], tuple[int, int]]:
    if _is_link_or_reparse(path):
        raise AgentRepositoryGuardError(f"{label} claim 不能是链接或 reparse point")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise AgentRepositoryGuardError(
                f"{label} claim 必须是单链接普通文件"
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise AgentRepositoryGuardError(f"{label} claim 超过大小上限")
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AgentRepositoryGuardError(f"{label} claim 无法验证") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise AgentRepositoryGuardError(f"{label} claim schema 无法验证")
    return payload, (metadata.st_dev, metadata.st_ino)


def _unlink_regular_file(
    path: Path,
    label: str,
    *,
    expected_identity: tuple[int, int] | None = None,
) -> None:
    if _is_link_or_reparse(path):
        raise AgentRepositoryGuardError(f"{label} 不能是链接或 reparse point")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise AgentRepositoryGuardError(f"{label} 无法读取") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise AgentRepositoryGuardError(f"{label} 必须是单链接普通文件")
    if (
        expected_identity is not None
        and (metadata.st_dev, metadata.st_ino) != expected_identity
    ):
        raise AgentRepositoryGuardError(f"{label} 在释放前已被替换")
    try:
        path.unlink()
    except OSError as exc:
        raise AgentRepositoryGuardError(f"{label} 无法释放") from exc


def _unlink_if_same_file(path: Path, expected_identity: tuple[int, int]) -> bool:
    """只清理当前调用确实创建的半成品 claim。"""

    try:
        metadata = path.lstat()
    except OSError:
        return False
    if (
        (metadata.st_dev, metadata.st_ino) != expected_identity
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
    ):
        return False
    try:
        path.unlink()
    except OSError:
        # 原始写入异常优先向上抛出；残留 claim 会继续 fail-closed，
        # 后续由人工核对，不能在这里扩大清理范围。
        return False
    return True


def _replace_claim_json(
    path: Path,
    payload: dict[str, object],
    expected_identity: tuple[int, int],
) -> None:
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    _write_exclusive_json(temp, payload)
    try:
        metadata = path.lstat()
        if (
            (metadata.st_dev, metadata.st_ino) != expected_identity
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise AgentRepositoryGuardError(
                "Writer claim 在标记释放阶段前已被替换"
            )
        os.replace(temp, path)
    except OSError as exc:
        raise AgentRepositoryGuardError("Writer claim 无法标记释放阶段") from exc
    finally:
        if os.path.lexists(temp):
            try:
                temp.unlink()
            except OSError:
                pass


def _remove_released_writer_claim(path: Path) -> bool:
    """只清理已经发布无 active Writer State 的 releasing claim。"""

    try:
        payload, identity = _read_claim_with_identity(path, "Writer")
    except AgentRepositoryGuardError:
        return False
    if payload.get("status") != "releasing":
        return False
    run_id = payload.get("run_id")
    operation_id = payload.get("operation_id")
    raw_run_dir = payload.get("run_dir")
    if (
        not isinstance(run_id, str)
        or not isinstance(operation_id, str)
        or not isinstance(raw_run_dir, str)
    ):
        return False
    run_dir = Path(raw_run_dir)
    if not run_dir.is_absolute() or run_dir.name != run_id:
        return False
    raw_repository_root = payload.get("repository_root")
    if not isinstance(raw_repository_root, str) or not Path(raw_repository_root).is_absolute():
        return False
    owner_repo = Path(raw_repository_root).resolve(strict=False)
    if owner_repo.exists() and (not owner_repo.is_dir() or _writer_claim_path(owner_repo) != path):
        return False
    try:
        state = load_agent_state(run_dir / "agent-state.json")
    except (OSError, AgentArtifactError):
        return False
    if (
        state.run_id != run_id
        or state.repository_id != repository_scope(owner_repo)
        or state.active_child_run is not None
        or state.active_operation_id is not None
        or state.operation_started
    ):
        return False
    return _unlink_if_same_file(path, identity)
