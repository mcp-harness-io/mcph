import pytest

from mcph.auth_probe import AuthInfo
from mcph.config import BearerAuthConfig, HttpServerConfig
from mcph.mcp_client import ServerError, _auth_message, _read_token, _redact


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


def test_without_auth_the_message_says_how_to_send_a_token():
    message = _auth_message(http_server(token_env=None), AuthInfo(challenge=None))

    assert message == (
        "authentication required: the server answered 401 "
        "(send a bearer token with `auth` in mcph.yaml)"
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
