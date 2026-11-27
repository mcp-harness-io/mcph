"""Thin adapter over the FastMCP client.

mcph talks to MCP servers only through this module, so FastMCP's types and API
changes stay contained here.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import (
    ClientTransport,
    StdioTransport,
    StreamableHttpTransport,
)

from mcph.auth_probe import AuthInfo, probe_auth
from mcph.config import (
    BearerAuthConfig,
    HttpServerConfig,
    OAuthAuthConfig,
    ServerConfig,
    StdioServerConfig,
)
from mcph.token_store import StoredToken, TokenStore, TokenStoreError

DEFAULT_TIMEOUT_SECONDS = 30.0

_STDERR_TAIL_LINES = 10

# A bearer token is visible ASCII without spaces (RFC 6750 allows more, but not
# whitespace or control characters, which would also corrupt the header).
_TOKEN = re.compile(r"[\x21-\x7e]+")


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
        """What the server says about the auth it needs: set when it answered 401, and
        when an OAuth server has no usable login yet and was asked what it needs."""
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
    store: TokenStore | None = None,
) -> ServerSnapshot:
    """Connect to a running Streamable HTTP server, handshake and list its tools.

    mcph does not start or stop the server. If the server has ``auth``, a bearer
    token is sent with every request: read from the environment variable the
    config names, or, for ``type: oauth``, the token ``mcph auth login`` stored in
    ``store``. It never appears in an error. Raises :class:`ServerError` if the
    token is missing or expired, the server cannot be reached, does not answer
    within ``timeout`` seconds, or fails while being queried; the message names
    the URL. If the server refuses us (401, or 403 for a missing scope), the error
    says why and carries what the server advertises (:attr:`ServerError.auth`).
    """
    try:
        token = await _resolve_token(server, store or TokenStore())
    except ServerError as error:
        if isinstance(server.auth, OAuthAuthConfig):
            # Not logged in (or the login is unusable): ask the server what it needs,
            # so the report can say whether signing in could work at all.
            error.auth = await probe_auth(server.url, timeout=timeout)
        raise
    try:
        return await _snapshot(
            StreamableHttpTransport(url=server.url, auth=token),
            kind="http",
            connecting=f"connecting to {server.url}",
            timeout=timeout,
            secrets=(token,) if token else (),
        )
    except ServerError as error:
        if error.timed_out:
            raise
        auth = await probe_auth(server.url, timeout=timeout, token=token)
        if auth is None:
            raise
        raise ServerError(_auth_message(server, auth), auth=auth) from error


async def _resolve_token(server: HttpServerConfig, store: TokenStore) -> str | None:
    """The token to send to ``server``, or ``None`` if it has no ``auth``."""
    if isinstance(server.auth, OAuthAuthConfig):
        return await _read_stored_token(server, store)
    return _read_token(server)


async def _read_stored_token(server: HttpServerConfig, store: TokenStore) -> str:
    """The access token ``mcph auth login`` stored; it must exist and be unexpired."""
    login = f"run `mcph auth login {server.name}`"
    try:
        stored: StoredToken | None = await store.load(server)
    except TokenStoreError as exc:
        raise ServerError(f"{exc} ({login})") from exc
    if stored is None:
        raise ServerError(f"not logged in ({login})")
    if stored.expired():
        raise ServerError(f"the stored token {stored.describe()} ({login})")
    return stored.access_token


def _read_token(server: HttpServerConfig) -> str | None:
    """The bearer token from the server's ``auth.token_env``, or ``None`` if none."""
    if not isinstance(server.auth, BearerAuthConfig):
        return None
    name = server.auth.token_env
    value = os.environ.get(name)
    if value is None:
        raise ServerError(f"environment variable {name} is not set (auth.token_env)")
    token = value.strip()
    if not token:
        raise ServerError(f"environment variable {name} is empty (auth.token_env)")
    if not _TOKEN.fullmatch(token):
        raise ServerError(
            f"environment variable {name} does not hold a usable bearer token "
            "(spaces and control characters are not allowed)"
        )
    return token


def _auth_message(server: HttpServerConfig, auth: AuthInfo) -> str:
    """Say in one line why the server refused us; the details come with ``auth``."""
    if server.auth is None:
        return (
            f"authentication required: the server answered {auth.status} "
            "(add `auth` to this server in mcph.yaml)"
        )
    if isinstance(server.auth, OAuthAuthConfig):
        again = f"run `mcph auth login {server.name}` again"
        if auth.error == "insufficient_scope":
            return (
                "the stored token lacks a scope the server requires "
                f"(add it to `scopes`, then {again})"
            )
        return f"the server rejected the stored token (HTTP {auth.status}); {again}"
    variable = server.auth.token_env
    if auth.error == "insufficient_scope":
        return f"the token in ${variable} lacks a scope the server requires"
    return f"the server rejected the token in ${variable} (HTTP {auth.status})"


async def _snapshot(
    transport: ClientTransport,
    *,
    kind: str,
    connecting: str,
    timeout: float,
    stderr_path: Path | None = None,
    secrets: Sequence[str] = (),
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
        detail = _redact(str(exc) or type(exc).__name__, secrets)
        raise ServerError(
            f"failed while {phase}: {detail}", _stderr_tail(stderr_path)
        ) from exc

    return ServerSnapshot(
        server_name=info.name if info else None,
        server_version=info.version if info else None,
        protocol_version=str(protocol_version) if protocol_version else None,
        startup_seconds=startup_seconds,
        tools=tuple(ToolInfo(tool.name, tool.description) for tool in tools),
        transport=kind,
    )


def _redact(text: str, secrets: Sequence[str]) -> str:
    """Hide secrets in text that comes from a library and may echo what we sent."""
    for secret in secrets:
        text = text.replace(secret, "***")
    return text


def _stderr_tail(path: Path | None) -> str:
    # The log file only exists once the stdio transport got as far as opening it.
    if path is None or not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return "\n".join(text.rstrip().splitlines()[-_STDERR_TAIL_LINES:])
