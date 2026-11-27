"""Where mcph keeps the OAuth tokens of the servers it signed in to.

Every configured server gets its own directory under mcph's state directory,
named after the server and a hash of its URL and scopes, so a changed URL or
scope list never reuses an old token. The directories are readable only by the
user. The files are plain JSON: the tokens are short-lived access tokens, and
mcph does not keep refresh tokens because it cannot use them yet.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import platformdirs
from fastmcp.client.auth.oauth import TokenStorageAdapter
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1CollectionSanitizationStrategy,
    FileTreeV1KeySanitizationStrategy,
)

from mcph.config import HttpServerConfig, OAuthAuthConfig

STATE_DIR_ENV = "MCPH_STATE_DIR"


def state_dir() -> Path:
    """mcph's per-user state directory; ``MCPH_STATE_DIR`` overrides it."""
    override = os.environ.get(STATE_DIR_ENV)
    if override:
        return Path(override)
    return Path(platformdirs.user_state_dir("mcph", appauthor=False))


class TokenStoreError(Exception):
    """The stored token could not be read."""


@dataclass(frozen=True)
class StoredToken:
    """The access token of a server and when it expires."""

    access_token: str = field(repr=False)  # never show it, not even by accident
    expires_at: float | None = None
    scope: str | None = None

    def seconds_left(self, now: float | None = None) -> float | None:
        """Seconds until the token expires (negative once it has), if known."""
        if self.expires_at is None:
            return None
        return self.expires_at - (time.time() if now is None else now)

    def expired(self, now: float | None = None) -> bool:
        left = self.seconds_left(now)
        return left is not None and left <= 0

    def describe(self, now: float | None = None) -> str:
        """A short phrase such as ``expires in 14 min`` or ``expired 3 min ago``."""
        left = self.seconds_left(now)
        if left is None:
            return "has no known expiry"
        if left <= 0:
            return f"expired {format_duration(-left)} ago"
        return f"expires in {format_duration(left)}"


def format_duration(seconds: float) -> str:
    """``45 s``, ``14 min`` or ``3 h``: the largest sensible unit, rounded."""
    seconds = max(0, round(seconds))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} min"
    return f"{round(seconds / 3600)} h"


class TokenStore:
    """The tokens of all servers, one directory per configured server entry."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = (root if root is not None else state_dir()) / "oauth"

    def directory(self, server: HttpServerConfig) -> Path:
        """Where this server's token lives (it may not exist yet)."""
        scopes = server.auth.scopes if isinstance(server.auth, OAuthAuthConfig) else []
        identity = f"{server.url}\n{' '.join(sorted(scopes))}"
        digest = hashlib.sha256(identity.encode()).hexdigest()[:12]
        return self._root / f"{server.name}-{digest}"

    def open(self, server: HttpServerConfig) -> FileTreeStore:
        """The key-value store FastMCP's OAuth client writes the token into.

        Creates the directories, readable only by the user.
        """
        directory = self.directory(server)
        for path in (self._root, directory):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.chmod(0o700)
        return FileTreeStore(
            data_directory=directory,
            key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(directory),
            collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(
                directory
            ),
        )

    async def load(self, server: HttpServerConfig) -> StoredToken | None:
        """The stored token, or ``None`` if the server was never logged in to.

        Raises :class:`TokenStoreError` if the files cannot be read.
        """
        directory = self.directory(server)
        if not directory.is_dir():
            return None
        try:
            adapter = self._adapter(server, self.open(server))
            tokens = await adapter.get_tokens()
            expires_at = await adapter.get_token_expiry()
        except Exception as exc:
            raise TokenStoreError(
                f"cannot read the stored token in {directory}: {exc}"
            ) from exc
        if tokens is None:
            return None
        return StoredToken(tokens.access_token, expires_at, tokens.scope)

    async def drop_refresh_token(self, server: HttpServerConfig) -> None:
        """Forget the refresh token, if the authorization server issued one."""
        adapter = self._adapter(server, self.open(server))
        tokens = await adapter.get_tokens()
        if tokens is not None and tokens.refresh_token:
            await adapter.set_tokens(tokens.model_copy(update={"refresh_token": None}))

    def delete(self, server: HttpServerConfig) -> bool:
        """Remove the stored token; ``False`` if there was none."""
        directory = self.directory(server)
        if not directory.exists():
            return False
        shutil.rmtree(directory)
        return True

    @staticmethod
    def _adapter(server: HttpServerConfig, store: FileTreeStore) -> TokenStorageAdapter:
        # FastMCP keys the token by the MCP URL without a trailing slash.
        return TokenStorageAdapter(store, server.url.rstrip("/"))
