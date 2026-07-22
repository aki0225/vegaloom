from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, cast

LoopEngineName = Literal["linear", "langgraph"]
DEFAULT_LOOP_ENGINE: LoopEngineName = "linear"
ENGINE_KEY_PATTERN = re.compile(r'"engine"\s*:')
KNOWN_ENGINE_VALUE_PATTERN = re.compile(
    r'"engine"\s*:\s*"(linear|langgraph)"'
)


def normalize_loop_engine(value: str | None) -> LoopEngineName:
    normalized = DEFAULT_LOOP_ENGINE if value is None else value.strip().lower()
    if normalized not in {"linear", "langgraph"}:
        raise ValueError("engine 只能是 linear 或 langgraph")
    return cast(LoopEngineName, normalized)


def ensure_loop_engine_matches(
    persisted_engine: str | None,
    requested_engine: str | None,
) -> LoopEngineName:
    persisted = normalize_loop_engine(persisted_engine)
    if requested_engine is None:
        return persisted
    requested = normalize_loop_engine(requested_engine)
    if requested != persisted:
        raise ValueError(
            f"run engine 已固定为 {persisted}，不能切换为 {requested}"
        )
    return persisted


def read_persisted_loop_engine(
    payload: Mapping[str, object],
) -> LoopEngineName:
    if "engine" not in payload:
        return DEFAULT_LOOP_ENGINE
    value = payload["engine"]
    if not isinstance(value, str) or value not in {"linear", "langgraph"}:
        raise ValueError("loop state.json 的 engine 字段不合法")
    return cast(LoopEngineName, value)


def parse_loop_state_json(raw: str) -> object:
    def reject_duplicate_engine_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        payload: dict[str, object] = {}
        engine_seen = False
        for key, value in pairs:
            if key == "engine":
                if engine_seen:
                    raise ValueError("loop state.json 包含重复 engine 字段")
                engine_seen = True
            payload[key] = value
        return payload

    return json.loads(
        raw,
        object_pairs_hook=reject_duplicate_engine_keys,
    )


def require_persisted_linear_engine(
    persisted_engine: str | None,
    requested_engine: str | None,
) -> LoopEngineName:
    persisted = ensure_loop_engine_matches(persisted_engine, requested_engine)
    if persisted == "linear":
        return persisted
    raise ValueError(
        "该 run 使用 langgraph engine；"
        "linear Runtime 不允许修改"
    )


def preflight_persisted_linear_engine(
    state_path: Path,
    requested_engine: str | None,
) -> None:
    """在任何 artifact 写入前，只读确认 run 的 engine 所有权。

    缺少 engine 字段的旧 run 按 linear 解释。即使 JSON 其他部分已损坏，只要
    raw state 声明了 engine，就必须先建立唯一所有权；重复、未知或无法可靠解析的
    engine 都会在 Runtime 写 diagnostic 前被拒绝。
    """
    try:
        raw = state_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(
            "loop state.json 无法读取，不能确认 engine 所有权；"
            "linear Runtime 已拒绝写入"
        ) from exc
    try:
        payload = parse_loop_state_json(raw)
    except json.JSONDecodeError:
        engine_key_count = len(ENGINE_KEY_PATTERN.findall(raw))
        if engine_key_count == 0:
            return
        known_values = KNOWN_ENGINE_VALUE_PATTERN.findall(raw)
        if engine_key_count != 1 or len(known_values) != 1:
            raise ValueError(
                "损坏的 loop state.json 无法唯一识别 engine；"
                "linear Runtime 已拒绝写入"
            )
        require_persisted_linear_engine(known_values[0], requested_engine)
        return
    if not isinstance(payload, dict):
        return
    persisted = read_persisted_loop_engine(payload)
    require_persisted_linear_engine(persisted, requested_engine)


def preflight_persisted_engine(
    state_path: Path,
    requested_engine: str | None,
) -> LoopEngineName:
    """只读识别 run engine，供 recover 在 linear 与 langgraph 间安全分派。"""

    try:
        raw = state_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(
            "loop state.json 无法读取，不能确认 engine 所有权"
        ) from exc
    try:
        payload = parse_loop_state_json(raw)
    except ValueError as exc:
        if "重复 engine" in str(exc):
            raise
        payload = None
    except json.JSONDecodeError:
        payload = None
    if payload is None:
        engine_key_count = len(ENGINE_KEY_PATTERN.findall(raw))
        if engine_key_count == 0:
            return ensure_loop_engine_matches(
                DEFAULT_LOOP_ENGINE,
                requested_engine,
            )
        known_values = KNOWN_ENGINE_VALUE_PATTERN.findall(raw)
        if engine_key_count != 1 or len(known_values) != 1:
            raise ValueError(
                "损坏的 loop state.json 无法唯一识别 engine；"
                "recover 已拒绝写入"
            )
        return ensure_loop_engine_matches(known_values[0], requested_engine)
    if not isinstance(payload, dict):
        return ensure_loop_engine_matches(
            DEFAULT_LOOP_ENGINE,
            requested_engine,
        )
    persisted = read_persisted_loop_engine(payload)
    return ensure_loop_engine_matches(persisted, requested_engine)
