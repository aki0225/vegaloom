from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field



class AgentRecoveryRequest(BaseModel):
    """宿主对失联 Worker 或未知副作用的窄人工确认。"""

    model_config = ConfigDict(extra="forbid")

    reason: str
    workspace_explained: bool = True
    external_side_effects: Literal["none", "known", "unknown"] = "unknown"
    actor: str = "human"
    evidence_refs: list[str] = Field(default_factory=list)
