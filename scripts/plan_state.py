from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Literal, cast

PLAN_PATH = Path("plans/vega-agent-evolution.json")
EVENTS_DIR = Path("plans/events")
CURRENT_VIEW_PATH = Path("docs/CURRENT.md")

PLAN_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
ITEM_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9-]*$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
EVENT_ID_PATTERN = re.compile(
    r"^(?P<timestamp>\d{8}T\d{6}Z)-"
    r"(?P<item>[A-Z][A-Z0-9-]*)-"
    r"(?P<transition>started|completed|blocked|superseded|reopened)$"
)

PlanTransition = Literal[
    "started",
    "completed",
    "blocked",
    "superseded",
    "reopened",
]
PlanItemStatus = Literal[
    "pending",
    "in_progress",
    "completed",
    "blocked",
    "superseded",
]


class PlanStateError(ValueError):
    """计划定义、事件或生成状态不可信。"""


@dataclass(frozen=True)
class PlanItem:
    item_id: str
    title: str
    summary: str
    depends_on: tuple[str, ...]
    acceptance: tuple[str, ...]
    required_checks: tuple[str, ...]


@dataclass(frozen=True)
class PlanDefinition:
    plan_id: str
    title: str
    summary: str
    items: tuple[PlanItem, ...]

    @property
    def item_map(self) -> dict[str, PlanItem]:
        return {item.item_id: item for item in self.items}


@dataclass(frozen=True)
class PlanEvidence:
    pull_requests: tuple[int, ...]
    commits: tuple[str, ...]
    checks: tuple[str, ...]
    artifacts: tuple[str, ...]

    @property
    def is_empty(self) -> bool:
        return not (
            self.pull_requests
            or self.commits
            or self.checks
            or self.artifacts
        )


@dataclass(frozen=True)
class PlanEvent:
    event_id: str
    plan_id: str
    item_id: str
    transition: PlanTransition
    recorded_at: datetime
    summary: str
    evidence: PlanEvidence
    relative_path: str


@dataclass(frozen=True)
class PlanSnapshot:
    plan: PlanDefinition
    events: tuple[PlanEvent, ...]
    statuses: dict[str, PlanItemStatus]
    latest_events: dict[str, PlanEvent]
    current_item_id: str | None

    @property
    def completed_count(self) -> int:
        return sum(status == "completed" for status in self.statuses.values())


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PlanStateError(f"JSON 包含重复字段：{key}")
        result[key] = value
    return result


