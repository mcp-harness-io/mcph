import pytest

from mcph.config import HttpServerConfig, StdioServerConfig
from mcph.mcp_client import ServerSnapshot, ToolInfo
from mcph.rules import RuleContext

TIMEOUT = 5.0


@pytest.fixture
def snapshot_with():
    """Make the snapshot of a server that has tools with the given names."""

    def make(*tool_names: str) -> ServerSnapshot:
        return ServerSnapshot(
            server_name="demo",
            server_version="1.0.0",
            protocol_version="2026-07-28",
            startup_seconds=0.1,
            tools=tuple(ToolInfo(name) for name in tool_names),
        )

    return make


@pytest.fixture
def stdio_server():
    return StdioServerConfig(name="local", transport="stdio", command="x")


@pytest.fixture
def http_server():
    return HttpServerConfig(name="docs", transport="http", url="http://x/mcp")


@pytest.fixture
def context_with(snapshot_with, stdio_server):
    """Make the context of a stdio server that connected and has tools with these names."""

    def make(*tool_names: str, **fields) -> RuleContext:
        fields.setdefault("server", stdio_server)
        fields.setdefault("snapshot", snapshot_with(*tool_names))
        return RuleContext(timeout=TIMEOUT, **fields)

    return make
