from __future__ import annotations

from typing import Protocol


class ExecutionIdentity(Protocol):
    execution_id: str | None
    started_at: str
    step: str
    iteration: int | None
    owner_pid: int


class StopRequestIdentity(Protocol):
    execution_id: str | None
    execution_started_at: str | None


def validate_explicit_execution_id(execution_id: str) -> None:
    if (
        not execution_id
        or execution_id != execution_id.strip()
        or len(execution_id) > 128
        or any(character in execution_id for character in ("\r", "\n", "\0"))
    ):
        raise ValueError(
            "execution_id 必须是 1..128 个字符，且不能包含首尾空白、换行或 NUL"
        )


def same_execution_identity(
    left: ExecutionIdentity,
    right: ExecutionIdentity,
) -> bool:
    if left.execution_id is not None or right.execution_id is not None:
        return (
            left.execution_id is not None
            and left.execution_id == right.execution_id
        )
    return (
        left.started_at == right.started_at
        and left.step == right.step
        and left.iteration == right.iteration
        and left.owner_pid == right.owner_pid
    )


def stop_request_matches_lease(
    request: StopRequestIdentity,
    lease: ExecutionIdentity,
) -> bool:
    if request.execution_id is not None:
        return (
            lease.execution_id is not None
            and request.execution_id == lease.execution_id
        )
    if request.execution_started_at is not None:
        return request.execution_started_at == lease.started_at
    # 旧三字段请求只能由同样没有 execution_id 的旧 lease 消费。
    return lease.execution_id is None
