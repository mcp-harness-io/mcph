import asyncio

import pytest
from fastmcp.client.auth.oauth import TokenStorageAdapter
from mcp.shared.auth import OAuthToken

from mcph.auth_probe import AuthInfo
from mcph.config import BearerAuthConfig, HttpServerConfig, OAuthAuthConfig
from mcph.mcp_client import (
    ServerError,
    _auth_message,
    _read_token,
    _redact,
    _resolve_token,
)
from mcph.token_store import TokenStore


def http_server(token_env: str | None = "DOCS_TOKEN") -> HttpServerConfig:
    auth = BearerAuthConfig(type="bearer", token_env=token_env) if token_env else None
    return HttpServerConfig(name="docs", transport="http", url="http://x/mcp", auth=auth)


# --- reading the token -------------------------------------------------------------------


def test_a_server_without_auth_has_no_token(monkeypatch):
    monkeypatch.setenv("DOCS_TOKEN", "ignored")

    assert _read_token(http_server(token_env=None)) is None


def test_the_token_comes_from_the_named_environment_variable(monkeypatch):
    monkeypatch.setenv("DOCS_TOKEN", "abc.def-123")

    assert _read_token(http_server()) == "abc.def-123"


def test_whitespace_around_the_token_is_dropped(monkeypatch):
    monkeypatch.setenv("DOCS_TOKEN", "  abc123\n")

    assert _read_token(http_server()) == "abc123"


def test_a_missing_variable_is_reported_by_name(monkeypatch):
    monkeypatch.delenv("DOCS_TOKEN", raising=False)

    with pytest.raises(ServerError, match="environment variable DOCS_TOKEN is not set"):
        _read_token(http_server())


@pytest.mark.parametrize("value", ["", "   ", "\n"])
def test_an_empty_variable_is_reported(monkeypatch, value):
    monkeypatch.setenv("DOCS_TOKEN", value)

    with pytest.raises(ServerError, match="environment variable DOCS_TOKEN is empty"):
        _read_token(http_server())


@pytest.mark.parametrize("value", ["two words", "line1\nline2", "tab\tsep", "caf\u00e9"])
def test_a_token_that_cannot_go_into_a_header_is_rejected_without_echoing_it(
    monkeypatch, value
):
    monkeypatch.setenv("DOCS_TOKEN", value)

    with pytest.raises(ServerError) as excinfo:
        _read_token(http_server())

    assert "does not hold a usable bearer token" in str(excinfo.value)
    assert value not in str(excinfo.value)


# --- explaining a refusal ------------------------------------------------------------------


def test_without_auth_the_message_says_to_add_auth():
    message = _auth_message(http_server(token_env=None), AuthInfo(challenge=None))

    assert message == (
        "authentication required: the server answered 401 "
        "(add `auth` to this server in mcph.yaml)"
    )


def test_a_refused_token_names_the_variable_not_the_value():
    message = _auth_message(http_server(), AuthInfo(challenge=None, error="invalid_token"))

    assert message == "the server rejected the token in $DOCS_TOKEN (HTTP 401)"


def test_a_missing_scope_has_its_own_message():
    auth = AuthInfo(challenge=None, status=403, error="insufficient_scope")

    assert _auth_message(http_server(), auth) == (
        "the token in $DOCS_TOKEN lacks a scope the server requires"
    )


# --- redaction -------------------------------------------------------------------------------


def test_secrets_are_hidden_in_library_error_text():
    text = "Illegal header value b'Bearer s3cr3t' while using s3cr3t"

    assert _redact(text, ["s3cr3t"]) == "Illegal header value b'Bearer ***' while using ***"


def test_text_without_secrets_is_left_alone():
    assert _redact("Not Found", ["s3cr3t"]) == "Not Found"
    assert _redact("Not Found", []) == "Not Found"


# --- OAuth: the token that `mcph auth login` stored ---------------------------------------


def oauth_server() -> HttpServerConfig:
    return HttpServerConfig(
        name="docs",
        transport="http",
        url="http://x/mcp",
        auth=OAuthAuthConfig(type="oauth"),
    )


def store_token(store: TokenStore, **fields) -> None:
    server = oauth_server()
    adapter = TokenStorageAdapter(store.open(server), server.url)
    fields.setdefault("access_token", "stored-token")
    asyncio.run(adapter.set_tokens(OAuthToken(token_type="Bearer", **fields)))


def resolve(server: HttpServerConfig, store: TokenStore) -> str | None:
    return asyncio.run(_resolve_token(server, store))


def test_the_stored_token_is_used(tmp_path):
    store = TokenStore(tmp_path)
    store_token(store, expires_in=900)

    assert resolve(oauth_server(), store) == "stored-token"


def test_a_token_without_a_known_expiry_is_used(tmp_path):
    store = TokenStore(tmp_path)
    store_token(store)  # no expires_in

    assert resolve(oauth_server(), store) == "stored-token"


def test_without_a_login_the_message_says_which_command_to_run(tmp_path):
    with pytest.raises(ServerError) as excinfo:
        resolve(oauth_server(), TokenStore(tmp_path))

    assert excinfo.value.message == "not logged in (run `mcph auth login docs`)"


def test_an_expired_token_says_how_long_ago_and_what_to_do(tmp_path):
    store = TokenStore(tmp_path)
    store_token(store, expires_in=1)
    asyncio.run(asyncio.sleep(1.2))

    with pytest.raises(ServerError) as excinfo:
        resolve(oauth_server(), store)

    message = excinfo.value.message
    assert message.startswith("the stored token expired ")
    assert message.endswith(" ago (run `mcph auth login docs`)")
    assert "stored-token" not in message


def test_an_unreadable_token_says_to_log_in_again(tmp_path):
    store = TokenStore(tmp_path)
    store_token(store, expires_in=900)
    [token_file] = [
        p for p in store.directory(oauth_server()).rglob("*_tokens-*.json") if p.is_file()
    ]
    token_file.write_text("garbage")

    with pytest.raises(ServerError, match=r"cannot read the stored token .*\(run `mcph auth login docs`\)"):
        resolve(oauth_server(), store)


def test_servers_without_oauth_do_not_touch_the_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_TOKEN", "from-env")

    assert resolve(http_server(), TokenStore(tmp_path)) == "from-env"
    assert resolve(http_server(token_env=None), TokenStore(tmp_path)) is None
    assert not (tmp_path / "oauth").exists()


def test_a_rejected_stored_token_says_to_log_in_again():
    message = _auth_message(oauth_server(), AuthInfo(challenge=None, error="invalid_token"))

    assert message == (
        "the server rejected the stored token (HTTP 401); "
        "run `mcph auth login docs` again"
    )


def test_a_stored_token_without_the_needed_scope_says_to_add_it():
    auth = AuthInfo(challenge=None, status=403, error="insufficient_scope")

    assert _auth_message(oauth_server(), auth) == (
        "the stored token lacks a scope the server requires "
        "(add it to `scopes`, then run `mcph auth login docs` again)"
    )

