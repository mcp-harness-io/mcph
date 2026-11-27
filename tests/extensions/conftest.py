import textwrap
from pathlib import Path

import pytest

from mcph.config import StdioServerConfig
from mcph.mcp_client import ServerSnapshot, ToolInfo
from mcph.rules import RuleContext

@pytest.fixture
def write_pack(tmp_path):
    """Write a pack directory and return it.

    ``rules`` maps the files of the pack to their source; the manifest lists them in
    that order. ``manifest`` replaces the whole manifest text.
    """

    def write(
        directory: str = "acme",
        *,
        name: str = "acme",
        version: str = "1.0.0",
        rules: dict[str, str] | None = None,
        manifest: str | None = None,
        under: Path | None = None,
    ) -> Path:
        pack = (under or tmp_path) / directory
        pack.mkdir(parents=True, exist_ok=True)
        files = rules or {}
        for file, source in files.items():
            target = pack / file
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(textwrap.dedent(source), encoding="utf-8")
        if manifest is None:
            listed = "".join(f"  - {file}\n" for file in files)
            manifest = f"name: {name}\nversion: {version!r}\n" + (
                f"rules:\n{listed}" if files else ""
            )
        (pack / "mcph-pack.yaml").write_text(manifest, encoding="utf-8")
        return pack

    return write


@pytest.fixture
def context():
    """A rule context for a stdio server that connected and has the tools a and delete_b."""

    def make(*tool_names: str) -> RuleContext:
        snapshot = ServerSnapshot(
            server_name="demo",
            server_version="1.0.0",
            protocol_version="2026-07-28",
            startup_seconds=0.1,
            tools=tuple(ToolInfo(name) for name in tool_names),
        )
        server = StdioServerConfig(name="local", transport="stdio", command="x")
        return RuleContext(server, 5.0, snapshot=snapshot)

    return make
