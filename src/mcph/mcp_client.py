"""Thin adapter over the FastMCP client.

mcph talks to MCP servers only through this module, so FastMCP's types and API
changes stay contained here.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from mcph.config import ServerConfig

DEFAULT_TIMEOUT_SECONDS = 30.0

_STDERR_TAIL_LINES = 10


@dataclass(frozen=True)
class ToolInfo:
    """A tool exposed by an MCP server."""

    name: str
    description: str | None = None


@dataclass(frozen=True)
class ServerSnapshot:
    """What mcph learned by starting a server and talking MCP to it."""

    server_name: str | None
    server_version: str | None
    protocol_version: str | None
    startup_seconds: float
    tools: tuple[ToolInfo, ...]


class ServerError(Exception):
    """A server could not be started, did not answer, or failed to list tools."""

    def __init__(self, message: str, stderr_tail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.stderr_tail = stderr_tail


async def snapshot_stdio_server(
    server: ServerConfig,
    *,
    cwd: Path,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> ServerSnapshot:
    """Start a stdio server, complete the MCP handshake, list its tools, stop it.

    The server runs in ``cwd`` with a minimal inherited environment (HOME, PATH
    and a few other basics) plus the server's configured ``env``; the rest of
    the caller's environment is deliberately not passed on. Whatever happens,
    the server process is stopped before this returns.

    Raises :class:`ServerError` if the server cannot be started, does not answer
    within ``timeout`` seconds, or fails while being queried. The server's
    stderr output is attached to the error to explain why.
    """
    with tempfile.TemporaryDirectory(prefix="mcph-") as tmp:
        stderr_path = Path(tmp) / "stderr.log"
        transport = StdioTransport(
            command=server.command,
            args=server.args,
            env=server.env or None,
            cwd=str(cwd),
            keep_alive=False,  # stop the process when the client disconnects
            log_file=stderr_path,
        )
        phase = "starting the server"
        try:
            async with asyncio.timeout(timeout):
                started = time.perf_counter()
                async with Client(transport) as client:
                    startup_seconds = time.perf_counter() - started
                    info = client.server_info
                    protocol_version = client.protocol_version
                    phase = "listing tools"
                    tools = await client.list_tools()
        except TimeoutError:
            raise ServerError(
                f"timed out after {timeout:g} s while {phase}",
                _stderr_tail(stderr_path),
            ) from None
        except Exception as exc:
            raise ServerError(
                f"failed while {phase}: {exc or type(exc).__name__}",
                _stderr_tail(stderr_path),
            ) from exc

        return ServerSnapshot(
            server_name=info.name if info else None,
            server_version=info.version if info else None,
            protocol_version=str(protocol_version) if protocol_version else None,
            startup_seconds=startup_seconds,
            tools=tuple(ToolInfo(tool.name, tool.description) for tool in tools),
        )


def _stderr_tail(path: Path) -> str:
    # The log file only exists once the transport got as far as opening it.
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    return "\n".join(text.rstrip().splitlines()[-_STDERR_TAIL_LINES:])
