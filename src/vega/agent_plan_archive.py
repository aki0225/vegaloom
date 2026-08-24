from __future__ import annotations

import json
from pathlib import Path

from .agent_contract import AgentPlan
from .redaction import redact_value, write_redacted_json_once


def archive_agent_plan_revision(run_dir: Path, plan: AgentPlan) -> str:
    """在替换当前 Plan 前保留不可变 revision，供后续证据对账。"""

    relative = f"plans/plan-revision-{plan.plan_revision:03d}.json"
    path = run_dir / relative
    redacted_payload = redact_value(plan.model_dump(mode="json"))
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("历史 Plan revision 已存在但无法验证") from exc
        if existing != redacted_payload:
            raise ValueError("历史 Plan revision 身份冲突，拒绝覆盖")
        return relative
    write_redacted_json_once(path, redacted_payload)
    return relative
