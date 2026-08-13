from __future__ import annotations

from .agent_cli import agent_app
from .cli import app


app.add_typer(agent_app, name="agent")