def _decode_json(payload: bytes, label: str) -> object:
    if payload.startswith(b"\xef\xbb\xbf"):
        raise PlanStateError(f"{label} 必须使用 UTF-8 无 BOM")
    try:
        text = payload.decode("utf-8")
        return json.loads(text, object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PlanStateError(f"{label} 不是有效 UTF-8 JSON：{exc}") from exc


def _load_json(path: Path, label: str) -> object:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise PlanStateError(f"无法读取 {label}") from exc
    return _decode_json(payload, label)


def _repo_file(repo_root: Path, relative_path: Path, label: str) -> Path:
    root = repo_root.resolve()
    path = repo_root / relative_path
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise PlanStateError(f"{label} 缺失或越出仓库边界") from exc
    if path.is_symlink() or not resolved.is_file():
        raise PlanStateError(f"{label} 必须是仓库内的普通文件")
    return path


def _repo_output_path(repo_root: Path, relative_path: Path, label: str) -> Path:
    root = repo_root.resolve()
    path = repo_root / relative_path
    missing_parents: list[Path] = []
    existing_parent = path.parent
    while not existing_parent.exists():
        if existing_parent.is_symlink():
            raise PlanStateError(f"{label} 的父目录不得使用符号链接")
        missing_parents.append(existing_parent)
        if existing_parent == repo_root:
            break
        existing_parent = existing_parent.parent
    try:
        existing_parent.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise PlanStateError(f"{label} 的父目录越出仓库边界") from exc
    for parent in reversed(missing_parents):
        parent.mkdir()
    try:
        path.parent.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise PlanStateError(f"{label} 的父目录创建后越出仓库边界") from exc
    if path.exists() or path.is_symlink():
        return _repo_file(repo_root, relative_path, label)
    return path


def _require_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PlanStateError(f"{label} 必须是 JSON object")
    return value


def _require_exact_fields(
    value: dict[str, object],
    *,
    label: str,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    missing = sorted(required.difference(value))
    unknown = sorted(set(value).difference(required | optional))
    if missing:
        raise PlanStateError(f"{label} 缺少字段：{missing}")
    if unknown:
        raise PlanStateError(f"{label} 包含未知字段：{unknown}")


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanStateError(f"{label} 必须是非空字符串")
    return value.strip()


def _require_string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise PlanStateError(f"{label} 必须是字符串数组")
    result = tuple(_require_string(item, label) for item in value)
    if len(set(result)) != len(result):
        raise PlanStateError(f"{label} 不得包含重复值")
    return result


def _require_integer_list(value: object, label: str) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise PlanStateError(f"{label} 必须是正整数数组")
    result: list[int] = []
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool) or item <= 0:
            raise PlanStateError(f"{label} 必须是正整数数组")
        result.append(item)
    if len(set(result)) != len(result):
        raise PlanStateError(f"{label} 不得包含重复值")
    return tuple(result)


def _require_repo_relative_path(value: str, label: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise PlanStateError(f"{label} 必须使用仓库相对路径：{value}")
    return path.as_posix()


def parse_plan(value: object, label: str) -> PlanDefinition:
    payload = _require_object(value, label)
    _require_exact_fields(
        payload,
        label=label,
        required={"schema_version", "plan_id", "title", "summary", "items"},
    )
    if payload["schema_version"] != 1:
        raise PlanStateError("计划 schema_version 必须为 1")
    plan_id = _require_string(payload["plan_id"], "plan_id")
    if PLAN_ID_PATTERN.fullmatch(plan_id) is None:
        raise PlanStateError("plan_id 必须使用小写字母、数字和连字符")
    items_value = payload["items"]
    if not isinstance(items_value, list) or not items_value:
        raise PlanStateError("计划必须至少包含一个事项")

    items: list[PlanItem] = []
    seen: set[str] = set()
    for index, item_value in enumerate(items_value):
        label = f"items[{index}]"
        item_payload = _require_object(item_value, label)
        _require_exact_fields(
            item_payload,
            label=label,
            required={
                "id",
                "title",
                "summary",
                "depends_on",
                "acceptance",
                "required_checks",
            },
        )
        item_id = _require_string(item_payload["id"], f"{label}.id")
        if ITEM_ID_PATTERN.fullmatch(item_id) is None:
            raise PlanStateError(f"{label}.id 格式无效：{item_id}")
        if item_id in seen:
            raise PlanStateError(f"计划事项 ID 重复：{item_id}")
        depends_on = _require_string_list(
            item_payload["depends_on"],
            f"{label}.depends_on",
        )
        unknown_or_late = [dependency for dependency in depends_on if dependency not in seen]
        if unknown_or_late:
            raise PlanStateError(
                f"{item_id} 的前置事项必须已经在计划中定义：{unknown_or_late}"
            )
        acceptance = _require_string_list(
            item_payload["acceptance"],
            f"{label}.acceptance",
        )
        required_checks = _require_string_list(
            item_payload["required_checks"],
            f"{label}.required_checks",
        )
        if not acceptance:
            raise PlanStateError(f"{item_id} 必须声明验收条件")
        if not required_checks:
            raise PlanStateError(f"{item_id} 必须声明验证检查")
        seen.add(item_id)
        items.append(
            PlanItem(
                item_id=item_id,
                title=_require_string(item_payload["title"], f"{label}.title"),
                summary=_require_string(
                    item_payload["summary"],
                    f"{label}.summary",
                ),
                depends_on=depends_on,
                acceptance=acceptance,
                required_checks=required_checks,
            )
        )
    return PlanDefinition(
        plan_id=plan_id,
        title=_require_string(payload["title"], "title"),
        summary=_require_string(payload["summary"], "summary"),
        items=tuple(items),
    )


def load_plan(repo_root: Path) -> PlanDefinition:
    label = PLAN_PATH.as_posix()
    path = _repo_file(repo_root, PLAN_PATH, label)
    return parse_plan(_load_json(path, label), label)


def _parse_recorded_at(value: object, label: str) -> datetime:
    text = _require_string(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PlanStateError(f"{label} 不是有效 ISO-8601 时间") from exc
    if parsed.tzinfo is None:
        raise PlanStateError(f"{label} 必须包含时区")
    parsed = parsed.astimezone(timezone.utc)
    if parsed.microsecond:
        raise PlanStateError(f"{label} 不得包含小数秒")
    return parsed


def _load_evidence(value: object, label: str) -> PlanEvidence:
    payload = _require_object(value, label)
    _require_exact_fields(
        payload,
        label=label,
        required={"pull_requests", "commits", "checks", "artifacts"},
    )
    commits = _require_string_list(payload["commits"], f"{label}.commits")
    invalid_commits = [commit for commit in commits if COMMIT_PATTERN.fullmatch(commit) is None]
    if invalid_commits:
        raise PlanStateError(f"{label}.commits 必须使用完整小写 Git SHA")
    artifacts = tuple(
        _require_repo_relative_path(path, f"{label}.artifacts")
        for path in _require_string_list(payload["artifacts"], f"{label}.artifacts")
    )
    evidence = PlanEvidence(
        pull_requests=_require_integer_list(
            payload["pull_requests"],
            f"{label}.pull_requests",
        ),
        commits=commits,
        checks=_require_string_list(payload["checks"], f"{label}.checks"),
        artifacts=artifacts,
    )
    if evidence.is_empty:
        raise PlanStateError(f"{label} 至少需要一种证据")
    return evidence


def load_events(repo_root: Path, plan: PlanDefinition) -> tuple[PlanEvent, ...]:
    events_dir = repo_root / EVENTS_DIR
    if not events_dir.is_dir():
        raise PlanStateError(f"计划事件目录缺失：{EVENTS_DIR.as_posix()}")
    try:
        events_dir.resolve(strict=True).relative_to(repo_root.resolve())
    except (OSError, ValueError) as exc:
        raise PlanStateError("计划事件目录越出仓库边界") from exc
    events: list[PlanEvent] = []
    seen: set[str] = set()
    item_map = plan.item_map
    for path in sorted(events_dir.glob("*.json")):
        relative = path.relative_to(repo_root).as_posix()
        trusted_path = _repo_file(repo_root, path.relative_to(repo_root), relative)
        payload = _require_object(_load_json(trusted_path, relative), relative)
        _require_exact_fields(
            payload,
            label=relative,
            required={
                "schema_version",
                "event_id",
                "plan_id",
                "item_id",
                "transition",
                "recorded_at",
                "summary",
                "evidence",
            },
        )
        if payload["schema_version"] != 1:
            raise PlanStateError(f"{relative} schema_version 必须为 1")
        event_id = _require_string(payload["event_id"], f"{relative}.event_id")
        if event_id in seen:
            raise PlanStateError(f"计划事件 ID 重复：{event_id}")
        if path.stem != event_id:
            raise PlanStateError(f"{relative} 文件名必须等于 event_id")
        match = EVENT_ID_PATTERN.fullmatch(event_id)
        if match is None:
            raise PlanStateError(f"{relative} event_id 格式无效")
        plan_id = _require_string(payload["plan_id"], f"{relative}.plan_id")
        if plan_id != plan.plan_id:
            raise PlanStateError(f"{relative} plan_id 与当前计划不一致")
        item_id = _require_string(payload["item_id"], f"{relative}.item_id")
        if item_id not in item_map:
            raise PlanStateError(f"{relative} 引用了未知事项：{item_id}")
        transition = _require_string(
            payload["transition"],
            f"{relative}.transition",
        )
        if transition not in {
            "started",
            "completed",
            "blocked",
            "superseded",
            "reopened",
        }:
            raise PlanStateError(f"{relative} transition 不受支持：{transition}")
        if match.group("item") != item_id or match.group("transition") != transition:
            raise PlanStateError(f"{relative} event_id 与事项或 transition 不一致")
        recorded_at = _parse_recorded_at(
            payload["recorded_at"],
            f"{relative}.recorded_at",
        )
        expected_timestamp = recorded_at.strftime("%Y%m%dT%H%M%SZ")
        if match.group("timestamp") != expected_timestamp:
            raise PlanStateError(f"{relative} event_id 时间与 recorded_at 不一致")
        evidence = _load_evidence(payload["evidence"], f"{relative}.evidence")
        missing_checks = sorted(
            set(item_map[item_id].required_checks).difference(evidence.checks)
        )
        if transition == "completed" and missing_checks:
            raise PlanStateError(
                f"{relative} 完成事件缺少事项要求的检查：{missing_checks}"
            )
        seen.add(event_id)
        events.append(
            PlanEvent(
                event_id=event_id,
                plan_id=plan_id,
                item_id=item_id,
                transition=cast(PlanTransition, transition),
                recorded_at=recorded_at,
                summary=_require_string(payload["summary"], f"{relative}.summary"),
                evidence=evidence,
                relative_path=relative,
            )
        )
    return tuple(sorted(events, key=lambda event: (event.recorded_at, event.event_id)))


def build_snapshot(plan: PlanDefinition, events: tuple[PlanEvent, ...]) -> PlanSnapshot:
    statuses: dict[str, PlanItemStatus] = {
        item.item_id: "pending" for item in plan.items
    }
    latest_events: dict[str, PlanEvent] = {}
    item_map = plan.item_map

    for event in events:
        current = statuses[event.item_id]
        dependencies = item_map[event.item_id].depends_on
        if event.transition in {"started", "completed"}:
            incomplete = [
                dependency
                for dependency in dependencies
                if statuses[dependency] != "completed"
            ]
            if incomplete:
                raise PlanStateError(
                    f"{event.relative_path} 在前置事项完成前推进：{incomplete}"
                )
        if event.transition == "started":
            if current != "pending":
                raise PlanStateError(f"{event.relative_path} 只能从 pending 开始")
            active = [
                item_id
                for item_id, status in statuses.items()
                if status == "in_progress"
            ]
            if active:
                raise PlanStateError(
                    f"{event.relative_path} 启动时仍有进行中事项：{active}"
                )
            statuses[event.item_id] = "in_progress"
        elif event.transition == "completed":
            if current not in {"pending", "in_progress"}:
                raise PlanStateError(f"{event.relative_path} 不能从 {current} 完成")
            statuses[event.item_id] = "completed"
        elif event.transition == "blocked":
            if current not in {"pending", "in_progress"}:
                raise PlanStateError(f"{event.relative_path} 不能从 {current} 阻塞")
            statuses[event.item_id] = "blocked"
        elif event.transition == "superseded":
            if current not in {"pending", "in_progress", "blocked"}:
                raise PlanStateError(f"{event.relative_path} 不能从 {current} 替代")
            statuses[event.item_id] = "superseded"
        else:
            if current not in {"completed", "blocked"}:
                raise PlanStateError(f"{event.relative_path} 不能从 {current} 重新打开")
            statuses[event.item_id] = "pending"
        latest_events[event.item_id] = event

    for item in plan.items:
        if statuses[item.item_id] not in {"completed", "in_progress"}:
            continue
        incomplete = [
            dependency
            for dependency in item.depends_on
            if statuses[dependency] != "completed"
        ]
        if incomplete:
            raise PlanStateError(
                f"{item.item_id} 的前置事项后来失效，必须先重新打开当前事项：{incomplete}"
            )

    current_item_id = next(
        (
            item.item_id
            for item in plan.items
            if statuses[item.item_id] == "in_progress"
        ),
        None,
    )
    if current_item_id is None:
        current_item_id = next(
            (
                item.item_id
                for item in plan.items
                if statuses[item.item_id] == "pending"
                and all(
                    statuses[dependency] == "completed"
                    for dependency in item.depends_on
                )
            ),
            None,
        )
    return PlanSnapshot(
        plan=plan,
        events=events,
        statuses=statuses,
        latest_events=latest_events,
        current_item_id=current_item_id,
    )


def load_snapshot(repo_root: Path) -> PlanSnapshot:
    plan = load_plan(repo_root)
    events = load_events(repo_root, plan)
    return build_snapshot(plan, events)


def render_current(snapshot: PlanSnapshot) -> str:
    latest = snapshot.events[-1] if snapshot.events else None
    lines = [
        "# Vega 当前计划状态",
        "",
        "> 本文件由 `python scripts/plan_state.py render` 生成，不手工修改。",
        "> 计划定义与完成事件分别位于 `plans/vega-agent-evolution.json` 和 `plans/events/`。",
        "",
        f"- 计划：{snapshot.plan.title}",
        f"- 计划 ID：`{snapshot.plan.plan_id}`",
        f"- 已完成：{snapshot.completed_count} / {len(snapshot.plan.items)}",
        (
            f"- 最近事件：`{latest.event_id}`"
            if latest is not None
            else "- 最近事件：无"
        ),
        "",
        "## 当前事项",
        "",
    ]
    if snapshot.current_item_id is None:
        lines.extend(
            [
                "当前没有可执行事项；计划已经完成，或剩余事项处于阻塞/已替代状态。",
                "",
            ]
        )
    else:
        item = snapshot.plan.item_map[snapshot.current_item_id]
        status = snapshot.statuses[item.item_id]
        heading = "进行中" if status == "in_progress" else "下一项"
        lines.extend(
            [
                f"### {heading}：`{item.item_id}` {item.title}",
                "",
                item.summary,
                "",
                "验收条件：",
                "",
                *[f"- {value}" for value in item.acceptance],
                "",
                "要求检查：",
                "",
                *[f"- `{value}`" for value in item.required_checks],
                "",
            ]
        )

    status_labels = {
        "pending": "待开始",
        "in_progress": "进行中",
        "completed": "已完成",
        "blocked": "阻塞",
        "superseded": "已替代",
    }
    lines.extend(
        [
            "## 全部事项",
            "",
            "| 状态 | ID | 事项 | 前置事项 |",
            "|---|---|---|---|",
        ]
    )
    for item in snapshot.plan.items:
        dependencies = "、".join(f"`{value}`" for value in item.depends_on) or "—"
        lines.append(
            f"| {status_labels[snapshot.statuses[item.item_id]]} "
            f"| `{item.item_id}` | {item.title} | {dependencies} |"
        )
    lines.extend(
        [
            "",
            "## 状态规则",
            "",
            "- 计划文件只描述事项、依赖和验收条件，不记录“等待 CI”等瞬时状态。",
            "- 实现 PR 在同一 Diff 中增加完成事件；事件进入 `main` 后，该事项才成为主线事实。",
            "- CI 失败或 PR 关闭不会改变主线状态；合并后不再补状态专用提交。",
            "- 已进入主线的事件只允许追加，不允许改写或删除。",
            "- 已有状态事件的事项定义保持不变；尚无事件的未来事项可以随实现证据调整。",
            "- 当前事项由事项依赖和事件账本确定，不由聊天记录或手工摘要决定。",
            "",
        ]
    )
    return "\n".join(lines)


def _run_git(
    repo_root: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
        }
    )
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={repo_root.resolve().as_posix()}",
            *args,
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        env=env,
    )
    if check and result.returncode != 0:
        command = " ".join(args[:2])
        raise PlanStateError(f"Git 命令失败：{command}")
    return result


def _git_payload(
    repo_root: Path,
    revision: str,
    relative_path: str,
) -> bytes | None:
    result = _run_git(
        repo_root,
        "show",
        f"{revision}:{relative_path}",
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def _normalized_payload(payload: bytes | None) -> bytes | None:
    if payload is None:
        return None
    return payload.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _worktree_payload(repo_root: Path, relative_path: str) -> bytes | None:
    try:
        trusted = _repo_file(repo_root, Path(relative_path), relative_path)
    except PlanStateError:
        return None
    return trusted.read_bytes()


def _intermediate_commits(repo_root: Path, base_ref: str) -> tuple[str, ...]:
    head = _run_git(repo_root, "rev-parse", "HEAD").stdout.decode("ascii").strip()
    output = _run_git(
        repo_root,
        "rev-list",
        "--reverse",
        f"{base_ref}..HEAD",
    ).stdout.decode("ascii")
    return tuple(
        commit
        for commit in output.splitlines()
        if commit and commit != head
    )


def _commit_parents(repo_root: Path, commit: str) -> tuple[str, ...]:
    fields = (
        _run_git(repo_root, "rev-list", "--parents", "-n", "1", commit)
        .stdout.decode("ascii")
        .split()
    )
    return tuple(fields[1:])


def _event_item_ids_at_revision(
    repo_root: Path,
    revision: str,
) -> tuple[str, ...]:
    output = _run_git(
        repo_root,
        "ls-tree",
        "-r",
        "--name-only",
        revision,
        "--",
        EVENTS_DIR.as_posix(),
    ).stdout.decode("utf-8")
    item_ids: list[str] = []
    for relative_path in sorted(
        path
        for path in output.splitlines()
        if path.startswith(f"{EVENTS_DIR.as_posix()}/") and path.endswith(".json")
    ):
        match = EVENT_ID_PATTERN.fullmatch(PurePosixPath(relative_path).stem)
        if match is None:
            raise PlanStateError(f"基线包含无效计划事件文件名：{relative_path}")
        item_id = match.group("item")
        if item_id not in item_ids:
            item_ids.append(item_id)
    return tuple(item_ids)


def _plan_revision_issues(
    reference: PlanDefinition,
    candidate: PlanDefinition | None,
    source: str,
    *,
    protected_item_ids: tuple[str, ...],
) -> list[str]:
    if candidate is None:
        return [f"既有计划不得删除（{source}）"]
    issues: list[str] = []
    if candidate.plan_id != reference.plan_id:
        issues.append(f"既有计划 ID 不得改写（{source}）")

    reference_items = reference.item_map
    candidate_items = candidate.item_map
    missing_reference_ids = [
        item_id for item_id in protected_item_ids if item_id not in reference_items
    ]
    if missing_reference_ids:
        raise PlanStateError(
            f"基线事件引用了计划中不存在的事项：{missing_reference_ids}"
        )

    for item_id in protected_item_ids:
        candidate_item = candidate_items.get(item_id)
        if candidate_item is None:
            issues.append(f"已有状态事件的计划事项不得删除：{item_id}（{source}）")
        elif candidate_item != reference_items[item_id]:
            issues.append(
                f"已有状态事件的计划事项不得改写：{item_id}（{source}）"
            )
    reference_order = [
        item.item_id
        for item in reference.items
        if item.item_id in protected_item_ids
    ]
    candidate_order = [
        item.item_id
        for item in candidate.items
        if item.item_id in protected_item_ids
    ]
    if candidate_order != reference_order:
        issues.append(f"已有状态事件的计划事项不得重排（{source}）")
    return issues


def check_plan_history(
    repo_root: Path,
    base_ref: str,
    current_plan: PlanDefinition,
) -> list[str]:
    base_payload = _git_payload(repo_root, base_ref, PLAN_PATH.as_posix())
    if base_payload is None:
        return []
    base_plan = parse_plan(
        _decode_json(base_payload, f"{PLAN_PATH.as_posix()}（基线）"),
        f"{PLAN_PATH.as_posix()}（基线）",
    )
    protected_item_ids = _event_item_ids_at_revision(repo_root, base_ref)
    issues: list[str] = []
    candidate_payloads = {
        "HEAD": _git_payload(repo_root, "HEAD", PLAN_PATH.as_posix()),
        "index": _git_payload(repo_root, "", PLAN_PATH.as_posix()),
    }
    candidates: dict[str, PlanDefinition | None] = {}
    for source, payload in candidate_payloads.items():
        candidates[source] = (
            parse_plan(
                _decode_json(payload, f"{PLAN_PATH.as_posix()}（{source}）"),
                f"{PLAN_PATH.as_posix()}（{source}）",
            )
            if payload is not None
            else None
        )
    candidates["worktree"] = current_plan

    for source, candidate in candidates.items():
        issues.extend(
            _plan_revision_issues(
                base_plan,
                candidate,
                source,
                protected_item_ids=protected_item_ids,
            )
        )

    for commit in _intermediate_commits(repo_root, base_ref):
        current_payload = _git_payload(repo_root, commit, PLAN_PATH.as_posix())
        current = (
            parse_plan(
                _decode_json(
                    current_payload,
                    f"{PLAN_PATH.as_posix()}（commit {commit[:12]}）",
                ),
                f"{PLAN_PATH.as_posix()}（commit {commit[:12]}）",
            )
            if current_payload is not None
            else None
        )
        for parent in _commit_parents(repo_root, commit):
            parent_payload = _git_payload(repo_root, parent, PLAN_PATH.as_posix())
            if parent_payload is None:
                continue
            parent_plan = parse_plan(
                _decode_json(
                    parent_payload,
                    f"{PLAN_PATH.as_posix()}（parent {parent[:12]}）",
                ),
                f"{PLAN_PATH.as_posix()}（parent {parent[:12]}）",
            )
            issues.extend(
                _plan_revision_issues(
                    base_plan,
                    current,
                    f"commit {commit[:12]}",
                    protected_item_ids=tuple(
                        item_id
                        for item_id in protected_item_ids
                        if item_id in parent_plan.item_map
                    ),
                )
            )
    return list(dict.fromkeys(issues))


def check_event_history(repo_root: Path, base_ref: str) -> list[str]:
    _run_git(repo_root, "rev-parse", "--verify", base_ref)
    output = _run_git(
        repo_root,
        "ls-tree",
        "-r",
        "--name-only",
        base_ref,
        "--",
        EVENTS_DIR.as_posix(),
    ).stdout.decode("utf-8")
    issues: list[str] = []
    intermediate_commits = _intermediate_commits(repo_root, base_ref)
    for relative_path in sorted(
        path
        for path in output.splitlines()
        if path.startswith(f"{EVENTS_DIR.as_posix()}/") and path.endswith(".json")
    ):
        base_payload = _normalized_payload(
            _git_payload(repo_root, base_ref, relative_path)
        )
        assert base_payload is not None
        candidates = {
            "HEAD": _normalized_payload(
                _git_payload(repo_root, "HEAD", relative_path)
            ),
            "index": _normalized_payload(
                _git_payload(repo_root, "", relative_path)
            ),
            "worktree": _normalized_payload(
                _worktree_payload(repo_root, relative_path)
            ),
        }
        for source, payload in candidates.items():
            if payload is None:
                issues.append(f"既有计划事件不得删除：{relative_path}（{source}）")
            elif payload != base_payload:
                issues.append(f"既有计划事件不得改写：{relative_path}（{source}）")
        for commit in intermediate_commits:
            current_payload = _normalized_payload(
                _git_payload(repo_root, commit, relative_path)
            )
            for parent in _commit_parents(repo_root, commit):
                parent_payload = _normalized_payload(
                    _git_payload(repo_root, parent, relative_path)
                )
                if parent_payload is not None and current_payload != parent_payload:
                    issues.append(
                        "既有计划事件不得在提交历史中改写或删除："
                        f"{relative_path}（commit {commit[:12]}）"
                    )
                    break
    return list(dict.fromkeys(issues))


def check_current_view(repo_root: Path, snapshot: PlanSnapshot) -> list[str]:
    try:
        path = _repo_file(
            repo_root,
            CURRENT_VIEW_PATH,
            CURRENT_VIEW_PATH.as_posix(),
        )
    except PlanStateError:
        return [f"生成状态文件缺失：{CURRENT_VIEW_PATH.as_posix()}"]
    expected = render_current(snapshot).encode("utf-8")
    actual = path.read_bytes()
    if actual.startswith(b"\xef\xbb\xbf"):
        return [f"{CURRENT_VIEW_PATH.as_posix()} 必须使用 UTF-8 无 BOM"]
    if actual != expected:
        return [
            "当前计划视图与计划事件不一致；"
            "请运行 `python scripts/plan_state.py render`"
        ]
    return []


def write_current_view(repo_root: Path, snapshot: PlanSnapshot) -> None:
    path = _repo_output_path(
        repo_root,
        CURRENT_VIEW_PATH,
        CURRENT_VIEW_PATH.as_posix(),
    )
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(render_current(snapshot))


def check_plan_state(repo_root: Path, base_ref: str | None = None) -> list[str]:
    snapshot = load_snapshot(repo_root)
    issues = check_current_view(repo_root, snapshot)
    if base_ref:
        _run_git(repo_root, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
        issues.extend(check_plan_history(repo_root, base_ref, snapshot.plan))
        issues.extend(check_event_history(repo_root, base_ref))
    if not issues:
        print(
            "计划状态检查通过："
            f"{snapshot.completed_count}/{len(snapshot.plan.items)} 已完成，"
            f"当前事项 {snapshot.current_item_id or '无'}"
        )
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查并生成 Vega 的版本化计划状态。")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="仓库根目录，默认当前目录。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check", help="检查计划、事件和当前视图。")
    check_parser.add_argument(
        "--base-ref",
        help="比较基线；提供后会拒绝改写或删除既有完成事件。",
    )
    render_parser = subparsers.add_parser("render", help="生成当前计划视图。")
    render_parser.add_argument(
        "--check",
        action="store_true",
        help="只检查生成结果，不写文件。",
    )
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    try:
        snapshot = load_snapshot(repo_root)
        if args.command == "render":
            issues = check_current_view(repo_root, snapshot) if args.check else []
            if not args.check:
                write_current_view(repo_root, snapshot)
                print(f"已生成 {CURRENT_VIEW_PATH.as_posix()}")
        else:
            issues = check_plan_state(repo_root, args.base_ref)
    except (
        OSError,
        PlanStateError,
        UnicodeError,
    ) as exc:
        print(f"计划状态检查无法完成：{exc}", file=sys.stderr)
        return 2
    if issues:
        print("计划状态检查失败：", file=sys.stderr)
        for issue in issues:
            print(f"- {issue}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
