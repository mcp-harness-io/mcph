import pytest

from mcph.mcp_client import ServerSnapshot, ToolInfo


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
