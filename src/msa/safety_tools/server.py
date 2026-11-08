"""MCP server exposing the deterministic safety tools.

    python -m msa.cli mcp        (stdio transport)

Agents inside this app call the same functions in-process (msa.safety_tools.tools), so the
logic exists once. The MCP server lets any MCP client (Claude Desktop, an IDE, another
agent) use exactly the same checks.
"""

from __future__ import annotations

from typing import Any

from msa.core.db import init_db, session_scope
from msa.safety_tools import tools

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore


def _dump(v) -> dict[str, Any]:
    return v.model_dump()


def build_server():
    server = _Server("msa-safety-tools")

    @server.tool()
    def check_material(equipment_id: str, material: str) -> dict:
        """Is this material allowed on this machine? allowed | banned | ask_staff | unknown."""
        with session_scope() as db:
            return _dump(tools.check_material(db, equipment_id, material))

    @server.tool()
    def required_ppe(equipment_id: str) -> dict:
        """PPE required for a machine, from house rules and the approved SOP (with citations)."""
        with session_scope() as db:
            return _dump(tools.required_ppe(db, equipment_id))

    @server.tool()
    def can_use(user_id: str, equipment_id: str) -> dict:
        """May this member use this machine now? yes | no, with reasons."""
        with session_scope() as db:
            return _dump(tools.can_use(db, user_id, equipment_id))

    @server.tool()
    def check_layout(plan_id: str) -> dict:
        """Check a floor plan for clearance, exit-path and safety-equipment problems."""
        with session_scope() as db:
            return _dump(tools.check_layout(db, plan_id))

    @server.tool()
    def check_sop_rules(category: str, statements: list[dict]) -> dict:
        """Structure checks for an SOP draft: sections, PPE terms, banned materials."""
        return _dump(tools.check_sop_rules(category, statements))

    @server.tool()
    def risk_score(likelihood: int, severity: int) -> dict:
        """5x5 risk matrix."""
        return _dump(tools.risk_score(likelihood, severity))

    return server


TOOL_NAMES = ["check_material", "required_ppe", "can_use", "check_layout", "check_sop_rules",
              "risk_score"]


def main() -> None:
    init_db()
    build_server().run()
