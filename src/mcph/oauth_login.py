"""Sign in to an OAuth-protected HTTP server through the browser.

The flow itself (discovery, dynamic client registration, PKCE, the loopback
redirect) is FastMCP's OAuth client; this module drives it, keeps its output
quiet, and stores the resulting token in mcph's :class:`~mcph.token_store.TokenStore`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from fastmcp import Client
from fastmcp.client.auth import OAuth
from fastmcp.client.transports import StreamableHttpTransport
from mcp.client.auth.utils import issuers_match

from mcph.config import HttpServerConfig, OAuthAuthConfig
from mcph.token_store import StoredToken, TokenStore

DEFAULT_LOGIN_TIMEOUT_SECONDS = 300.0


class LoginError(Exception):
    """The sign-in did not complete."""


class _SameIssuer(str):
    """An issuer that also equals its spelling with or without a root trailing slash.

    The SDK checks that the issuer in an authorization server's metadata is the
    one the resource server announced, using ``!=``; this makes that comparison
    use the SDK's own :func:`issuers_match`, which treats ``https://host`` and
    ``https://host/`` as the same server and nothing else (another host, scheme,
    port or path still differs).
    """

    def __eq__(self, other: object) -> bool:
        return isinstance(other, str) and issuers_match(str(self), str(other))

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    __hash__ = str.__hash__


class _AnnouncingOAuth(OAuth):
    """FastMCP's browser flow, which also tells the user the authorization URL.

    With ``ignore_issuer_trailing_slash`` a root issuer announced as
    ``https://host/`` is accepted when its metadata says ``https://host`` (or the
    other way round).
    """

    def __init__(
        self,
        *args: object,
        announce: Callable[[str], None],
        ignore_issuer_trailing_slash: bool = False,
        **kwargs: object,
    ) -> None:
        self._announce = announce
        self._ignore_issuer_trailing_slash = ignore_issuer_trailing_slash
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def _expected_issuer(self) -> str:
        issuer = super()._expected_issuer()
        return _SameIssuer(issuer) if self._ignore_issuer_trailing_slash else issuer

    async def redirect_handler(self, authorization_url: str) -> None:
        self._announce(authorization_url)
        await super().redirect_handler(authorization_url)


async def login(
    server: HttpServerConfig,
    store: TokenStore,
    *,
    announce: Callable[[str], None] = lambda url: None,
    timeout: float = DEFAULT_LOGIN_TIMEOUT_SECONDS,
) -> StoredToken:
    """Sign in to ``server`` in the browser and store its token.

    ``announce`` is called with the authorization URL just before the browser is
    opened, so it can be shown in case the browser does not open. Any earlier
    login is discarded first, so this always starts a fresh sign-in. Raises
    :class:`LoginError` if the sign-in fails, is not finished within ``timeout``
    seconds, or if the server does not ask for authentication at all.
    """
    auth = server.auth
    if not isinstance(auth, OAuthAuthConfig):
        raise LoginError("the server does not use `auth: {type: oauth}`")

    store.delete(server)
    oauth = _AnnouncingOAuth(
        mcp_url=server.url,
        scopes=auth.scopes or None,
        token_storage=store.open(server),
        client_name="mcph",
        callback_timeout=timeout,
        announce=announce,
        ignore_issuer_trailing_slash=auth.ignore_issuer_trailing_slash,
    )
    try:
        with _quiet_logging():
            async with asyncio.timeout(timeout + 30):
                async with Client(StreamableHttpTransport(url=server.url, auth=oauth)):
                    pass
    except Exception as exc:
        store.delete(server)
        raise LoginError(
            _failure_message(
                exc, timeout, ignoring_slash=auth.ignore_issuer_trailing_slash
            )
        ) from exc

    token = await store.load(server)
    if token is None:
        store.delete(server)
        raise LoginError(
            "the server did not ask for authentication, "
            "so there is nothing to sign in to"
        )
    await store.drop_refresh_token(server)
    return token


def _failure_message(
    exc: BaseException, timeout: float, *, ignoring_slash: bool = False
) -> str:
    if _caused_by(exc, TimeoutError):
        return f"timed out after {timeout:g} s waiting for the sign-in to finish"
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]  # the first real error inside the group
    text = (str(exc) or type(exc).__name__).removeprefix("Client failed to connect: ")
    if "issuer mismatch" in text and not ignoring_slash:
        text += (
            " (if the two differ only by a trailing slash, set "
            "`ignore_issuer_trailing_slash: true` under `auth` to accept that)"
        )
    return text


def _caused_by(exc: BaseException, kind: type[BaseException]) -> bool:
    """Whether ``exc`` is, or was caused by, an error of this kind (even in a group)."""
    seen: set[int] = set()
    pending = [exc]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, kind):
            return True
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        pending.extend(e for e in (current.__cause__, current.__context__) if e)
    return False


@contextmanager
def _quiet_logging() -> Iterator[None]:
    """Keep the libraries' own logging out of the terminal.

    FastMCP logs the whole flow at INFO and the MCP SDK logs a full traceback for
    a failed sign-in; the CLI prints what the user needs.
    """
    quieted = {"fastmcp": logging.WARNING, "mcp.client.auth": logging.CRITICAL}
    previous = {name: logging.getLogger(name).level for name in quieted}
    for name, level in quieted.items():
        logging.getLogger(name).setLevel(level)
    try:
        yield
    finally:
        for name, level in previous.items():
            logging.getLogger(name).setLevel(level)
