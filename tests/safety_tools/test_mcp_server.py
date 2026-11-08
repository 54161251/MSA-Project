import asyncio

from msa.safety_tools.server import TOOL_NAMES, build_server


def test_mcp_server_lists_every_tool():
    server = build_server()
    listed = asyncio.run(server.list_tools())
    names = {t.name for t in listed}
    assert set(TOOL_NAMES) <= names
