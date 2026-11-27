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
from fastmcp.client.transports import (
    ClientTransport,
    StdioTransport,
    StreamableHttpTransport,
)

from mcph.auth_probe import AuthInfo, probe_auth
from mcph.config import HttpServerConfig, ServerConfig, StdioServerConfig

DEFAULT_TIMEOUT_SECONDS = 30.0

_STDERR_TAIL_LINES = 10


@dataclass(frozen=True)
class ToolInfo:
    """A tool exposed by an MCP server."""

    name: str
    description: str | None = None


@dataclass(frozen=True)
class ServerSnapshot:
    """What mcph learned by connecting to a server and talking MCP to it.

    ``startup_seconds`` is the time until the connection was ready: starting the
    process and the handshake for a stdio server, the handshake alone for an
    HTTP server (which is already running).
    """

    server_name: str | None
    server_version: str | None
    protocol_version: str | None
    startup_seconds: float
    tools: tuple[ToolInfo, ...]
    transport: str = "stdio"


class ServerError(Exception):
    """A server could not be reached, did not answer, or failed to list tools."""

    def __init__(
        self,
        message: str,
        stderr_tail: str = "",
        *,
        auth: AuthInfo | None = None,
        timed_out: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.stderr_tail = stderr_tail
        self.auth = auth
        """Set when the server answered 401: what it says about the auth it needs."""
        self.timed_out = timed_out


async def snapshot_server(
    server: ServerConfig,
    *,
    cwd: Path,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> ServerSnapshot:
    """Check a server of either transport; ``cwd`` is where stdio servers run."""
    if isinstance(server, HttpServerConfig):
        return await snapshot_http_server(server, timeout=timeout)
    return await snapshot_stdio_server(server, cwd=cwd, timeout=timeout)


async def snapshot_stdio_server(
    server: StdioServerConfig,
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
        return await _snapshot(
            transport,
            kind="stdio",
            connecting="starting the server",
            timeout=timeout,
            stderr_path=stderr_path,
        )


async def snapshot_http_server(
    server: HttpServerConfig,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> ServerSnapshot:
    """Connect to a running Streamable HTTP server, handshake and list its tools.

    mcph does not start or stop the server. Raises :class:`ServerError` if it
    cannot be reached, does not answer within ``timeout`` seconds, or fails
    while being queried; the message names the URL. If the server answers 401,
    the error says authentication is required and carries what the server
    advertises about it (:attr:`ServerError.auth`).
    """
    try:
        return await _snapshot(
            StreamableHttpTransport(url=server.url),
            kind="http",
            connecting=f"connecting to {server.url}",
            timeout=timeout,
        )
    except ServerError as error:
        if error.timed_out:
            raise
        auth = await probe_auth(server.url, timeout=timeout)
        if auth is None:
            raise
        raise ServerError(
            "authentication required: the server answered 401 "
            "(mcph cannot send credentials yet)",
            auth=auth,
        ) from error


async def _snapshot(
    transport: ClientTransport,
    *,
    kind: str,
    connecting: str,
    timeout: float,
    stderr_path: Path | None = None,
) -> ServerSnapshot:
    phase = connecting
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
            timed_out=True,
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
        transport=kind,
    )


def _stderr_tail(path: Path | None) -> str:
    # The log file only exists once the stdio transport got as far as opening it.
    if path is None or not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return "\n".join(text.rstrip().splitlines()[-_STDERR_TAIL_LINES:])
