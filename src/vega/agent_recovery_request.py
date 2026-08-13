from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict



class AgentRecoveryRequest(BaseModel):
    """宿主对失联 Worker 的窄观察；进程和 Workspace 事实由 Vega 重新采集。"""

    model_config = ConfigDict(extra="forbid")

    reason: str
    workspace_explained: bool = True
    external_side_effects: Literal["none", "known", "unknown"] = "unknown"
