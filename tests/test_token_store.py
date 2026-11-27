import asyncio
from pathlib import Path

import pytest
from fastmcp.client.auth.oauth import TokenStorageAdapter
from mcp.shared.auth import OAuthToken

from mcph.config import HttpServerConfig, OAuthAuthConfig
from mcph.token_store import (
    STATE_DIR_ENV,
    StoredToken,
    TokenStore,
    TokenStoreError,
    format_duration,
    state_dir,
)


def server(
    name="docs", url="https://example.com/mcp", scopes=("api.read",)
) -> HttpServerConfig:
    auth = OAuthAuthConfig(type="oauth", scopes=list(scopes))
    return HttpServerConfig(name=name, transport="http", url=url, auth=auth)


def store_token(store: TokenStore, target: HttpServerConfig, **fields) -> None:
    """Write a token the way FastMCP's OAuth client does after a login."""
    adapter = TokenStorageAdapter(store.open(target), target.url.rstrip("/"))
    fields.setdefault("access_token", "abc123")
    asyncio.run(adapter.set_tokens(OAuthToken(token_type="Bearer", **fields)))


def load(store: TokenStore, target: HttpServerConfig) -> StoredToken | None:
    return asyncio.run(store.load(target))


# --- where the files live ---------------------------------------------------------------


def test_the_state_directory_can_be_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv(STATE_DIR_ENV, str(tmp_path / "custom"))

    assert state_dir() == tmp_path / "custom"


def test_the_default_state_directory_belongs_to_mcph_and_the_user(monkeypatch):
    monkeypatch.delenv(STATE_DIR_ENV)

    path = state_dir()

    assert path.name == "mcph"
    assert Path.home() in path.parents


def test_each_server_gets_its_own_stable_directory(tmp_path):
    store = TokenStore(tmp_path)

    first = store.directory(server())

    assert first == store.directory(server())
    assert first.parent == tmp_path / "oauth"
    assert first.name.startswith("docs-")


@pytest.mark.parametrize(
    "other",
    [
        server(name="other"),
        server(url="https://example.com/other"),
        server(scopes=("api.read", "api.write")),
        server(scopes=()),
    ],
    ids=["name", "url", "scopes", "no scopes"],
)
def test_a_changed_server_entry_never_reuses_an_old_token(tmp_path, other):
    store = TokenStore(tmp_path)

    assert store.directory(other) != store.directory(server())


def test_the_order_of_the_scopes_does_not_matter(tmp_path):
    store = TokenStore(tmp_path)

    one = store.directory(server(scopes=("a", "b")))
    two = store.directory(server(scopes=("b", "a")))

    assert one == two


# --- reading and writing ----------------------------------------------------------------


def test_nothing_stored_means_not_logged_in_and_nothing_is_created(tmp_path):
    store = TokenStore(tmp_path / "state")

    assert load(store, server()) is None
    assert not (tmp_path / "state").exists()


def test_a_stored_token_is_read_back_with_its_expiry(tmp_path):
    store = TokenStore(tmp_path)
    target = server()

    store_token(store, target, access_token="the-token", expires_in=900, scope="api.read")
    token = load(store, target)

    assert token.access_token == "the-token"
    assert token.scope == "api.read"
    assert 890 < token.seconds_left() <= 900
    assert not token.expired()


def test_other_servers_do_not_see_the_token(tmp_path):
    store = TokenStore(tmp_path)
    store_token(store, server(name="one"))

    assert load(store, server(name="two")) is None


def test_only_the_user_can_reach_the_files(tmp_path):
    store = TokenStore(tmp_path)
    target = server()
    store_token(store, target)

    paths = [tmp_path / "oauth", store.directory(target)]
    paths += [p for p in store.directory(target).rglob("*") if p.is_file()]

    assert len(paths) > 3
    assert all(p.stat().st_mode & 0o077 == 0 for p in paths), "readable by others"
    assert (tmp_path / "oauth").stat().st_mode & 0o777 == 0o700


def test_an_unreadable_token_is_reported_not_crashed_on(tmp_path):
    store = TokenStore(tmp_path)
    target = server()
    store_token(store, target)
    [token_file] = [
        p for p in store.directory(target).rglob("*_tokens-*.json") if p.is_file()
    ]
    token_file.write_text("this is not json")

    with pytest.raises(TokenStoreError, match="cannot read the stored token"):
        load(store, target)


def test_the_refresh_token_can_be_dropped_and_the_access_token_stays(tmp_path):
    store = TokenStore(tmp_path)
    target = server()
    store_token(store, target, access_token="keep-me", refresh_token="drop-me", expires_in=900)
    adapter = TokenStorageAdapter(store.open(target), target.url.rstrip("/"))

    asyncio.run(store.drop_refresh_token(target))
    tokens = asyncio.run(adapter.get_tokens())

    assert tokens.refresh_token is None
    assert tokens.access_token == "keep-me"
    assert load(store, target).access_token == "keep-me"


def test_dropping_a_refresh_token_that_is_not_there_is_fine(tmp_path):
    store = TokenStore(tmp_path)
    target = server()
    store_token(store, target)

    asyncio.run(store.drop_refresh_token(target))

    assert load(store, target).access_token == "abc123"


def test_deleting_removes_the_token(tmp_path):
    store = TokenStore(tmp_path)
    target = server()
    store_token(store, target)

    assert store.delete(target) is True
    assert load(store, target) is None
    assert not store.directory(target).exists()


def test_deleting_what_is_not_there_says_so(tmp_path):
    assert TokenStore(tmp_path).delete(server()) is False


# --- describing a token -----------------------------------------------------------------


def test_a_token_never_shows_up_in_its_repr():
    assert "secret-value" not in repr(StoredToken("secret-value", expires_at=1.0))


@pytest.mark.parametrize(
    ("expires_at", "now", "expected"),
    [
        (1000 + 14 * 60, 1000, "expires in 14 min"),
        (1000 + 45, 1000, "expires in 45 s"),
        (1000 + 3 * 3600, 1000, "expires in 3 h"),
        (1000 - 3 * 60, 1000, "expired 3 min ago"),
        (1000 - 5, 1000, "expired 5 s ago"),
        (None, 1000, "has no known expiry"),
    ],
)
def test_describe(expires_at, now, expected):
    assert StoredToken("t", expires_at=expires_at).describe(now) == expected


def test_a_token_expires_exactly_when_its_time_is_up():
    token = StoredToken("t", expires_at=100.0)

    assert not token.expired(now=99.9)
    assert token.expired(now=100.0)
    assert not StoredToken("t").expired(now=1e12)  # no expiry known: never expired


def test_a_token_without_an_expiry_has_no_time_left():
    assert StoredToken("t").seconds_left() is None


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0 s"), (-5, "0 s"), (89, "89 s"), (90, "2 min"), (3599, "60 min"), (5400, "2 h")],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected
