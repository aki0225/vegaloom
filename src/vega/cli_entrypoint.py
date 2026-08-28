from __future__ import annotations

from .agent_cli import register_agent_commands
from .cli import app


register_agent_commands(app)
